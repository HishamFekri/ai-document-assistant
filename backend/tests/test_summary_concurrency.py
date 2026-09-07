"""Local regression checks: python -B tests/test_summary_concurrency.py.

Real lifecycle SQL runs only in SQLite memory, with PostgreSQL locks replaced at
their boundary. Providers/network/application engines are blocked by Batch 4's
isolated import harness. This does not verify PostgreSQL concurrency.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from datetime import datetime
import importlib
import json
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import test_public_generation_errors as public_error_harness


CONTENT = {"title": "Synthetic", "sections": [{"type": "text", "content": "Result"}]}


@compiles(JSONB, "sqlite")
def sqlite_jsonb(type_, compiler, **kwargs):
    return "JSON"


class SummaryConcurrencyTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        # The only real engine in this suite is this literal in-memory target.
        cls.engine = create_engine(
            "sqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        cls.addClassCleanup(cls.engine.dispose)
        public_error_harness.PublicGenerationErrorTests.setUpClass()
        cls.addClassCleanup(public_error_harness.PublicGenerationErrorTests.doClassCleanups)
        cls.generation = public_error_harness.PublicGenerationErrorTests.generation
        cls.routes = public_error_harness.PublicGenerationErrorTests.summary_routes
        cls.claims = importlib.import_module("app.services.summaries.summary_claim")
        cls.service = importlib.import_module("app.services.summaries.summary_service")
        cls.model = cls.service.DocumentSummary
        cls.model.__table__.create(cls.engine)

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        with self.engine.begin() as connection:
            connection.execute(self.model.__table__.delete())
        self.lock = self.stack.enter_context(patch.object(self.service, "lock_summary_context"))
        self.safe = self.stack.enter_context(patch.object(self.service, "cleanup_is_safe", return_value=True))
        self.owner_lock = Lock()
        self.releases = 0
        self.active_db = None

        @contextmanager
        def claim(chat_id, document_id, mode):
            if not self.owner_lock.acquire(blocking=False):
                raise self.claims.SummaryGenerationBusy()
            try:
                with self.session((chat_id, document_id, mode)) as db:
                    self.active_db = db
                    yield db
            finally:
                self.active_db = None
                self.releases += 1
                self.owner_lock.release()

        self.stack.enter_context(patch.object(self.generation, "summary_generation_session", side_effect=claim))
        self.provider = self.stack.enter_context(patch.object(
            self.generation, "generate_summary_content", return_value=CONTENT,
        ))
        self.document = SimpleNamespace(
            id=7, filename="synthetic.txt", file_type="txt", pages_count=1,
            processing_status="ready",
        )

    def session(self, context=(5, 7, "summary")):
        return Session(self.engine, expire_on_commit=False, autoflush=False,
                       info={"summary_context": context})

    def row(self, version=1, status="pending", selected=False, mode="summary"):
        with self.session() as db:
            row = self.model(
                chat_id=5, document_id=7, mode=mode, version=version,
                status=status, is_selected=selected, error=None, content=None,
                created_at=datetime(2026, 1, 1),
            )
            db.add(row)
            db.commit()
            db.expunge(row)
            return row

    def rows(self):
        with self.session() as db:
            return db.scalars(select(self.model).order_by(self.model.version)).all()

    def generate(self):
        return self.generation.generate_summary_for_record(
            MagicMock(), self.document, chat_id=5,
        )

    def test_overlapping_starts_allocate_one_version_and_one_provider_call(self):
        entered, release = Event(), Event()
        def provider(**kwargs):
            self.assertFalse(kwargs["db"].in_transaction())
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release provider")
            return CONTENT
        self.provider.side_effect = provider
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.generate)
            try:
                self.assertTrue(entered.wait(5))
                duplicate = executor.submit(self.generate)
                with self.assertRaises(self.claims.SummaryGenerationBusy):
                    duplicate.result(timeout=5)
            finally:
                release.set()
            result = first.result(timeout=5)
        self.assertEqual((result.version, result.status), (1, "completed"))
        self.assertEqual(len(self.rows()), 1)
        self.provider.assert_called_once()

    def test_regenerate_allocates_next_version_and_releases_claim(self):
        first, second = self.generate(), self.generate()
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertEqual([row.id for row in self.rows()], [second.id])
        self.assertEqual(self.releases, 2)
        self.assertFalse(self.owner_lock.locked())

    def test_stale_active_record_requires_cancel_before_regenerate(self):
        old = self.row(status="generating")
        with self.assertRaises(self.claims.SummaryGenerationBusy):
            self.generate()
        self.provider.assert_not_called()
        with self.session() as db:
            self.service.mark_summary_cancelled(db, old)
        self.assertEqual(self.generate().version, 2)

    def test_cancel_before_generating_prevents_paid_call(self):
        row = self.row(status="cancelled", selected=True)
        result = self.generation.generate_summary_for_record(MagicMock(), self.document, row)
        self.assertEqual(result.status, "cancelled")
        self.provider.assert_not_called()

    def test_late_provider_completion_cannot_overwrite_cancellation(self):
        def provider(**kwargs):
            self.assertFalse(kwargs["db"].in_transaction())
            current = self.rows()[0]
            with self.session() as db:
                self.service.mark_summary_cancelled(db, current)
            return CONTENT
        self.provider.side_effect = provider
        result = self.generate()
        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(result.content)
        self.assertTrue(result.is_selected)
        self.assertFalse(self.owner_lock.locked())

    def test_stale_completion_and_failure_respect_cancelled_database_state(self):
        row = self.row(status="generating")
        with self.session() as stale, self.session() as cancel:
            stale_row = stale.get(self.model, row.id)
            stale.rollback()
            self.service.mark_summary_cancelled(cancel, row)
            completed = self.service.mark_summary_completed(stale, stale_row, CONTENT)
            failed = self.service.mark_summary_failed(stale, row, "raw secret")
        self.assertEqual((completed.status, failed.status), ("cancelled", "cancelled"))
        self.assertIsNone(failed.error)

    def test_two_completion_attempts_keep_first_result(self):
        row = self.row(status="generating")
        with self.session() as first, self.session() as second:
            stale = second.get(self.model, row.id)
            first_result = self.service.mark_summary_completed(first, row, CONTENT)
            late_result = self.service.mark_summary_completed(second, stale, {"title": "Late"})
        self.assertEqual(first_result.content, CONTENT)
        self.assertEqual(late_result.content, CONTENT)
        self.assertEqual(late_result.status, "completed")

    def test_completion_wins_before_cancel(self):
        row = self.row(status="generating")
        with self.session() as db:
            self.service.mark_summary_completed(db, row, CONTENT)
            result = self.service.mark_summary_cancelled(db, row)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, CONTENT)

    def test_completion_preserves_other_pending_and_generating_rows(self):
        pending = self.row(1, "pending")
        generating = self.row(2, "generating")
        owner = self.row(3, "generating")
        with self.session() as db:
            self.service.mark_summary_completed(db, owner, CONTENT)
        self.assertEqual({row.id for row in self.rows()}, {pending.id, generating.id, owner.id})

    def test_cleanup_only_removes_older_unselected_terminal_rows(self):
        rows = [self.row(i, state) for i, state in enumerate(
            ("completed", "failed", "cancelled", "pending", "generating"), 1,
        )]
        selected = self.row(6, "completed", True)
        keep = self.row(7, "completed")
        newer = self.row(8, "completed")
        with self.session() as db:
            self.service.cleanup_old_summaries(db, 5, 7, "summary", keep.id)
            db.commit()
        self.assertEqual({r.id for r in self.rows()}, {rows[3].id, rows[4].id, selected.id, keep.id, newer.id})

    def test_cleanup_skips_context_owned_by_another_request(self):
        old = self.row(1, "cancelled")
        keep = self.row(2, "completed", True)
        self.safe.return_value = False
        with self.session() as db:
            self.service.cleanup_old_summaries(db, 5, 7, "summary", keep.id)
            db.commit()
        self.assertEqual({r.id for r in self.rows()}, {old.id, keep.id})

    def test_selection_preserves_active_and_other_mode(self):
        old = self.row(1, "completed")
        keep = self.row(2, "completed")
        active = self.row(3, "generating")
        other_mode = self.row(1, "completed", True, "transcription")
        with self.session() as db:
            selected = self.service.select_summary(db, 5, 7, keep.id)
            visible = self.service.get_selected_summary(db, 5, 7)
            self.assertEqual(visible.id, keep.id)
        self.assertTrue(selected.is_selected)
        self.assertEqual({r.id for r in self.rows()}, {keep.id, active.id, other_mode.id})
        self.assertNotIn(old.id, {r.id for r in self.rows()})

    def test_older_completion_cannot_replace_newer_selected_result(self):
        old = self.row(1, "generating")
        newer = self.row(2, "completed", True)
        with self.session() as db:
            result = self.service.mark_summary_completed(db, old, CONTENT)
        self.assertFalse(result.is_selected)
        self.assertTrue(next(r for r in self.rows() if r.id == newer.id).is_selected)

    def test_failure_is_safe_terminal_and_releases_claim(self):
        self.provider.side_effect = RuntimeError("private provider secret")
        with self.assertLogs("app.services.error_service", level="ERROR"):
            result = self.generate()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "Summary generation failed. Please try again.")
        self.assertEqual(self.releases, 1)
        self.assertFalse(self.owner_lock.locked())
        self.provider.side_effect = None
        self.assertEqual(self.generate().version, 2)

    def test_deleted_summary_is_not_recreated_by_late_writes(self):
        row = self.row(status="generating")
        with self.session() as db:
            self.service.delete_summary(db, row)
            self.assertIsNone(self.service.mark_summary_completed(db, row, CONTENT))
            self.assertIsNone(self.service.mark_summary_failed(db, row, "secret"))
            self.assertIsNone(self.service.mark_summary_cancelled(db, row))
        self.assertEqual(self.rows(), [])

    def test_cancelled_partial_saved_once_without_reselection(self):
        row = self.row(status="generating")
        with self.session() as db:
            self.service.mark_summary_cancelled(db, row)
            first = self.service.mark_summary_cancelled(db, row, CONTENT)
            second = self.service.mark_summary_cancelled(db, row, {"title": "Late"})
        self.assertEqual(first.content, CONTENT)
        self.assertEqual(second.content, CONTENT)
        self.assertEqual(second.status, "cancelled")

    def test_lock_namespace_and_key_are_stable_and_context_specific(self):
        keys = {self.claims.context_key(*context) for context in (
            (5, 7, "summary"), (6, 7, "summary"), (5, 8, "summary"), (5, 7, "transcription"),
        )}
        self.assertEqual(len(keys), 4)
        self.assertEqual(self.claims.context_key(5, 7, "summary"), self.claims.context_key(5, 7, "summary"))
        self.assertEqual(len({0x444F4350, self.claims.GENERATION_NAMESPACE, self.claims.MUTATION_NAMESPACE}), 3)

    def test_real_claim_helper_releases_after_success_and_failure(self):
        for fail in (False, True):
            connection = MagicMock(closed=False, invalidated=False)
            connection.scalar.return_value = True
            engine = MagicMock()
            engine.connect.return_value = connection
            with patch.object(self.claims.database, "engine", engine, create=True):
                try:
                    with self.claims.summary_generation_session(5, 7, "summary") as db:
                        self.assertFalse(db.in_transaction())
                        if fail:
                            raise ValueError("synthetic")
                except ValueError:
                    self.assertTrue(fail)
            self.assertIn("pg_advisory_unlock", str(connection.scalar.call_args_list[-1].args[0]))
            connection.close.assert_called_once()

    def test_real_claim_helper_rejects_duplicate_without_unlocking_owner(self):
        connection = MagicMock(closed=False, invalidated=False)
        connection.scalar.return_value = False
        with patch.object(self.claims.database, "engine", create=True) as engine:
            engine.connect.return_value = connection
            with self.assertRaises(self.claims.SummaryGenerationBusy):
                with self.claims.summary_generation_session(5, 7, "summary"):
                    self.fail("Duplicate got a session")
        connection.scalar.assert_called_once()
        connection.close.assert_called_once()

    def test_lost_claim_cannot_reconnect_or_write(self):
        connection = MagicMock(closed=False, invalidated=False)
        connection.scalar.return_value = True
        with patch.object(self.claims.database, "engine", create=True) as engine:
            engine.connect.return_value = connection
            with self.claims.summary_generation_session(5, 7, "summary") as db:
                connection.invalidated = True
                with self.assertRaises(self.claims.SummaryClaimLost):
                    db.execute(text("SELECT 1"))
        connection.execute.assert_not_called()

    def test_uncertain_acquisition_and_unlock_invalidate_connection(self):
        for outcomes in ((RuntimeError("synthetic"),), (True, RuntimeError("synthetic")), (True, False)):
            connection = MagicMock(closed=False, invalidated=False)
            connection.scalar.side_effect = outcomes
            with patch.object(self.claims.database, "engine", create=True) as engine:
                engine.connect.return_value = connection
                try:
                    with self.claims.summary_generation_session(5, 7, "summary"):
                        pass
                except RuntimeError:
                    pass
            connection.invalidate.assert_called_once()
            connection.close.assert_called_once()

    def test_mutation_and_cleanup_use_transaction_locks(self):
        db = MagicMock()
        self.claims.lock_summary_context(db, 5, 7, "summary")
        self.claims.cleanup_is_safe(db, 5, 7, "summary")
        self.assertIn("pg_advisory_xact_lock", str(db.execute.call_args.args[0]))
        self.assertIn("pg_try_advisory_xact_lock", str(db.scalar.call_args.args[0]))
        self.assertNotEqual(db.execute.call_args.args[1]["namespace"], db.scalar.call_args.args[1]["namespace"])

    def test_summary_provider_boundaries_have_no_open_transaction(self):
        with self.session() as db:
            def history(**kwargs):
                db.execute(text("SELECT 1"))
                return [SimpleNamespace(role="user", content="Use concise prose")]
            calls = []
            response = MagicMock()
            response.choices = [SimpleNamespace(message=SimpleNamespace(content="{}"))]
            response.__iter__.return_value = iter([
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=json.dumps({"type": "title", "title": "T"}) + "\n" + json.dumps({"type": "section", "section": CONTENT["sections"][0]}) + "\n"))]),
            ])
            def provider(**kwargs):
                self.assertFalse(db.in_transaction())
                calls.append(kwargs)
                return response
            with patch.object(self.generation, "get_summary_instruction_history", side_effect=history), \
                 patch.object(self.generation, "get_document_summary_context", return_value={"text_context": "Text", "asset_context": "", "document": vars(self.document)}), \
                 patch.object(self.generation, "resolve_output_language", return_value="English"), \
                 patch.object(self.generation.client.chat.completions, "create", side_effect=provider):
                events = list(self.generation.stream_summary_content(db, self.document, 5))
            self.assertEqual(len(calls), 2)
            self.assertEqual([event["type"] for event in events], ["title", "section"])
            response.close.assert_called_once()

    def test_transcription_page_provider_has_no_open_transaction(self):
        with self.session() as db:
            def context(**kwargs):
                db.execute(text("SELECT 1"))
                return {"pages": [{"page_number": 1, "text_context": "Synthetic text", "assets": []}]}
            def provider(**kwargs):
                self.assertFalse(db.in_transaction())
                return [{"kind": "text", "content": "Page result"}]
            with patch.object(self.generation, "get_document_transcription_context", side_effect=context), \
                 patch.object(self.generation, "build_summary_instruction_context", return_value=""), \
                 patch.object(self.generation, "resolve_output_language", return_value="English"), \
                 patch.object(self.generation, "generate_transcription_page_segments", side_effect=provider) as call:
                list(self.generation.stream_transcription_content(
                    db, self.document, 5, request={"scope_type": "whole_document"},
                ))
            call.assert_called_once()

    def http_client(self):
        harness = public_error_harness.PublicGenerationErrorTests
        self.stack.enter_context(patch.object(self.routes, "get_chat_document", return_value=(None, self.document)))
        self.stack.enter_context(patch.object(self.routes, "SessionLocal", side_effect=self.session))
        harness.app.dependency_overrides[harness.database.get_db] = lambda: MagicMock()
        harness.app.dependency_overrides[harness.auth.get_current_user] = lambda: SimpleNamespace(id=1)
        self.addCleanup(harness.app.dependency_overrides.clear)
        return self.stack.enter_context(harness.TestClient(harness.app))

    def stream_events(self, **kwargs):
        self.assertFalse(kwargs["db"].in_transaction())
        yield {"type": "title", "title": CONTENT["title"]}
        yield {"type": "section", "section": CONTENT["sections"][0]}
        return CONTENT

    def test_http_stream_preserves_ndjson_contract_and_releases_owner(self):
        client = self.http_client()
        with patch.object(self.routes, "stream_summary_content", side_effect=self.stream_events):
            response = client.post("/documents/7/summaries/generate/stream", json={"chat_id": 5})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([e["type"] for e in events], ["start", "title", "section", "done"])
        self.assertEqual(events[-1]["summary"]["status"], "completed")
        self.assertFalse(self.owner_lock.locked())

    def test_duplicate_http_and_stream_cannot_cancel_or_fail_original(self):
        client = self.http_client()
        with self.generation.start_summary_generation(5, 7) as (_, original, owns):
            self.assertTrue(owns)
            with patch.object(self.routes, "stream_summary_content") as provider:
                plain = client.post("/documents/7/summaries/generate", json={"chat_id": 5})
                stream = client.post("/documents/7/summaries/generate/stream", json={"chat_id": 5})
            self.assertEqual(plain.status_code, 409)
            events = [json.loads(line) for line in stream.text.splitlines()]
            self.assertEqual([e["type"] for e in events], ["error"])
            provider.assert_not_called()
            self.provider.assert_not_called()
            rows = self.rows()
            self.assertEqual([(r.id, r.status) for r in rows], [(original.id, "generating")])

    def test_stream_cancel_during_provider_discards_late_event_and_done(self):
        client = self.http_client()
        def provider(**kwargs):
            yield {"type": "title", "title": "Already sent"}
            current = self.rows()[0]
            with self.session() as db:
                self.service.mark_summary_cancelled(db, current)
            yield {"type": "section", "section": {"type": "text", "content": "Late"}}
            return CONTENT
        with patch.object(self.routes, "stream_summary_content", side_effect=provider):
            response = client.post("/documents/7/summaries/generate/stream", json={"chat_id": 5})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([e["type"] for e in events], ["start", "title"])
        row = self.rows()[0]
        self.assertEqual(row.status, "cancelled")
        self.assertEqual(row.content["title"], "Already sent")
        self.assertNotIn("Late", json.dumps(row.content))
        self.assertFalse(self.owner_lock.locked())

    def test_stream_generator_exit_persists_partial_and_releases_claim(self):
        self.http_client()
        with patch.object(self.routes, "StreamingResponse", side_effect=lambda iterator, **kwargs: iterator), \
             patch.object(self.routes, "stream_summary_content", side_effect=self.stream_events):
            iterator = self.routes.stream_document_summary(
                7, self.routes.SummaryGenerateRequest(chat_id=5), SimpleNamespace(id=1), MagicMock(),
            )
            self.assertEqual(json.loads(next(iterator))["type"], "start")
            self.assertEqual(json.loads(next(iterator))["type"], "title")
            iterator.close()
        self.assertEqual(self.rows()[0].status, "cancelled")
        self.assertEqual(self.rows()[0].content["title"], CONTENT["title"])
        self.assertFalse(self.owner_lock.locked())

    def test_stream_failure_uses_safe_error_and_releases_claim(self):
        client = self.http_client()
        with patch.object(self.routes, "stream_summary_content", side_effect=RuntimeError("private secret")), \
             self.assertLogs("app.services.error_service", level="ERROR"):
            response = client.post("/documents/7/summaries/generate/stream", json={"chat_id": 5})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([e["type"] for e in events], ["start", "error"])
        self.assertNotIn("private secret", response.text)
        self.assertEqual(self.rows()[0].status, "failed")
        self.assertFalse(self.owner_lock.locked())

    def test_cancel_route_returns_completion_winner(self):
        client = self.http_client()
        stale = self.row(status="generating")
        with self.session() as db:
            self.service.mark_summary_completed(db, stale, CONTENT)
        harness = public_error_harness.PublicGenerationErrorTests
        def request_db():
            with self.session() as db:
                yield db
        harness.app.dependency_overrides[harness.database.get_db] = request_db
        with patch.object(self.routes, "get_summary_by_id", return_value=stale):
            response = client.post(f"/documents/7/summaries/{stale.id}/cancel?chat_id=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")

    def test_cancellation_after_instruction_call_prevents_summary_provider_call(self):
        row = self.row(status="generating")
        with self.session() as db:
            db.info["summary_id"] = row.id
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])
            def provider(**kwargs):
                self.assertFalse(db.in_transaction())
                with self.session() as cancel:
                    self.service.mark_summary_cancelled(cancel, row)
                return response
            with patch.object(self.generation, "get_summary_instruction_history", return_value=[SimpleNamespace(role="user", content="Instructions")]), \
                 patch.object(self.generation, "get_document_summary_context", return_value={"text_context": "Text"}), \
                 patch.object(self.generation, "resolve_output_language", return_value="English"), \
                 patch.object(self.generation.client.chat.completions, "create", side_effect=provider) as call:
                with self.assertRaises(self.generation.SummaryGenerationStopped):
                    list(self.generation.stream_summary_content(db, self.document, 5))
            call.assert_called_once()

    def test_cancelled_transcription_does_not_call_fallback_provider(self):
        row = self.row(status="generating")
        with self.session() as db:
            db.info["summary_id"] = row.id
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="malformed"))])
            def provider(**kwargs):
                self.assertFalse(db.in_transaction())
                with self.session() as cancel:
                    self.service.mark_summary_cancelled(cancel, row)
                return response
            with patch.object(self.generation.client.chat.completions, "create", side_effect=provider) as call:
                with self.assertRaises(self.generation.SummaryGenerationStopped):
                    self.generation.generate_transcription_page_segments(
                        self.document, {"page_number": 1, "text_context": "Text", "assets": []},
                        "", "English", before_provider=lambda: self.generation.prepare_summary_provider_call(db),
                    )
            call.assert_called_once()


def test_summary_concurrency_in_isolated_process():
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve())],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
