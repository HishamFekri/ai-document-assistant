"""Database-free checks: python -B tests/test_public_generation_errors.py.

Exercise the real failure handlers, persistence helpers and response schemas with
mocked sessions/providers. The pytest wrapper uses a subprocess so these doubles
cannot interfere with Batch 1's integration database fixtures.
"""

import copy
import importlib
import io
import os
import subprocess
import sys
import unittest
from contextlib import contextmanager, ExitStack, redirect_stdout
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


SENSITIVE = "secret-token-123 /internal/path database-password"


class PublicGenerationErrorTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        backend = str(Path(__file__).resolve().parents[1])
        cls.stack.enter_context(patch.object(sys, "path", [backend, *sys.path]))
        cls.stack.enter_context(patch.dict(sys.modules))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        preserved = {key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
        cls.stack.enter_context(patch.dict(os.environ, {
            **preserved,
            "GOOGLE_CLIENT_ID": "synthetic-client",
            "JWT_SECRET_KEY": "synthetic-test-secret-never-used-in-production",
            "DEEPSEEK_API_KEY": "synthetic-key",
            "VOYAGE_API_KEY": "synthetic-key",
        }, clear=True))
        cls.stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        cls.stack.enter_context(patch("sqlalchemy.create_engine", side_effect=AssertionError("No database engines in this suite")))
        cls.stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("No network in this suite")))
        cls.stack.enter_context(patch("openai.OpenAI"))

        from sqlalchemy.orm import DeclarativeBase
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        database = ModuleType("app.database.database")
        class Base(DeclarativeBase):
            pass
        def forbidden_db():
            raise AssertionError("No real database sessions in this suite")
        database.Base = Base
        database.get_db = forbidden_db
        database.SessionLocal = forbidden_db
        sys.modules[database.__name__] = database

        cls.errors = importlib.import_module("app.services.error_service")
        cls.generation = importlib.import_module("app.services.summaries.summary_generation_service")
        cls.queue = importlib.import_module("app.services.queued_message_service")
        cls.schemas = importlib.import_module("app.schemas.schemas")
        cls.summary_schema = importlib.import_module("app.schemas.summary_schemas")
        cls.assistant_schema = importlib.import_module("app.schemas.summary_assistant_schemas")
        cls.summary_routes = importlib.import_module("app.routes.summaries")
        cls.chat_routes = importlib.import_module("app.routes.chats")
        cls.auth = importlib.import_module("app.routes.auth")
        cls.app = FastAPI()
        cls.app.include_router(cls.summary_routes.router)
        cls.app.include_router(cls.chat_routes.router)
        cls.database = database
        cls.TestClient = TestClient
        from resource_test_helpers import install_resource_mocks
        install_resource_mocks(cls.stack)
        cls.stack.enter_context(patch.object(cls.generation, "summary_owner_id", return_value=1))

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.document = SimpleNamespace(id=7, processing_status="ready", filename="synthetic.txt", file_type="txt", pages_count=1)
        self.summary = SimpleNamespace(
            id=11, document_id=7, chat_id=5, mode="summary", version=1,
            status="pending", error=None, content=None, is_selected=False,
            created_at=datetime(2026, 1, 1),
        )
        self.message = SimpleNamespace(
            id=13, chat_id=5, role="user", content="Synthetic question",
            status="waiting", error=None, sources=None, documents=[],
            created_at=datetime(2026, 1, 1),
        )
        self.db = MagicMock()
        self.db.get.side_effect = lambda model, *a, **k: (
            self.summary if model.__name__ == "DocumentSummary" else self.message
        )
        self.db.info = {"summary_context": (5, 7, "summary")}
        self.db.scalar.return_value = None
        self.db.query.return_value.filter.return_value.first.return_value = self.summary
        # Model the conditional SQL persistence boundary. SQL predicates and
        # actual stale-session behavior have separate SQLite/PostgreSQL tests.
        from sqlalchemy.sql.dml import Update
        def execute(statement, *args, **kwargs):
            if isinstance(statement, Update):
                params = statement.compile().params
                expected = params.get("status_1")
                if expected is not None:
                    expected = expected if isinstance(expected, list) else [expected]
                    if self.summary.status not in expected:
                        return SimpleNamespace(rowcount=0)
                for name in ("status", "error", "content", "is_selected"):
                    if name in params:
                        setattr(self.summary, name, params[name])
            return SimpleNamespace(rowcount=1)
        self.db.execute.side_effect = execute
        @contextmanager
        def claimed_session(*args):
            yield self.db
        self.stack.enter_context(patch.object(
            self.generation, "summary_generation_session", side_effect=claimed_session,
        ))
        self.stack.enter_context(patch.object(
            self.generation, "create_summary_record", return_value=self.summary,
        ))
        self.persisted = []
        self.db.commit.side_effect = lambda: self.persisted.append({
            "summary": copy.deepcopy(vars(self.summary)),
            "message": copy.deepcopy(vars(self.message)),
        })
        self.generate = self.stack.enter_context(patch.object(self.generation, "generate_summary_content"))
        self.answer = self.stack.enter_context(patch.object(self.queue, "answer_question"))
        self.claim = self.stack.enter_context(patch.object(self.queue, "claim_waiting_message", return_value=True))
        self.stack.enter_context(patch.object(self.summary_routes, "get_chat_document", return_value=(None, self.document)))
        self.stack.enter_context(patch.object(self.chat_routes, "get_owned_chat"))
        self.app.dependency_overrides[self.database.get_db] = lambda: self.db
        self.app.dependency_overrides[self.auth.get_current_user] = lambda: SimpleNamespace(id=1)
        self.addCleanup(self.app.dependency_overrides.clear)
        self.client = self.stack.enter_context(self.TestClient(self.app))

    def assert_no_sensitive_data(self, value):
        for marker in ("secret-token-123", "/internal/path", "database-password"):
            self.assertNotIn(marker, str(value))

    def fail_summary(self, error):
        # Each error case represents a fresh generation lifecycle.
        self.summary.status = "pending"
        self.summary.error = None
        self.generate.side_effect = error
        with self.assertLogs(self.errors.logger, level="ERROR") as captured:
            result = self.generation.generate_summary_for_record(self.db, self.document, self.summary)
        return result, captured

    def fail_message(self, error):
        self.answer.side_effect = error
        stdout = io.StringIO()
        with self.assertLogs(self.errors.logger, level="ERROR") as captured, redirect_stdout(stdout):
            self.queue.process_waiting_message(self.db, self.message)
        self.assert_no_sensitive_data(stdout.getvalue())
        return captured

    def test_summary_exception_is_persisted_and_serialized_as_safe_error(self):
        result, _ = self.fail_summary(RuntimeError(SENSITIVE))
        self.assertIs(result, self.summary)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "Summary generation failed. Please try again.")
        self.assertFalse(result.is_selected)
        self.assert_no_sensitive_data(self.persisted)
        response = self.summary_schema.DocumentSummaryResponse.model_validate(result).model_dump(mode="json")
        self.assertEqual(response["error"], result.error)
        self.assert_no_sensitive_data(response)
        self.db.rollback.assert_called()

    def test_summary_generation_http_response_keeps_200_and_failed_status(self):
        self.generate.side_effect = RuntimeError(SENSITIVE)
        with self.assertLogs(self.errors.logger, level="ERROR"):
            response = self.client.post("/documents/7/summaries/generate", json={"chat_id": 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(response.json()["error"], "Summary generation failed. Please try again.")
        self.assertEqual(set(response.json()), {"id", "chat_id", "document_id", "mode", "version", "status", "content", "is_selected", "error", "created_at"})
        self.assert_no_sensitive_data(response.text)

    def test_queued_message_exception_is_persisted_and_returned_safely(self):
        self.fail_message(RuntimeError(SENSITIVE))
        self.assertEqual(self.message.status, "failed")
        self.assertEqual(self.message.error, "Message generation failed. Please try again.")
        self.assert_no_sensitive_data(self.persisted)
        self.db.query.return_value.options.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [self.message]
        response = self.client.get("/chats/5/messages")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["error"], self.message.error)
        self.assertEqual(response.json()[0]["status"], "failed")
        self.assert_no_sensitive_data(response.text)
        self.db.rollback.assert_called_once()

    def test_value_errors_are_not_assumed_safe(self):
        self.fail_summary(ValueError(SENSITIVE))
        self.fail_message(ValueError(SENSITIVE))
        self.assert_no_sensitive_data(self.persisted)
        self.assertEqual(self.summary.error, self.errors.GENERATION_FAILED["summary"])
        self.assertEqual(self.message.error, self.errors.GENERATION_FAILED["message"])

    def test_provider_timeout_has_stable_public_category(self):
        import httpx
        from openai import APITimeoutError
        request = httpx.Request("POST", "https://internal.invalid/provider", headers={"Authorization": SENSITIVE})
        self.fail_summary(APITimeoutError(request))
        self.fail_message(APITimeoutError(request))
        self.assertEqual(self.summary.error, "Summary generation timed out. Please try again.")
        self.assertEqual(self.message.error, "Message generation timed out. Please try again.")
        self.assert_no_sensitive_data(self.persisted)

    def test_wrapped_timeouts_are_classified_without_reading_messages(self):
        import httpx
        import requests
        for timeout in (TimeoutError(SENSITIVE), httpx.ReadTimeout(SENSITIVE), requests.exceptions.Timeout(SENSITIVE)):
            wrapped = RuntimeError(SENSITIVE)
            wrapped.__cause__ = timeout
            result, captured = self.fail_summary(wrapped)
            self.assertEqual(result.error, self.errors.GENERATION_TIMED_OUT["summary"])
            self.assertEqual(captured.records[0].failure_category, "timeout")
            self.assert_no_sensitive_data(captured.output)

    def test_diagnostics_retain_type_ids_and_stack_locations_without_payloads(self):
        def provider_failure(**kwargs):
            full_document_text = SENSITIVE
            error = RuntimeError(full_document_text)
            error.provider_response = {"Authorization": SENSITIVE, "body": full_document_text}
            error.add_note(SENSITIVE)
            raise error
        self.generate.side_effect = provider_failure
        with self.assertLogs(self.errors.logger, level="ERROR") as captured:
            self.generation.generate_summary_for_record(self.db, self.document, self.summary)
        record = captured.records[0]
        self.assertEqual((record.operation, record.document_id, record.chat_id, record.summary_id), ("summary", 7, 5, 11))
        self.assertEqual(record.diagnostics[0]["exception_type"], "builtins.RuntimeError")
        self.assertTrue(any(frame["function"] == "provider_failure" for frame in record.diagnostics[0]["frames"]))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.stack_info)
        self.assert_no_sensitive_data(captured.output)
        self.assert_no_sensitive_data(record.__dict__)

    def test_message_diagnostics_survive_record_disappearing_after_rollback(self):
        self.db.get.side_effect = [self.message, None]
        captured = self.fail_message(RuntimeError(SENSITIVE))
        self.assertEqual(captured.records[0].operation, "message")
        self.assertEqual(captured.records[0].chat_id, 5)
        self.assertEqual(captured.records[0].message_id, 13)
        self.db.commit.assert_not_called()
        self.assert_no_sensitive_data(captured.output)

    def test_exception_string_is_never_evaluated_by_changed_handlers(self):
        class NonPrintableError(Exception):
            def __str__(self):
                raise AssertionError("Raw exception text must not be evaluated")
        self.fail_summary(NonPrintableError())
        self.fail_message(NonPrintableError())

    def test_database_exception_diagnostics_exclude_sql_and_parameters(self):
        from sqlalchemy.exc import StatementError
        error = StatementError(SENSITIVE, f"SELECT '{SENSITIVE}'", {"password": SENSITIVE}, RuntimeError(SENSITIVE))
        result, captured = self.fail_summary(error)
        self.assertEqual(result.error, self.errors.GENERATION_FAILED["summary"])
        self.assert_no_sensitive_data(captured.output)
        self.assert_no_sensitive_data(self.persisted)

    def test_historical_summary_schemas_mask_raw_values_without_modifying_rows(self):
        self.summary.error = SENSITIVE
        self.summary.status = "failed"
        before = copy.deepcopy(vars(self.summary))
        for schema in (self.summary_schema.DocumentSummaryResponse, self.assistant_schema.GeneratedSummaryResponse):
            result = schema.model_validate(self.summary).model_dump(mode="json")
            self.assertEqual(result["error"], self.errors.GENERATION_FAILED["summary"])
            self.assert_no_sensitive_data(result)
        self.assertEqual(vars(self.summary), before)
        self.db.commit.assert_not_called()

    def test_historical_message_read_masks_raw_value_without_db_writes(self):
        self.message.error = SENSITIVE
        self.message.status = "failed"
        self.db.query.return_value.options.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [self.message]
        response = self.client.get("/chats/5/messages")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["error"], self.errors.GENERATION_FAILED["message"])
        self.assert_no_sensitive_data(response.text)
        self.assertEqual(self.message.error, SENSITIVE)
        self.db.commit.assert_not_called()

    def test_existing_safe_messages_and_nulls_are_preserved(self):
        for operation, schema, row in (
            ("summary", self.summary_schema.DocumentSummaryResponse, self.summary),
            ("summary", self.assistant_schema.GeneratedSummaryResponse, self.summary),
            ("message", self.schemas.MessageResponse, self.message),
        ):
            for value in (None, *self.errors.SAFE_GENERATION_ERRORS[operation]):
                row.error = value
                self.assertEqual(schema.model_validate(row).model_dump()["error"], value)

    def test_document_not_ready_keeps_409_and_does_not_call_provider(self):
        self.document.processing_status = "processing"
        response = self.client.post("/documents/7/summaries/generate", json={"chat_id": 5})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Document is not ready for summary generation")
        self.generate.assert_not_called()

    def test_missing_summary_context_fails_safely_without_provider_call(self):
        self.summary.chat_id = None
        with self.assertLogs(self.errors.logger, level="ERROR"):
            self.generation.generate_summary_for_record(self.db, self.document, self.summary)
        self.assertEqual(self.summary.status, "failed")
        self.assertEqual(self.summary.error, self.errors.GENERATION_FAILED["summary"])
        self.generate.assert_not_called()

    def test_cancelled_summary_is_not_changed_to_failed(self):
        def cancelled_failure(**kwargs):
            self.summary.status = "cancelled"
            raise RuntimeError(SENSITIVE)
        self.generate.side_effect = cancelled_failure
        with self.assertLogs(self.errors.logger, level="ERROR"):
            self.generation.generate_summary_for_record(self.db, self.document, self.summary)
        self.assertEqual(self.summary.status, "cancelled")
        self.assertIsNone(self.summary.error)

    def test_successful_summary_and_message_keep_completed_status_and_null_error(self):
        content = {"title": "Summary", "sections": [{"type": "text", "content": "Result"}]}
        self.generate.return_value = content
        result = self.generation.generate_summary_for_record(self.db, self.document, self.summary)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error)
        self.assertEqual(result.content, content)
        self.answer.return_value = {"answer": "Result", "sources": []}
        with redirect_stdout(io.StringIO()):
            self.queue.process_waiting_message(self.db, self.message)
        self.assertEqual(self.message.status, "completed")
        self.assertIsNone(self.message.error)

    def test_unclaimed_message_is_not_processed(self):
        self.claim.return_value = False
        self.queue.process_waiting_message(self.db, self.message)
        self.answer.assert_not_called()
        self.db.get.assert_not_called()
        self.db.commit.assert_not_called()

    def test_exception_chain_cycles_are_bounded(self):
        first = RuntimeError(SENSITIVE)
        second = TimeoutError(SENSITIVE)
        first.__cause__ = second
        second.__cause__ = first
        _, captured = self.fail_summary(first)
        self.assertEqual(len(captured.records[0].diagnostics), 2)
        self.assert_no_sensitive_data(captured.output)


def test_public_generation_errors_in_isolated_process():
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve())],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
