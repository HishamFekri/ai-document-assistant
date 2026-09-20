"""Database-free checks: python -B tests/test_document_processing_reliability.py.

Uses Batch 5's isolated import harness, synthetic credentials, blocked engines and
blocked networking. PostgreSQL lock behavior has separate guarded live tests.
"""

import copy
from contextlib import contextmanager, ExitStack
from datetime import datetime
import hashlib
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import test_embedding_recovery as embedding_test_harness
from test_embedding_recovery import vector


class ProcessingState:
    def __init__(self, processing, completeness):
        self.connection = MagicMock()
        self.processing = processing
        self.completeness = completeness
        self.document = SimpleNamespace(
            id=1, user_id=7, filename="synthetic.txt", file_type="txt",
            file_path="synthetic.txt", pages_count=None, processing_status="processing",
            storage_key=None, file_size_bytes=None, file_sha256=None,
            processing_stage="uploaded", processing_progress=5, processing_error=None,
            created_at=datetime(2026, 1, 1),
        )
        self.chunks = []
        self.assets = []
        self.active = False
        self.owned = False
        self.sessions = 0
        self.vector_writes = 0

    @contextmanager
    def claim(self, document_id):
        if self.owned:
            yield None
            return
        self.owned = True
        try:
            yield self
        finally:
            self.owned = False

    @contextmanager
    def session(self):
        assert not self.active
        self.active = True
        self.sessions += 1
        before = copy.deepcopy((self.document, self.chunks, self.assets, self.vector_writes))
        try:
            yield self
        except Exception:
            self.document, self.chunks, self.assets, self.vector_writes = before
            raise
        finally:
            self.active = False

    def get(self, model, document_id, **kwargs):
        assert self.active
        return self.document

    def scalar(self, statement):
        assert self.active
        return self.chunks[0].id if self.chunks else None

    def execute(self, statement):
        assert self.active
        from sqlalchemy.sql.dml import Update
        if isinstance(statement, Update):
            values = statement.compile().params
            selected = next(item for item in self.chunks if item.id == values["id_1"])
            if selected.embedding is not None:
                return SimpleNamespace(rowcount=0)
            selected.embedding = values["embedding"]
            self.vector_writes += 1
            return SimpleNamespace(rowcount=1)
        rows = [dict(id=item.id, document_id=1, content=item.content, metadata_type="object")
                for item in self.chunks if item.embedding is None and item.content.strip()]
        rows = rows[:self.processing.PROCESSING_EMBEDDING_BATCH_SIZE]
        return SimpleNamespace(mappings=lambda: rows)

    def add(self, item):
        assert self.active
        if isinstance(item, self.processing.DocumentChunk):
            item.id = len(self.chunks) + 1
            self.chunks.append(item)
        else:
            self.assets.append(item)

    def flush(self):
        assert self.active

    def inspect(self, db, ids):
        assert self.active
        required = [item for item in self.chunks if item.content.strip()]
        valid = [item for item in required if item.embedding is not None]
        return [self.completeness.EmbeddingCompleteness(1, self.document.processing_status, len(required), len(valid))]


class DocumentProcessingReliabilityTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        # Reuse setup only, without inheriting or rerunning Batch 5's test methods.
        embedding_test_harness.EmbeddingRecoveryTests.setUpClass.__func__(cls)
        cls.claims = importlib.import_module("app.services.document_processing_claim")
        cls.failures = importlib.import_module("app.services.document_processing_errors")
        cls.storage = importlib.import_module("app.services.original_storage")
        cls.queue_module = importlib.import_module("app.services.task_queue")
        cls.worker = importlib.import_module("app.worker")
        cls.errors = importlib.import_module("app.services.error_service")
        sys.modules["app.services.file_service"].SUPPORTED_FILE_TYPES = {".pdf", ".docx", ".xlsx", ".txt"}
        auth = ModuleType("app.routes.auth")
        auth.get_current_user = lambda: None
        sys.modules[auth.__name__] = auth
        cls.database.get_db = lambda: None
        # Importing the upload route creates its configured upload directory.
        # Keep that reversible side effect inside an explicitly owned temp folder.
        cls.temp = cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="processing-tests-"))
        cls.stack.enter_context(patch("pathlib.Path.mkdir"))
        cls.routes = importlib.import_module("app.routes.documents")
        @contextmanager
        def quota(*args, **kwargs):
            yield MagicMock()
        cls.stack.enter_context(patch.object(cls.routes, "upload_quota_session", side_effect=quota))
        del sys.modules["app.services.assets.asset_extraction_service"]
        cls.assets_service = importlib.import_module("app.services.assets.asset_extraction_service")

    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.state = ProcessingState(self.processing, self.completeness)
        self.patches.enter_context(patch.object(self.processing, "claim_document_processing", self.state.claim))
        self.patches.enter_context(patch.object(self.processing, "inspect_embeddings", self.state.inspect))
        # This harness has synthetic paths and an in-memory checkpoint adapter.
        # Batch 9 separately exercises the real resource checks before embeddings.
        self.patches.enter_context(patch.object(self.processing, "validate_resumed_processing"))
        self.patches.enter_context(patch.object(self.processing.Path, "is_file", return_value=True))
        self.extract = self.patches.enter_context(patch.object(self.processing, "extract_content", side_effect=self.extract_content))
        self.embed = self.patches.enter_context(patch.object(self.processing, "create_passage_embeddings", side_effect=self.embeddings))
        self.ensure_assets = self.patches.enter_context(patch.object(self.processing, "ensure_document_assets"))
        self.wake = self.patches.enter_context(patch.object(self.processing, "process_waiting_messages_for_document"))
        self.patches.enter_context(patch.object(self.processing.logger, "info"))
        self.patches.enter_context(patch.object(self.processing.logger, "warning"))
        self.public_errors = self.patches.enter_context(patch.object(self.processing, "log_generation_failure", return_value="Document processing failed. Please try again."))

    def extract_content(self, **kwargs):
        self.assertFalse(self.state.active, "A transaction spans extraction")
        return [{"type": "text", "content": "Synthetic content", "metadata": {"page": 1}}]

    def embeddings(self, texts, **kwargs):
        self.assertFalse(self.state.active, "A transaction spans embeddings")
        return [vector() for _ in texts]

    def existing_chunk(self, embedding=None, text="Synthetic existing chunk"):
        self.state.chunks.append(self.processing.DocumentChunk(
            id=len(self.state.chunks) + 1, document_id=1, content=text, content_type="text",
            location="page 1", chunk_metadata={"page": 1}, embedding=embedding,
        ))

    def test_duplicate_overlapping_invocation_cannot_repeat_expensive_work(self):
        def extraction(**kwargs):
            self.assertFalse(self.state.active)
            self.assertEqual(self.processing.process_document(1), "busy")
            return self.extract_content(**kwargs)
        self.extract.side_effect = extraction
        self.assertEqual(self.processing.process_document(1), "completed")
        self.extract.assert_called_once()
        self.embed.assert_called_once()
        self.assertEqual(self.processing.process_document(1), "already_complete")
        self.assertEqual(len(self.state.chunks), 1)
        self.assertEqual(self.embed.call_count, 1)

    def test_ready_complete_guard_skips_file_access_and_provider_calls(self):
        self.state.document.processing_status = "ready"
        self.state.document.file_path = None
        self.existing_chunk(vector())
        self.assertEqual(self.processing.process_document(1), "already_complete")
        self.extract.assert_not_called()
        self.embed.assert_not_called()

    def test_ready_incomplete_requires_batch_five_recovery(self):
        self.state.document.processing_status = "ready"
        self.existing_chunk()
        self.assertEqual(self.processing.process_document(1), "embedding_recovery_required")
        self.extract.assert_not_called()
        self.embed.assert_not_called()

    def test_timeout_keeps_checkpoint_and_retry_only_processes_remaining_vectors(self):
        self.extract.side_effect = lambda **kwargs: [
            {"type": "text", "content": "First synthetic block"}, {"type": "text", "content": "Second synthetic block"}]
        self.patches.enter_context(patch.object(self.processing, "PROCESSING_EMBEDDING_BATCH_SIZE", 1))
        self.embed.side_effect = [[vector()], TimeoutError("secret synthetic provider body")]
        with self.assertRaises(self.failures.RetryableDocumentProcessingError) as failure:
            self.processing.process_document(1)
        self.assertNotIn("secret", str(failure.exception))
        self.assertEqual(self.state.vector_writes, 1)
        self.assertEqual(len(self.state.chunks), 2)
        self.assertEqual(self.state.document.processing_status, "failed")
        self.assertEqual(self.state.document.processing_stage, "retryable_failure")
        ids = [item.id for item in self.state.chunks]
        self.embed.reset_mock(side_effect=True)
        self.embed.side_effect = self.embeddings
        self.assertEqual(self.processing.process_document(1), "completed")
        self.extract.assert_called_once()
        self.ensure_assets.assert_called_once()
        self.embed.assert_called_once_with(["Second synthetic block"], batch_size=1)
        self.assertEqual([item.id for item in self.state.chunks], ids)

    def test_provider_timeout_never_erases_previously_valid_vectors(self):
        self.existing_chunk(vector(3))
        self.existing_chunk()
        self.embed.side_effect = TimeoutError("synthetic secret")
        with self.assertRaises(self.failures.RetryableDocumentProcessingError):
            self.processing.process_document(1)
        self.assertEqual(self.state.chunks[0].embedding, vector(3))
        self.assertIsNone(self.state.chunks[1].embedding)
        self.extract.assert_not_called()

    def test_permanent_failure_is_recorded_and_duplicate_delivery_does_not_retry(self):
        self.extract.side_effect = ValueError("synthetic malformed document secret")
        self.assertEqual(self.processing.process_document(1), "permanent_failure")
        self.assertEqual(self.state.document.processing_stage, "permanent_failure")
        self.assertNotIn("secret", self.state.document.processing_error)
        self.assertEqual(self.processing.process_document(1), "permanent_failure")
        self.extract.assert_called_once()

    def test_deleted_document_is_never_recreated(self):
        self.state.document = None
        self.assertEqual(self.processing.process_document(1), "deleted")
        self.extract.assert_not_called()
        self.embed.assert_not_called()

    def test_deletion_during_provider_call_stops_before_persistence(self):
        self.existing_chunk()
        def embedding(texts, **kwargs):
            self.state.document = None
            self.state.chunks.clear()
            return [vector()]
        self.embed.side_effect = embedding
        self.assertEqual(self.processing.process_document(1), "deleted")
        self.assertIsNone(self.state.document)
        self.assertEqual(self.state.chunks, [])
        self.assertEqual(self.state.vector_writes, 0)

    def test_incomplete_final_verification_cannot_mark_ready(self):
        self.existing_chunk(vector())
        incomplete = self.completeness.EmbeddingCompleteness(1, "processing", 2, 1)
        with patch.object(self.processing, "inspect_embeddings", return_value=[incomplete]):
            self.assertEqual(self.processing.process_document(1), "permanent_failure")
        self.assertEqual(self.state.document.processing_status, "failed")

    def test_background_and_legacy_paths_share_the_claim(self):
        self.assertIs(self.parser_service.process_document, self.processing.process_document)
        self.state.owned = True
        self.queue_module._run_document_processing(1)
        self.extract.assert_not_called()
        self.embed.assert_not_called()

    def test_pending_background_task_uses_common_entry_point(self):
        from fastapi import BackgroundTasks
        tasks = BackgroundTasks()
        with patch.object(self.queue_module, "TASK_QUEUE", "background"):
            self.queue_module.enqueue_document_processing(tasks, 1, "synthetic.txt")
        self.assertIs(tasks.tasks[0].func, self.queue_module._run_document_processing)

    def test_failed_queue_wakeup_does_not_repeat_successful_processing(self):
        self.wake.side_effect = RuntimeError("synthetic secret wakeup error")
        self.assertEqual(self.processing.process_document(1), "completed")
        self.assertEqual(self.state.document.processing_status, "ready")
        self.assertEqual(self.processing.process_document(1), "already_complete")
        self.extract.assert_called_once()

    def test_transient_and_permanent_classification_uses_types_and_status(self):
        import requests
        from billiard.exceptions import SoftTimeLimitExceeded
        for error in (TimeoutError(), requests.ConnectionError(), SoftTimeLimitExceeded(), self.failures.ProcessingClaimLost()):
            self.assertTrue(self.failures.is_retryable_processing_error(error))
        for error in (ValueError(), FileNotFoundError(), RuntimeError("429 timeout text is not a type")):
            self.assertFalse(self.failures.is_retryable_processing_error(error))
        for status, expected in ((400, False), (401, False), (429, True), (503, True)):
            response = requests.Response()
            response.status_code = status
            self.assertEqual(self.failures.is_retryable_processing_error(requests.HTTPError(response=response)), expected)
        outer = RuntimeError("Datalab wrapper")
        outer.__cause__ = requests.Timeout()
        self.assertTrue(self.failures.is_retryable_processing_error(outer))

    def test_celery_retry_and_backoff_are_bounded(self):
        from celery.exceptions import Retry
        task = self.worker.process_document_task
        with patch.object(self.processing, "process_document", side_effect=self.failures.RetryableDocumentProcessingError("safe")), \
                patch.object(self.processing, "mark_processing_retries_exhausted") as exhausted:
            for retries in range(4):
                task.push_request(retries=retries)
                try:
                    with patch.object(task, "retry", side_effect=Retry()) as retry:
                        if retries < 3:
                            with self.assertRaises(Retry):
                                task.run(1)
                            self.assertEqual(retry.call_args.kwargs["max_retries"], 3)
                            self.assertLessEqual(retry.call_args.kwargs["countdown"], 60)
                        else:
                            self.assertEqual(task.run(1), "retry_exhausted")
                            retry.assert_not_called()
                finally:
                    task.pop_request()
            exhausted.assert_called_once_with(1)
        self.assertLessEqual(self.worker.retry_delay(1000000), 60)
        self.assertFalse(self.worker.celery_app.conf.task_reject_on_worker_lost)

    def test_celery_does_not_retry_permanent_processing_result(self):
        task = self.worker.process_document_task
        with patch.object(self.processing, "process_document", return_value="permanent_failure"), patch.object(task, "retry") as retry:
            self.assertEqual(task.run(1), "permanent_failure")
        retry.assert_not_called()

    def test_exhausted_retries_are_persisted_and_skip_duplicate_delivery(self):
        self.state.document.processing_status = "failed"
        self.state.document.processing_stage = "retryable_failure"
        self.processing.mark_processing_retries_exhausted(1)
        self.assertEqual(self.state.document.processing_stage, "retry_exhausted")
        self.assertEqual(self.processing.process_document(1), "permanent_failure")
        self.extract.assert_not_called()
        self.embed.assert_not_called()

    def test_retry_publish_error_is_sanitized_without_another_processing_attempt(self):
        task = self.worker.process_document_task
        task.push_request(retries=0)
        try:
            with patch.object(self.processing, "process_document", side_effect=self.failures.RetryableDocumentProcessingError()), \
                    patch.object(task, "retry", side_effect=RuntimeError("secret broker credentials")), \
                    patch.object(self.worker, "log_generation_failure"), self.assertRaises(RuntimeError) as result:
                task.run(1)
            self.assertEqual(str(result.exception), "Could not schedule document processing retry")
        finally:
            task.pop_request()

    def test_celery_submission_disables_implicit_publish_retries(self):
        with patch.object(self.queue_module, "TASK_QUEUE", "celery"), \
                patch.object(self.worker.process_document_task, "apply_async") as submit:
            self.queue_module.enqueue_document_processing(MagicMock(), 1, "synthetic.txt")
        submit.assert_called_once_with(args=(1,), retry=False)

    def test_lost_claim_never_reconnects_for_a_write(self):
        connection = MagicMock(closed=False, invalidated=True)
        claim = self.claims.DocumentProcessingClaim(connection)
        with patch.object(self.claims, "Session") as session, self.assertRaises(self.failures.ProcessingClaimLost):
            with claim.session():
                self.fail("Lost claim yielded a session")
        session.assert_not_called()

    def test_claim_is_nonblocking_and_releases_session_lock(self):
        first, second = MagicMock(closed=False, invalidated=False), MagicMock(closed=False, invalidated=False)
        first.execute.return_value.scalar_one.side_effect = [True, True]
        second.execute.return_value.scalar_one.return_value = False
        with patch.object(self.claims.engine, "connect", side_effect=[first, second]):
            with self.claims.claim_document_processing(1) as owner:
                self.assertIsNotNone(owner)
                with self.claims.claim_document_processing(1) as duplicate:
                    self.assertIsNone(duplicate)
                self.assertEqual(str(first.execute.call_args_list[0].args[0]), "SELECT pg_try_advisory_lock(:namespace, :document_id)")
                first.commit.assert_called_once()
            self.assertEqual(str(first.execute.call_args_list[-1].args[0]), "SELECT pg_advisory_unlock(:namespace, :document_id)")
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_ambiguous_acquisition_invalidates_connection_instead_of_pooling_lock(self):
        connection = MagicMock(closed=False, invalidated=False)
        connection.execute.side_effect = RuntimeError("synthetic disconnected after server acquired lock")
        with patch.object(self.claims.engine, "connect", return_value=connection), self.assertRaises(RuntimeError):
            with self.claims.claim_document_processing(1):
                pass
        connection.invalidate.assert_called_once()
        connection.close.assert_called_once()

    def test_interrupted_unlock_invalidates_connection_before_returning_it(self):
        connection = MagicMock(closed=False, invalidated=False)
        connection.execute.side_effect = [MagicMock(), KeyboardInterrupt()]
        connection.execute.return_value.scalar_one.return_value = True
        with patch.object(self.claims.engine, "connect", return_value=connection), self.assertRaises(KeyboardInterrupt):
            with self.claims.claim_document_processing(1):
                pass
        connection.invalidate.assert_called_once()
        connection.close.assert_called_once()

    def test_dispatch_failure_marks_saved_upload_failed_and_preserves_file(self):
        from fastapi import BackgroundTasks, UploadFile
        db = MagicMock()
        saved = []
        def add(document):
            document.id = 1
            document.created_at = datetime(2026, 1, 1)
            saved.append(document)
        db.add.side_effect = add
        def update_failure(statement):
            saved[0].processing_status = "failed"
            saved[0].processing_stage = "dispatch_failed"
            saved[0].processing_error = "Document processing could not be scheduled. Please retry."
            return SimpleNamespace(rowcount=1)
        db.execute.side_effect = update_failure
        db.get.side_effect = lambda *args, **kwargs: saved[0]
        @contextmanager
        def quota(*args, **kwargs):
            yield db
        with patch.object(self.routes, "UPLOAD_DIR", Path(self.temp)), \
                patch.object(self.routes, "upload_quota_session", side_effect=quota), \
                patch.object(self.routes, "enqueue_document_processing", side_effect=RuntimeError("secret broker URL")), \
                patch.object(self.routes, "log_generation_failure"), patch.object(self.routes.logger, "warning"):
            response = self.routes.upload_document(BackgroundTasks(), UploadFile(file=io.BytesIO(b"synthetic"), filename="synthetic.txt"), db, SimpleNamespace(id=7))
        self.assertEqual(response.processing_status, "failed")
        self.assertNotIn("secret", response.model_dump_json())
        self.assertTrue(Path(saved[0].file_path).exists())
        db.delete.assert_not_called()
        self.assertGreaterEqual(db.commit.call_count, 2)
        sql = str(db.execute.call_args.args[0])
        self.assertIn("documents.processing_stage =", sql)
        self.assertIn("documents.processing_status =", sql)

    def test_private_raw_storage_persists_exact_metadata(self):
        source = io.BytesIO(b"synthetic original")
        upload_options = {}

        def upload(upload_source, **options):
            upload_options.update(options)
            self.assertIs(upload_source.source, source)
            return {
                "public_id": options["public_id"],
                "resource_type": "raw",
                "type": "authenticated",
                "bytes": len(source.getvalue()),
            }

        with patch.object(self.storage, "uses_shared_original_storage", return_value=True), \
                patch.object(self.storage, "_cloudinary_config", return_value=SimpleNamespace(
                    cloud_name="synthetic", api_key="synthetic", api_secret="synthetic",
                )), patch.object(self.storage.cloudinary.uploader, "upload_large", side_effect=upload), \
                patch.object(self.storage.cloudinary.uploader, "destroy") as destroy:
            stored = self.storage.store_original(
                source, ".txt", len(source.getvalue()), 1024, Path(self.temp),
            )

        self.assertIsNone(stored.file_path)
        self.assertRegex(stored.storage_key, self.storage.STORAGE_KEY_PATTERN)
        self.assertEqual(stored.file_size_bytes, len(source.getvalue()))
        self.assertEqual(stored.file_sha256, hashlib.sha256(source.getvalue()).hexdigest())
        self.assertEqual(upload_options["resource_type"], "raw")
        self.assertEqual(upload_options["type"], "authenticated")
        self.assertFalse(upload_options["overwrite"])
        self.assertEqual(source.tell(), 0)
        destroy.assert_not_called()

    def test_signed_worker_downloads_support_every_document_extension(self):
        from urllib.parse import parse_qs, urlsplit

        identifier = "0123456789abcdef0123456789abcdef"
        config = self.storage.cloudinary.Config()
        config.update(cloud_name="synthetic", api_key="synthetic", api_secret="synthetic")
        with patch.object(self.storage.cloudinary, "_config", config):
            for extension in ("pdf", "docx", "xlsx", "txt"):
                with self.subTest(extension=extension):
                    storage_key = f"ai-document-assistant/originals/{identifier}.{extension}"
                    parsed = urlsplit(self.storage._signed_download_url(storage_key, extension))
                    query = parse_qs(parsed.query)
                    self.assertEqual(parsed.scheme, "https")
                    self.assertEqual(parsed.netloc, "api.cloudinary.com")
                    self.assertEqual(query["public_id"], [storage_key])
                    self.assertEqual(query["format"], [extension])
                    self.assertEqual(query["type"], ["authenticated"])

    def test_storage_upload_failure_creates_no_document(self):
        from fastapi import BackgroundTasks, HTTPException, UploadFile

        db = MagicMock()
        @contextmanager
        def quota(*args, **kwargs):
            yield db

        with patch.object(self.routes, "upload_quota_session", side_effect=quota), \
                patch.object(self.routes, "store_original", side_effect=RuntimeError("storage unavailable")), \
                patch.object(self.routes, "enqueue_document_processing") as enqueue, \
                self.assertRaises(HTTPException) as error:
            self.routes.upload_document(
                BackgroundTasks(),
                UploadFile(file=io.BytesIO(b"synthetic"), filename="synthetic.txt"),
                db,
                SimpleNamespace(id=7),
            )

        self.assertEqual(error.exception.status_code, 500)
        db.add.assert_not_called()
        db.commit.assert_not_called()
        enqueue.assert_not_called()

    def test_ambiguous_private_upload_is_compensated_without_closing_request_file(self):
        source = io.BytesIO(b"synthetic original")
        storage_key = "ai-document-assistant/originals/0123456789abcdef0123456789abcdef.pdf"
        with patch.object(self.storage, "uses_shared_original_storage", return_value=True), \
                patch.object(self.storage, "_cloudinary_config", return_value=SimpleNamespace(
                    cloud_name="synthetic", api_key="synthetic", api_secret="synthetic",
                )), patch.object(self.storage, "uuid4", return_value=SimpleNamespace(
                    hex="0123456789abcdef0123456789abcdef",
                )), patch.object(
                    self.storage.cloudinary.uploader, "upload_large",
                    side_effect=RuntimeError("ambiguous provider response"),
                ), patch.object(
                    self.storage.cloudinary.uploader, "destroy", return_value={"result": "not found"},
                ) as destroy, self.assertRaises(RuntimeError):
            self.storage.store_original(source, ".pdf", len(source.getvalue()), 1024, Path(self.temp))

        destroy.assert_called_once_with(
            storage_key,
            resource_type="raw",
            type="authenticated",
            invalidate=False,
            timeout=30,
        )
        self.assertFalse(source.closed)
        self.assertEqual(source.tell(), 0)

    def test_production_upload_records_shared_key_checksum_and_enqueues_by_id(self):
        from fastapi import BackgroundTasks, UploadFile

        db = MagicMock()
        saved = []
        def add(document):
            document.id = 1
            document.created_at = datetime(2026, 1, 1)
            saved.append(document)
        db.add.side_effect = add
        @contextmanager
        def quota(*args, **kwargs):
            yield db
        stored = self.storage.StoredOriginal(
            None,
            "ai-document-assistant/originals/0123456789abcdef0123456789abcdef.txt",
            9,
            "a" * 64,
        )
        tasks = BackgroundTasks()
        with patch.object(self.routes, "upload_quota_session", side_effect=quota), \
                patch.object(self.routes, "store_original", return_value=stored), \
                patch.object(self.routes, "enqueue_document_processing") as enqueue:
            response = self.routes.upload_document(
                tasks,
                UploadFile(file=io.BytesIO(b"synthetic"), filename="synthetic.txt"),
                db,
                SimpleNamespace(id=7),
            )

        self.assertEqual(response.id, 1)
        self.assertIsNone(saved[0].file_path)
        self.assertEqual(saved[0].storage_key, stored.storage_key)
        self.assertEqual(saved[0].file_size_bytes, 9)
        self.assertEqual(saved[0].file_sha256, "a" * 64)
        enqueue.assert_called_once_with(tasks, 1, None)

    def test_document_commit_failure_removes_uploaded_shared_original(self):
        from fastapi import BackgroundTasks, HTTPException, UploadFile

        db = MagicMock()
        db.commit.side_effect = RuntimeError("database unavailable")
        @contextmanager
        def quota(*args, **kwargs):
            yield db
        stored = self.storage.StoredOriginal(
            None,
            "ai-document-assistant/originals/0123456789abcdef0123456789abcdef.txt",
            9,
            "a" * 64,
        )
        with patch.object(self.routes, "upload_quota_session", side_effect=quota), \
                patch.object(self.routes, "store_original", return_value=stored), \
                patch.object(self.routes, "delete_stored_original") as delete, \
                patch.object(self.routes, "enqueue_document_processing") as enqueue, \
                self.assertRaises(HTTPException) as error:
            self.routes.upload_document(
                BackgroundTasks(),
                UploadFile(file=io.BytesIO(b"synthetic"), filename="synthetic.txt"),
                db,
                SimpleNamespace(id=7),
            )

        self.assertEqual(error.exception.status_code, 500)
        delete.assert_called_once_with(stored.storage_key)
        enqueue.assert_not_called()

    def test_worker_download_is_verified_and_temporary_file_is_always_removed(self):
        data = b"verified shared original"
        temporary_path = Path(self.temp) / "worker-original.txt"
        response = MagicMock()
        response.status_code = 200
        response.headers = {"Content-Length": str(len(data)), "Content-Encoding": "identity"}
        response.iter_content.return_value = [data[:8], data[8:]]
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        session = MagicMock()
        session.get.return_value = response_context
        session_context = MagicMock()
        session_context.__enter__.return_value = session

        def make_temporary(**kwargs):
            descriptor = os.open(temporary_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            return descriptor, str(temporary_path)

        with patch.object(self.storage, "_signed_download_url", return_value="https://api.cloudinary.com/signed"), \
                patch.object(self.storage.requests, "Session", return_value=session_context), \
                patch.object(self.storage.tempfile, "mkstemp", side_effect=make_temporary):
            with self.storage.materialize_original(
                file_path=None,
                storage_key="ai-document-assistant/originals/0123456789abcdef0123456789abcdef.txt",
                file_type="txt",
                expected_size=len(data),
                checksum=hashlib.sha256(data).hexdigest(),
                max_size=1024,
            ) as path:
                self.assertTrue(path.is_file())
                self.assertEqual(path.read_bytes(), data)
            self.assertFalse(temporary_path.exists())

        bad_path = Path(self.temp) / "worker-bad-original.txt"
        response.iter_content.return_value = [data]
        with patch.object(self.storage, "_signed_download_url", return_value="https://api.cloudinary.com/signed"), \
                patch.object(self.storage.requests, "Session", return_value=session_context), \
                patch.object(self.storage.tempfile, "mkstemp", side_effect=lambda **kwargs: (
                    os.open(bad_path, os.O_CREAT | os.O_EXCL | os.O_RDWR), str(bad_path),
                )), self.assertRaises(ValueError):
            with self.storage.materialize_original(
                file_path=None,
                storage_key="ai-document-assistant/originals/0123456789abcdef0123456789abcdef.txt",
                file_type="txt",
                expected_size=len(data),
                checksum="0" * 64,
                max_size=1024,
            ):
                pass
        self.assertFalse(bad_path.exists())

    def test_worker_download_failure_uses_existing_retryable_failed_state(self):
        import requests

        self.state.document.file_path = None
        self.state.document.storage_key = (
            "ai-document-assistant/originals/0123456789abcdef0123456789abcdef.txt"
        )
        self.state.document.file_size_bytes = 9
        self.state.document.file_sha256 = "a" * 64

        @contextmanager
        def failed_download(**kwargs):
            raise requests.ConnectionError("synthetic storage outage")
            yield

        with patch.object(self.processing, "materialize_original", failed_download), \
                self.assertRaises(self.failures.RetryableDocumentProcessingError):
            self.processing.process_document(1)
        self.assertEqual(self.state.document.processing_status, "failed")
        self.assertEqual(self.state.document.processing_stage, "retryable_failure")
        self.extract.assert_not_called()

    def test_document_deletion_removes_authenticated_raw_original_after_commit(self):
        storage_key = "ai-document-assistant/originals/0123456789abcdef0123456789abcdef.txt"
        document = SimpleNamespace(id=1, file_path=None, storage_key=storage_key)
        db = MagicMock()
        with patch.object(self.routes, "get_owned_document", return_value=document), \
                patch.object(self.routes, "delete_stored_original") as delete:
            result = self.routes.delete_document(1, db, SimpleNamespace(id=7))
        self.assertEqual(result["document_id"], 1)
        db.commit.assert_called_once()
        delete.assert_called_once_with(storage_key)

    def test_retry_route_resets_interrupted_work_and_releases_claim_before_dispatch(self):
        from fastapi import BackgroundTasks
        db = MagicMock()
        self.state.document.file_path = None
        self.state.document.storage_key = (
            "ai-document-assistant/originals/0123456789abcdef0123456789abcdef.txt"
        )
        with patch.object(self.routes, "get_owned_document", return_value=self.state.document), \
                patch.object(self.routes, "claim_document_processing", self.state.claim), \
                patch.object(self.routes, "enqueue_document_processing") as dispatch:
            dispatch.side_effect = lambda *args: self.assertFalse(self.state.owned)
            response = self.routes.retry_document_processing(1, BackgroundTasks(), db, SimpleNamespace(id=7))
        self.assertEqual(response.processing_stage, "uploaded")
        dispatch.assert_called_once()

    def test_ambiguous_dispatch_failure_returns_workers_current_ready_state(self):
        self.state.document.processing_status = "ready"
        self.state.document.processing_stage = "ready"
        db = MagicMock()
        db.execute.return_value.rowcount = 0
        db.get.return_value = self.state.document
        with patch.object(self.routes, "enqueue_document_processing", side_effect=RuntimeError("synthetic ambiguous reply")), \
                patch.object(self.routes, "log_generation_failure"), patch.object(self.routes.logger, "warning"):
            result = self.routes.dispatch_uploaded_document(db, MagicMock(), 1, "synthetic.txt", None)
        self.assertEqual(result.processing_status, "ready")
        self.assertIsNone(result.processing_error)
        self.assertIn("uploaded", db.execute.call_args.args[0].compile().params.values())

    def test_retry_route_rejects_active_owner_and_ownership_mismatch(self):
        from fastapi import BackgroundTasks, HTTPException
        db = MagicMock()
        for owned, owner_id, code in ((True, 7, 409), (False, 8, 404)):
            self.state.owned = owned
            with patch.object(self.routes, "get_owned_document", return_value=self.state.document), \
                    patch.object(self.routes, "claim_document_processing", self.state.claim), \
                    patch.object(self.routes, "enqueue_document_processing") as dispatch, self.assertRaises(HTTPException) as error:
                self.routes.retry_document_processing(1, BackgroundTasks(), db, SimpleNamespace(id=owner_id))
            self.assertEqual(error.exception.status_code, code)
            dispatch.assert_not_called()

    def test_asset_reuse_keeps_existing_ids_and_avoids_duplicate_rows(self):
        content = [{"type": "table", "content": "Synthetic table", "location": "page 1", "metadata": {"page": 1}}]
        existing = self.assets_service.build_document_assets(1, content)[0]
        existing.id = 91
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [existing]
        self.assets_service.ensure_document_assets(db, 1, content + content)
        db.add.assert_not_called()
        db.delete.assert_not_called()
        self.assertEqual(existing.id, 91)

    def test_changed_legacy_assets_require_review_without_deletion_or_duplicates(self):
        content = [{"type": "table", "content": "Synthetic table", "location": "page 1"}]
        existing = self.assets_service.build_document_assets(1, content)[0]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [existing]
        with self.assertRaises(ValueError):
            self.assets_service.ensure_document_assets(db, 1, [{**content[0], "content": "Different extraction"}])
        db.add.assert_not_called()
        db.delete.assert_not_called()

    def test_document_failure_diagnostics_do_not_log_sensitive_exception_text(self):
        with self.assertLogs(self.errors.logger, level="ERROR") as captured:
            public = self.errors.log_generation_failure(RuntimeError("secret-token document text database-password"), "document", document_id=1)
        for secret in ("secret-token", "document text", "database-password"):
            self.assertNotIn(secret, str(captured.output))
            self.assertNotIn(secret, public)


def test_document_processing_reliability_in_isolated_process():
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
