"""Database-free regression suite: python -B tests/test_embedding_recovery.py."""

import copy
import importlib
import io
import os
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


def vector(value=1.0):
    return [value] + [0.0] * 511


def chunk(chunk_id, embedding=None, document_id=1, content=None):
    return dict(id=chunk_id, document_id=document_id, content=content if content is not None else f"Synthetic chunk {chunk_id}",
                embedding=embedding, metadata={"page": 4, "asset_filename": "synthetic.png"})


class MemoryStore:
    """Transactional state double; separate tests check real PostgreSQL statements."""

    def __init__(self, recovery, completeness, chunks=(), documents=None):
        self.recovery = recovery
        self.completeness = completeness
        self.chunks = {item["id"]: copy.deepcopy(item) for item in chunks}
        self.documents = documents or {1: "ready"}
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = None

    def document_ids(self, requested, after, limit):
        return [i for i in sorted(self.documents) if i > after and (requested is None or i in requested)][:limit]

    @staticmethod
    def usable(item):
        result = item["embedding"]
        return result is not None and len(result) == 512 and any(result)

    def inspect(self, ids):
        result = []
        for document_id in ids:
            required = [c for c in self.chunks.values() if c["document_id"] == document_id and c["content"].strip()]
            valid = [c for c in required if self.usable(c)]
            result.append(self.completeness.EmbeddingCompleteness(document_id, self.documents[document_id], len(required), len(valid)))
        return result

    def select_chunks(self, ids, limit, after_chunk_id=0):
        result = []
        for item in sorted(self.chunks.values(), key=lambda item: item["id"]):
            if (item["document_id"] in ids and self.documents[item["document_id"]] in self.recovery.RECOVERABLE_STATUSES
                    and item["id"] > after_chunk_id and item["content"].strip() and not self.usable(item)):
                metadata = item["metadata"]
                kind = None if metadata is None else "object" if isinstance(metadata, dict) else "array"
                result.append(self.recovery.RecoveryChunk(item["id"], item["document_id"], item["content"], kind))
        return result[:limit]

    def persist_batch(self, chunks, vectors):
        updated = copy.deepcopy(self.chunks)
        written = 0
        for selected, result in zip(chunks, vectors):
            item = updated.get(selected.id)
            if (item and not self.usable(item) and item["content"] == selected.content
                    and self.documents[item["document_id"]] in self.recovery.RECOVERABLE_STATUSES):
                item["embedding"] = result
                item["metadata"] = self.recovery.with_embedding_generation(item["metadata"])
                written += 1
        if self.fail_commit == self.commits + 1:
            raise RuntimeError("synthetic private database error")
        self.chunks = updated
        self.commits += 1
        return written

    def rollback(self):
        self.rollbacks += 1


class EmbeddingRecoveryTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        stack = cls.stack = ExitStack()
        cls.addClassCleanup(stack.close)
        backend = str(Path(__file__).resolve().parents[1])
        stack.enter_context(patch.object(sys, "path", [backend, *sys.path]))
        stack.enter_context(patch.dict(sys.modules))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        preserved = {key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
        stack.enter_context(patch.dict(os.environ, {**preserved, "VOYAGE_API_KEY": "synthetic-key"}, clear=True))
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        stack.enter_context(patch("sqlalchemy.create_engine", side_effect=AssertionError("No database engines in this suite")))
        stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("No network in this suite")))
        stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("No provider calls in this suite")))

        from sqlalchemy.orm import DeclarativeBase
        database = ModuleType("app.database.database")
        class Base(DeclarativeBase):
            pass
        database.Base = Base
        database.SessionLocal = MagicMock(side_effect=AssertionError("No real database sessions"))
        database.engine = MagicMock()
        sys.modules[database.__name__] = database
        cls.database = database
        cls.contract = importlib.import_module("app.services.embedding_contract")
        cls.completeness = importlib.import_module("app.services.embedding_completeness_service")
        cls.recovery = importlib.import_module("app.services.embedding_recovery_service")
        cls.provider = importlib.import_module("app.services.embedding_service")
        cls.search = importlib.import_module("app.services.search_service")
        cls.cli = importlib.import_module("scripts.recover_embeddings")

        # Isolate the real processing entry points from extraction/upload/queue
        # imports. Their embedding and ready-state logic is exercised below.
        for module_name, functions in {
            "app.services.file_service": ["extract_content"],
            "app.services.queued_message_service": ["process_waiting_messages_for_document"],
            "app.services.assets.asset_extraction_service": ["replace_document_assets", "ensure_document_assets"],
        }.items():
            module = ModuleType(module_name)
            for name in functions:
                setattr(module, name, MagicMock())
            sys.modules[module_name] = module
        cls.processing = importlib.import_module("app.services.document_processing_service")
        cls.parser_service = importlib.import_module("app.services.document_parser_service")

    def store(self, chunks=(), documents=None):
        return MemoryStore(self.recovery, self.completeness, chunks, documents)

    def recover(self, store, **kwargs):
        kwargs.setdefault("execute", True)
        kwargs.setdefault("embed", lambda texts: [vector() for _ in texts])
        return self.recovery.recover_embeddings(store, **kwargs)

    def test_four_completeness_states_and_empty_is_not_complete(self):
        for required, valid, state in [(2, 2, "fully_embedded"), (2, 1, "partially_embedded"),
                                       (2, 0, "no_valid_embeddings"), (0, 0, "no_embeddable_chunks")]:
            with self.subTest(state=state):
                result = self.completeness.EmbeddingCompleteness(1, "ready", required, valid)
                self.assertEqual(result.state, state)
                self.assertEqual(result.complete, state == "fully_embedded")
                self.assertEqual(result.report()["semantic_ready"], result.complete)

    def test_partial_recovery_preserves_valid_vectors_ids_and_source_metadata(self):
        store = self.store([chunk(1, vector(2)), chunk(2)])
        existing = copy.deepcopy(store.chunks[1])
        provider = MagicMock(return_value=[vector(3)])
        report = self.recover(store, embed=provider)
        provider.assert_called_once_with(["Synthetic chunk 2"])
        self.assertEqual(store.chunks[1], existing)
        self.assertEqual(set(store.chunks), {1, 2})
        self.assertEqual(store.chunks[2]["metadata"]["asset_filename"], "synthetic.png")
        self.assertEqual(store.chunks[2]["metadata"]["embedding_generation"]["model"], "voyage-4-lite")
        self.assertEqual(report["written_chunks"], 1)
        self.assertTrue(report["documents"][0]["semantic_ready"])

    def test_complete_rerun_has_zero_provider_calls_and_zero_commits(self):
        store = self.store([chunk(1, vector())])
        provider = MagicMock()
        report = self.recover(store, embed=provider)
        provider.assert_not_called()
        self.assertEqual(store.commits, 0)
        self.assertEqual(report["attempted_chunks"], 0)

    def test_provider_failure_keeps_completed_batches_and_resume_only_remaining(self):
        store = self.store([chunk(1, vector(2)), chunk(2), chunk(3), chunk(4)])
        provider = MagicMock(side_effect=[[vector(3)], RuntimeError("secret-token synthetic-content")])
        events = []
        limits = self.recovery.RecoveryLimits(batch_size=1)
        first = self.recover(store, embed=provider, limits=limits, emit=events.append)
        self.assertTrue(first["failed"])
        self.assertEqual(first["attempted_chunks"], 2)
        self.assertEqual(first["written_chunks"], 1)
        self.assertEqual(store.commits, 1)
        self.assertEqual(store.rollbacks, 1)
        self.assertEqual(store.chunks[1]["embedding"], vector(2))
        self.assertEqual(store.chunks[2]["embedding"], vector(3))
        self.assertIsNone(store.chunks[3]["embedding"])
        self.assertFalse(first["documents"][0]["semantic_ready"])
        self.assertNotIn("secret-token", str(events))
        self.assertNotIn("Synthetic chunk", str(events))
        resumed_provider = MagicMock(return_value=[vector(), vector()])
        second = self.recover(store, embed=resumed_provider)
        resumed_provider.assert_called_once_with(["Synthetic chunk 3", "Synthetic chunk 4"])
        self.assertEqual(second["remaining_missing_chunks"], 0)

    def test_database_failure_rolls_back_current_batch_only(self):
        store = self.store([chunk(1), chunk(2), chunk(3)])
        store.fail_commit = 2
        report = self.recover(store, limits=self.recovery.RecoveryLimits(batch_size=1))
        self.assertTrue(report["failed"])
        self.assertEqual(report["written_chunks"], 1)
        self.assertEqual(store.commits, 1)
        self.assertIsNotNone(store.chunks[1]["embedding"])
        self.assertIsNone(store.chunks[2]["embedding"])

    def test_bad_count_or_dimension_rejects_entire_batch_without_writes(self):
        for results in ([vector()], [vector(), [1.0] * 384], [vector(), None]):
            with self.subTest(results=len(results)):
                store = self.store([chunk(1, vector(3)), chunk(2), chunk(3)])
                before = copy.deepcopy(store.chunks)
                report = self.recover(store, embed=lambda texts: results)
                self.assertTrue(report["failed"])
                self.assertEqual(store.chunks, before)
                self.assertEqual(store.commits, 0)

    def test_dry_run_is_default_and_uses_no_key_provider_or_writes(self):
        store = self.store([chunk(1), chunk(2, vector())])
        before = copy.deepcopy(store.chunks)
        provider = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            report = self.recovery.recover_embeddings(store, embed=provider)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["planned_chunks"], 1)
        self.assertGreater(report["planned_input_characters"], 0)
        provider.assert_not_called()
        self.assertEqual(store.commits, 0)
        self.assertEqual(store.chunks, before)

    def test_document_chunk_and_batch_limits_and_cursor_are_respected(self):
        store = self.store([chunk(i, document_id=1 if i < 6 else 2) for i in range(1, 9)], {1: "ready", 2: "ready"})
        batches = []
        report = self.recover(store, limits=self.recovery.RecoveryLimits(1, 3, 2),
                              embed=lambda texts: batches.append(texts) or [vector() for _ in texts])
        self.assertEqual([len(batch) for batch in batches], [2, 1])
        self.assertEqual(report["written_chunks"], 3)
        self.assertEqual(len(report["documents"]), 1)
        self.assertIsNone(store.chunks[6]["embedding"])
        second = self.recover(store, after_document_id=1, limits=self.recovery.RecoveryLimits(1, 1, 1))
        self.assertEqual(second["documents"][0]["document_id"], 2)
        self.assertEqual(second["written_chunks"], 1)

    def test_resume_after_bounded_success_only_embeds_remaining_rows(self):
        store = self.store([chunk(1), chunk(2)])
        self.recover(store, limits=self.recovery.RecoveryLimits(max_chunks=1))
        provider = MagicMock(return_value=[vector()])
        self.recover(store, embed=provider)
        provider.assert_called_once_with(["Synthetic chunk 2"])

    def test_empty_and_blank_chunks_are_explicit_and_never_sent(self):
        for chunks in ([], [chunk(1, content=" \t\r\n ")]):
            store = self.store(chunks)
            provider = MagicMock()
            report = self.recover(store, embed=provider)
            self.assertEqual(report["documents"][0]["embedding_state"], "no_embeddable_chunks")
            self.assertFalse(report["documents"][0]["semantic_ready"])
            provider.assert_not_called()

    def test_processing_documents_skipped_and_failed_never_promoted(self):
        store = self.store([chunk(1), chunk(2, document_id=2)], {1: "processing", 2: "failed"})
        report = self.recover(store)
        self.assertEqual(report["skipped_document_ids"], [1])
        self.assertIsNone(store.chunks[1]["embedding"])
        self.assertEqual(store.documents, {1: "processing", 2: "failed"})
        self.assertEqual(report["documents"][1]["embedding_state"], "fully_embedded")
        self.assertFalse(report["documents"][1]["semantic_ready"])

    def test_zero_and_wrong_dimension_stored_vectors_are_recoverable(self):
        store = self.store([chunk(1, [0.0] * 512), chunk(2, [1.0] * 384)])
        self.assertEqual(self.recover(store)["written_chunks"], 2)

    def test_valid_legacy_or_different_model_vectors_are_preserved(self):
        store = self.store([chunk(1, vector()), chunk(2, vector())])
        store.chunks[2]["metadata"]["embedding_generation"] = {"model": "synthetic-other-model"}
        before = copy.deepcopy(store.chunks)
        provider = MagicMock()
        self.recover(store, embed=provider)
        provider.assert_not_called()
        self.assertEqual(store.chunks, before)

    def test_invalid_metadata_aborts_before_spending_or_writing(self):
        store = self.store([chunk(1)])
        store.chunks[1]["metadata"] = ["preserve-me"]
        provider = MagicMock()
        report = self.recover(store, embed=provider)
        self.assertTrue(report["failed"])
        provider.assert_not_called()
        self.assertEqual(store.chunks[1]["metadata"], ["preserve-me"])

    def test_concurrent_valid_vector_is_preserved_and_reported_as_conflict(self):
        store = self.store([chunk(1)])
        def embed(texts):
            store.chunks[1]["embedding"] = vector(7)
            return [vector(8)]
        report = self.recover(store, embed=embed)
        self.assertEqual(store.chunks[1]["embedding"], vector(7))
        self.assertEqual(report["conflicted_chunks"], 1)

    def test_indices_restore_correspondence_and_request_keeps_model_dimension(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"data": [{"index": 1, "embedding": vector(2)}, {"index": 0, "embedding": vector(1)}]}
        with patch.object(self.provider.requests, "post", return_value=response) as post:
            results = self.provider.create_passage_embeddings([" first ", " second "], batch_size=2)
        self.assertEqual(results, [vector(1), vector(2)])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["input"], ["first", "second"])
        self.assertEqual((payload["model"], payload["output_dimension"], payload["input_type"]), ("voyage-4-lite", 512, "document"))

    def test_duplicate_missing_and_out_of_range_indices_are_rejected(self):
        for indices in ([0, 0], [0, 2], [0, -1], [0, True], [0, None]):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                self.contract.validate_provider_response({"data": [{"index": i, "embedding": vector()} for i in indices]}, 2)

    def test_provider_count_dimension_and_nonnumeric_results_are_rejected(self):
        for payload in ({}, {"data": []}, {"data": [{"index": 0, "embedding": [1] * 384}]},
                        {"data": [{"index": 0, "embedding": None}]}):
            with self.subTest(payload=str(payload)[:30]), self.assertRaises(ValueError):
                self.contract.validate_provider_response(payload, 1)
        for value in (float("nan"), float("inf"), 1e100, "1.0", True, 0.0, 1e-100):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.contract.validate_embeddings([vector(value)], 1)

    def test_invalid_limits_fail_before_any_io(self):
        for kwargs in ({"max_documents": 0}, {"max_chunks": -1}, {"batch_size": 129}, {"max_chunks": 10001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.recovery.RecoveryLimits(**kwargs)

    def test_real_completeness_adapter_uses_aggregate_counts_not_vectors(self):
        db = MagicMock()
        db.execute.return_value.mappings.return_value = [dict(document_id=1, processing_status="ready", required_chunks=2,
                                                           valid_chunks=1, unverified_generation_chunks=1)]
        report = self.completeness.inspect_embeddings(db, [1], ["image"])[0]
        self.assertEqual(report.state, "partially_embedded")
        statement = db.execute.call_args.args[0]
        self.assertEqual(list(statement.selected_columns.keys()), ["document_id", "processing_status", "required_chunks",
                                                                 "valid_chunks", "unverified_generation_chunks"])
        from sqlalchemy.dialects import postgresql
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.assertIn("count(document_chunks.id) FILTER", sql)
        self.assertIn("LEFT OUTER JOIN", sql)
        self.assertIn("vector_dims", sql)
        self.assertIn("vector_norm", sql)
        self.assertIn(["image"], compiled.params.values())
        self.assertEqual(report.unverified_generation_chunks, 1)

    def test_real_selection_sql_limits_missing_nonblank_rows_and_status(self):
        from sqlalchemy.dialects import postgresql
        statement = self.recovery.recovery_chunk_statement([1], 3, 9)
        self.assertNotIn("embedding", statement.selected_columns.keys())
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        for required in ("NOT coalesce", "embedding IS NOT NULL", "vector_dims", "vector_norm", "content ~", "ORDER BY", "LIMIT"):
            self.assertIn(required, sql)
        self.assertIn(3, compiled.params.values())
        self.assertIn(9, compiled.params.values())
        self.assertIn(["ready", "failed"], compiled.params.values())

    def test_real_update_sql_rechecks_content_validity_status_and_merges_metadata(self):
        from sqlalchemy.dialects import postgresql
        selected = self.recovery.RecoveryChunk(1, 2, "Synthetic unchanged content", "object")
        statement = self.recovery.recovery_update_statement(selected, vector())
        sql = str(statement.compile(dialect=postgresql.dialect()))
        for required in ("UPDATE document_chunks SET", "document_chunks.content =", "NOT coalesce", "EXISTS", "jsonb_typeof", " || "):
            self.assertIn(required, sql)
        self.assertNotIn("DELETE", sql)
        self.assertNotIn("processing_status=", sql)
        db = MagicMock()
        db.execute.return_value.rowcount = 1
        store = self.recovery.PostgresRecoveryStore(db)
        self.assertEqual(store.persist_batch([selected, selected], [vector(), vector()]), 2)
        self.assertEqual(db.execute.call_count, 2)
        db.commit.assert_called_once()

    def test_search_all_null_returns_empty_without_query_provider_call(self):
        db = MagicMock()
        incomplete = self.completeness.EmbeddingCompleteness(1, "ready", 2, 0)
        with patch.object(self.search, "inspect_embeddings", return_value=[incomplete]), \
                patch.object(self.search, "create_query_embedding") as embed, self.assertLogs(self.search.logger, "WARNING") as logs:
            self.assertEqual(self.search.search_similar_chunks(db, "synthetic question", [1]), [])
        embed.assert_not_called()
        db.query.assert_not_called()
        self.assertIn("embeddings incomplete", str(logs.output))

    def test_search_partial_vectors_preserves_null_filter_and_valid_results(self):
        db = MagicMock()
        query = db.query.return_value
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        valid_chunk = SimpleNamespace(id=1, document_id=1, content="Synthetic relevant passage", content_type="text",
                                     location="page 4", chunk_metadata={"page": 4})
        query.all.return_value = [(valid_chunk, 0.05)]
        incomplete = self.completeness.EmbeddingCompleteness(1, "ready", 2, 1)
        with patch.object(self.search, "inspect_embeddings", return_value=[incomplete]) as inspect, \
                patch.object(self.search, "create_query_embedding", return_value=vector()), \
                patch.object(self.search, "get_companion_chunks", return_value=[]), self.assertLogs(self.search.logger, "WARNING"):
            result = self.search.search_similar_chunks(db, "synthetic question", [1], min_similarity=0.1)
        self.assertTrue(result)
        self.assertEqual(result[0]["chunk"].id, 1)
        self.assertIn("document_chunks.embedding IS NOT NULL", [str(call.args[0]) for call in query.filter.call_args_list])
        inspect.assert_called_once_with(db, [1], self.search.GENERIC_CONTENT_TYPES)

    def test_cli_refuses_application_database_fallback_and_execute_without_model(self):
        with patch.dict(os.environ, {"DATABASE_URL": "synthetic-do-not-connect"}, clear=True), \
                patch.object(self.cli, "open_store") as open_store, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.cli.main(["--dry-run"])
            open_store.assert_not_called()
        with patch.dict(os.environ, {"EMBEDDING_RECOVERY_DATABASE_URL": "synthetic"}, clear=True), \
                patch.object(self.cli, "open_store") as open_store, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.cli.main(["--execute"])
            open_store.assert_not_called()

    def test_cli_default_dry_run_reports_incomplete_exit_without_provider_import(self):
        store = self.store([chunk(1)])
        @contextmanager
        def open_store(*args):
            self.assertEqual(args, ("synthetic-explicit-target", False, None))
            yield store
        with patch.dict(os.environ, {"EMBEDDING_RECOVERY_DATABASE_URL": "synthetic-explicit-target"}, clear=True), \
                patch.object(self.cli, "open_store", open_store), patch.object(self.provider, "create_passage_embeddings") as embed, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.cli.main([]), 2)
        embed.assert_not_called()
        self.assertEqual(store.commits, 0)
        self.assertIn('"dry_run": true', output.getvalue())

    def test_cli_connection_readonly_and_schema_guard(self):
        engine = MagicMock()
        connection = MagicMock()
        engine.connect.return_value.execution_options.return_value.__enter__.return_value = connection
        db = MagicMock()
        db.execute.return_value.scalar_one.return_value = 512
        with patch("sqlalchemy.create_engine", return_value=engine) as create_engine, patch("sqlalchemy.orm.Session") as session:
            session.return_value.__enter__.return_value = db
            with self.cli.open_store("synthetic-explicit-target", False, None):
                pass
            engine.connect.return_value.execution_options.assert_called_once_with(postgresql_readonly=True)
            create_engine.assert_called_once_with("synthetic-explicit-target", hide_parameters=True)
            db.commit.assert_not_called()
            db.execute.return_value.scalar_one.return_value = 384
            with self.assertRaises(ValueError), self.cli.open_store("synthetic-explicit-target", True, "voyage-4-lite"):
                pass

    def test_cli_model_mismatch_fails_before_database_connection(self):
        with patch("sqlalchemy.create_engine") as create_engine:
            with self.assertRaises(ValueError), self.cli.open_store("synthetic-explicit-target", True, "different-model"):
                pass
            create_engine.assert_not_called()

    def test_ingestion_does_not_set_ready_until_completeness_verified(self):
        from contextlib import contextmanager
        for valid_count in (0, 1):
            with self.subTest(valid_count=valid_count), ExitStack() as stack:
                db = MagicMock()
                document = SimpleNamespace(id=1, file_type="txt", file_path="synthetic.txt",
                                           processing_status="processing", processing_stage="uploaded")
                db.get.return_value = document
                db.scalar.return_value = None
                db.execute.return_value.mappings.return_value = []
                @contextmanager
                def session():
                    yield db
                claim = SimpleNamespace(session=session)
                @contextmanager
                def acquire(document_id):
                    yield claim
                stack.enter_context(patch.object(self.processing, "claim_document_processing", acquire))
                stack.enter_context(patch.object(self.processing.Path, "is_file", return_value=True))
                stack.enter_context(patch.object(self.processing, "extract_content", return_value=[{"type": "text", "content": "synthetic"}]))
                stack.enter_context(patch.object(self.processing, "inspect_embeddings", side_effect=[
                    [self.completeness.EmbeddingCompleteness(1, "processing", 0, 0)],
                    [self.completeness.EmbeddingCompleteness(1, "processing", 1, valid_count)],
                ]))
                error_log = stack.enter_context(patch.object(self.processing, "log_generation_failure", return_value="Synthetic safe failure"))
                stack.enter_context(patch.object(self.processing.logger, "warning"))
                self.processing.process_document(1, "ignored-task-path.txt")
                self.assertEqual(document.processing_status, "ready" if valid_count else "failed", error_log.call_args)
                db.flush.assert_called_once()
                db.delete.assert_not_called()

    def test_legacy_duplicate_guard_requires_explicit_recovery_without_reparse(self):
        from contextlib import contextmanager
        db = MagicMock()
        db.get.return_value = SimpleNamespace(id=1, processing_status="ready")
        @contextmanager
        def session():
            yield db
        @contextmanager
        def acquire(document_id):
            yield SimpleNamespace(session=session)
        with patch.object(self.processing, "claim_document_processing", acquire), \
                patch.object(self.processing, "inspect_embeddings", return_value=[
                    self.completeness.EmbeddingCompleteness(1, "ready", 1, 0)]), \
                patch.object(self.processing, "extract_content") as extract, self.assertLogs(self.processing.logger, "WARNING"):
            result = self.parser_service.process_document(1, "synthetic.txt")
        self.assertEqual(result, "embedding_recovery_required")
        extract.assert_not_called()
        db.commit.assert_not_called()


def test_embedding_recovery_in_isolated_process():
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
