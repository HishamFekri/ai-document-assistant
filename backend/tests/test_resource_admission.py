"""Run with python -B tests/test_resource_admission.py. No network/providers/DB."""

import asyncio
from concurrent.futures import ThreadPoolExecutor, Future
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Lock
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


class SharedRates:
    """Test double for Redis's atomic script boundary, shared by all clients."""
    def __init__(self):
        self.now = 0
        self.entries = {}
        self.mutex = Lock()
        self.calls = 0

    def eval(self, script, key_count, key, window, limit, token):
        with self.mutex:
            self.calls += 1
            times = [t for t in self.entries.get(key, []) if t > self.now - window]
            if len(times) >= limit:
                return 0, max(1, math.ceil(times[0] + window - self.now))
            times.append(self.now)
            self.entries[key] = times
            return 1, 0


class SharedPermits:
    """Physical-session lock double, not a production admission backend."""
    def __init__(self):
        self.slots = {}
        self.connections = []
        self.mutex = Lock()

    def connect(self):
        owner = self
        class Connection:
            closed = False
            invalidated = False
            def in_transaction(self):
                return False
            def scalar(self, statement, parameters):
                key = (parameters["namespace"], parameters["user_id"])
                with owner.mutex:
                    if "pg_try_advisory_lock" in str(statement):
                        if key in owner.slots and owner.slots[key] is not self:
                            return False
                        owner.slots[key] = self
                        return True
                    if owner.slots.get(key) is self:
                        del owner.slots[key]
                        return True
                    return False
            def commit(self):
                pass
            def rollback(self):
                pass
            def close(self):
                with owner.mutex:
                    for key, value in list(owner.slots.items()):
                        if value is self:
                            del owner.slots[key]
                self.closed = True
            def invalidate(self):
                self.invalidated = True
                self.close()
        connection = Connection()
        self.connections.append(connection)
        return connection


class ResourceAdmissionTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        cls.scope = ExitStack()
        cls.addClassCleanup(cls.scope.close)
        cls.scope.enter_context(patch.object(sys, "path", [str(Path(__file__).resolve().parents[1]), *sys.path]))
        cls.scope.enter_context(patch.dict(sys.modules))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        preserved = {key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
        cls.scope.enter_context(patch.dict(os.environ, {
            **preserved, "JWT_SECRET_KEY": "synthetic-test-secret", "GOOGLE_CLIENT_ID": "synthetic",
            "DEEPSEEK_API_KEY": "synthetic", "VOYAGE_API_KEY": "synthetic",
            "CLOUDINARY_URL": "cloudinary://synthetic:synthetic@synthetic",
            "DATALAB_API_KEY": "synthetic",
        }, clear=True))
        cls.scope.enter_context(patch("dotenv.load_dotenv", return_value=False))
        cls.scope.enter_context(patch("sqlalchemy.create_engine", side_effect=AssertionError("No database engines")))
        cls.scope.enter_context(patch("socket.create_connection", side_effect=AssertionError("No network")))
        cls.scope.enter_context(patch("redis.Redis.from_url", side_effect=AssertionError("No real Redis")))
        cls.scope.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("No providers")))
        cls.scope.enter_context(patch("cloudinary.uploader.upload", side_effect=AssertionError("No uploads")))
        cls.scope.enter_context(patch("openai.OpenAI"))
        from sqlalchemy.orm import DeclarativeBase
        database = ModuleType("app.database.database")
        class Base(DeclarativeBase):
            pass
        database.Base = Base
        database.engine = MagicMock(side_effect=AssertionError("No real database"))
        database.SessionLocal = MagicMock(side_effect=AssertionError("No real sessions"))
        database.get_db = lambda: None
        sys.modules[database.__name__] = database
        cls.database = database
        cls.limits = importlib.import_module("app.services.resource_limits")
        cls.admission = importlib.import_module("app.services.resource_admission")
        cls.auth = importlib.import_module("app.routes.auth")
        cls.dependencies = importlib.import_module("app.services.admission_dependencies")
        cls.quota = importlib.import_module("app.services.upload_quota_service")
        with patch("pathlib.Path.mkdir"):
            cls.documents = importlib.import_module("app.routes.documents")
        cls.chats = importlib.import_module("app.routes.chats")
        cls.summaries = importlib.import_module("app.routes.summaries")
        cls.assistant = importlib.import_module("app.routes.summary_assistant")
        cls.generation = importlib.import_module("app.services.summaries.summary_generation_service")
        cls.processing = importlib.import_module("app.services.document_processing_service")
        cls.queue = importlib.import_module("app.services.queued_message_service")

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.limits.resource_limits.cache_clear()
        self.admission.rate_backend.cache_clear()
        self.rates = SharedRates()
        self.permits = SharedPermits()
        self.stack.enter_context(patch.object(self.admission, "rate_backend", return_value=self.rates))
        self.stack.enter_context(patch.object(self.database, "engine", self.permits))

    def take(self, category, user=1):
        self.admission.consume_user_rate(user, category)

    def exhaust(self, category, user=1):
        for _ in range(self.limits.resource_limits().rates[category].limit):
            self.take(category, user)

    def test_under_limit_succeeds(self):
        for category in ("api", "chat", "search", "upload", "summary"):
            self.take(category)

    def test_ordinary_api_limit_returns_429_with_retry_after(self):
        self.exhaust("api")
        with self.assertRaises(self.admission.ResourceRejected) as error:
            self.take("api")
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.headers["Retry-After"], "60")

    def test_chat_is_independent_from_ordinary_api(self):
        self.exhaust("api")
        self.take("chat")
        self.exhaust("chat", user=2)
        self.take("api", user=2)

    def test_search_has_its_own_limit(self):
        self.exhaust("search")
        with self.assertRaises(self.admission.ResourceRejected):
            self.take("search")
        self.take("api")

    def test_upload_and_retry_share_hourly_policy(self):
        self.exhaust("upload")
        for name in ("upload_document", "retry_document_processing"):
            request = SimpleNamespace(method="POST", scope={"route": SimpleNamespace(name=name)})
            self.assertEqual(self.limits.request_policy(request), "upload")
            with self.assertRaises(self.admission.ResourceRejected) as error:
                self.take(self.limits.request_policy(request))
            self.assertEqual(error.exception.retry_after, 3600)

    def test_summary_and_transcription_share_hourly_policy(self):
        self.exhaust("summary")
        with self.assertRaises(self.admission.ResourceRejected):
            self.take("summary")
        self.take("chat")

    def test_user_isolation(self):
        self.exhaust("chat", 1)
        self.take("chat", 2)

    def test_sliding_window_expiry_allows_later_request(self):
        self.exhaust("chat")
        self.rates.now = 59.1
        with self.assertRaises(self.admission.ResourceRejected) as error:
            self.take("chat")
        self.assertEqual(error.exception.retry_after, 1)
        self.rates.now = 60
        self.take("chat")

    def test_multiple_clients_share_one_atomic_rate_boundary(self):
        def request(_):
            try:
                self.take("chat")
                return True
            except self.admission.ResourceRejected:
                return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(request, range(30))), 10)

    def test_two_chats_allowed_third_rejected(self):
        first = self.admission.acquire_permit(1, "chat")
        second = self.admission.acquire_permit(1, "chat")
        try:
            with self.assertRaises(self.admission.ResourceRejected):
                self.admission.acquire_permit(1, "chat")
            other = self.admission.acquire_permit(2, "chat")
            other.release()
        finally:
            first.release()
            second.release()
        self.assertEqual(self.permits.slots, {})

    def test_one_summary_allowed_second_rejected(self):
        with self.admission.user_operation(1, "summary"):
            with self.assertRaises(self.admission.ResourceRejected):
                self.admission.acquire_permit(1, "summary")
        self.assertEqual(self.permits.slots, {})

    def test_processing_limit_applies_across_different_documents(self):
        with self.admission.user_operation(1, "processing", rate=False):
            with self.assertRaises(self.admission.ResourceRejected):
                with self.admission.user_operation(1, "processing", rate=False):
                    self.fail("second processing admitted")

    def test_permit_released_on_success_failure_and_cancellation(self):
        for failure in (None, ValueError("synthetic"), asyncio.CancelledError()):
            try:
                with self.admission.user_operation(1, "chat"):
                    if failure:
                        raise failure
            except (ValueError, asyncio.CancelledError):
                pass
            self.assertEqual(self.permits.slots, {})

    def test_existing_permit_does_not_consume_internal_rate_or_slot(self):
        with self.admission.user_operation(1, "summary") as existing:
            calls = self.rates.calls
            with self.admission.user_operation(1, "summary", existing=existing):
                self.assertEqual(self.rates.calls, calls)
                self.assertEqual(len(self.permits.slots), 1)

    def test_background_title_retains_chat_permit_after_response(self):
        permit = self.admission.acquire_permit(1, "chat")
        permit.retain()
        permit.release()  # HTTP response finishes before the title future.
        self.assertEqual(len(self.permits.slots), 1)
        permit.release()  # Title future callback.
        self.assertEqual(self.permits.slots, {})

    def test_streaming_disconnect_holds_permit_until_provider_returns(self):
        permit = self.admission.acquire_permit(1, "chat")
        entered, finish = Event(), Event()
        closed = []
        def stream():
            try:
                entered.set()
                if not finish.wait(5):
                    raise AssertionError("provider not released")
                yield "late"
            finally:
                closed.append(True)
        iterator = self.admission.OwnedIterator(stream(), permit)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(next, iterator)
            try:
                self.assertTrue(entered.wait(5))
                iterator.close()
                permit.release()
                self.assertEqual(len(self.permits.slots), 1)
            finally:
                finish.set()
            future.result(timeout=5)
        self.assertEqual(closed, [True])
        self.assertEqual(self.permits.slots, {})

    def test_stream_chunks_do_not_consume_rate_units(self):
        with self.admission.user_operation(1, "chat") as permit:
            calls = self.rates.calls
            iterator = self.admission.OwnedIterator((str(i) for i in range(10)), permit)
            self.assertEqual(len(list(iterator)), 10)
            iterator.close()
            self.assertEqual(self.rates.calls, calls)

    def test_redis_outage_fails_closed_except_recovery(self):
        with patch.object(self.rates, "eval", side_effect=RuntimeError("redis://private-secret")):
            for category in ("api", "chat", "search", "upload", "summary"):
                with self.assertRaises(self.admission.AdmissionUnavailable) as error:
                    self.take(category)
                self.assertEqual(error.exception.status_code, 503)
                self.assertNotIn("private-secret", error.exception.detail)
            self.take("recovery")
            with self.assertRaises(self.admission.AdmissionUnavailable):
                self.admission.consume_rate("ip:127.0.0.1", "auth")

    def test_auth_ignores_forwarded_headers(self):
        from starlette.requests import Request
        request = Request({"type": "http", "client": ("203.0.113.4", 1234),
                           "headers": [(b"x-forwarded-for", b"198.51.100.1"), (b"forwarded", b"for=198.51.100.2")]})
        self.assertEqual(self.admission.authentication_subject(request), "ip:203.0.113.4")

    def test_invalid_user_or_lost_permit_never_starts_work(self):
        with self.assertRaises(self.admission.AdmissionUnavailable):
            self.admission.acquire_permit(None, "chat")
        permit = self.admission.acquire_permit(1, "chat")
        permit.connection.invalidate()
        with self.assertRaises(self.admission.AdmissionUnavailable):
            permit.check()
        permit.release()

    def test_uncertain_pg_acquisition_and_release_invalidate_connection(self):
        for outcomes in ((RuntimeError("private"),), (True, RuntimeError("private")), (True, False)):
            connection = MagicMock(closed=False, invalidated=False)
            connection.in_transaction.return_value = False
            connection.scalar.side_effect = outcomes
            with patch.object(self.database, "engine") as engine:
                engine.connect.return_value = connection
                try:
                    permit = self.admission.acquire_permit(1, "chat")
                    permit.release()
                except self.admission.AdmissionUnavailable:
                    pass
            connection.invalidate.assert_called_once()
            connection.close.assert_called_once()

    def test_original_file_byte_quota_and_document_count(self):
        settings = replace(self.limits.resource_limits(), max_documents=2, max_original_bytes=10)
        with tempfile.TemporaryDirectory() as folder, patch.object(self.quota, "resource_limits", return_value=settings):
            path = Path(folder) / "original.txt"
            path.write_bytes(b"123456")
            row = SimpleNamespace(id=1, file_path=str(path), file_size_bytes=None,
                                  processing_status="ready")
            self.quota.check_upload_quota([row], 4, upload_root=Path(folder))
            with self.assertRaises(self.admission.ResourceRejected) as error:
                self.quota.check_upload_quota([row], 5, upload_root=Path(folder))
            self.assertEqual(error.exception.code, "storage_quota")
            with self.assertRaises(self.admission.ResourceRejected) as error:
                self.quota.check_upload_quota([row, row], 1, upload_root=Path(folder))
            self.assertEqual(error.exception.code, "document_quota")

    def test_missing_legacy_original_is_conservatively_charged(self):
        settings = replace(self.limits.resource_limits(), max_original_bytes=10)
        with tempfile.TemporaryDirectory() as folder:
            row = SimpleNamespace(id=1, file_path=str(Path(folder) / "missing-original.txt"),
                                  file_size_bytes=None, processing_status="ready")
            with patch.object(self.quota, "resource_limits", return_value=settings), \
                 patch.object(self.quota, "upload_limits",
                              return_value=SimpleNamespace(file_bytes=10)):
                with self.assertRaises(self.admission.ResourceRejected) as error:
                    self.quota.check_upload_quota([row], 1, upload_root=Path(folder))
        self.assertEqual(error.exception.code, "storage_quota")

    def test_recorded_original_size_does_not_require_local_file(self):
        settings = replace(self.limits.resource_limits(), max_original_bytes=10)
        row = SimpleNamespace(id=1, file_path="missing-original.txt",
                              file_size_bytes=6, processing_status="ready")
        with patch.object(self.quota, "resource_limits", return_value=settings):
            self.quota.check_upload_quota([row], 4)
            with self.assertRaises(self.admission.ResourceRejected) as error:
                self.quota.check_upload_quota([row], 5)
        self.assertEqual(error.exception.code, "storage_quota")

    def test_retry_cannot_bypass_processing_reservation(self):
        rows = [SimpleNamespace(id=i, file_path=None, processing_status="processing") for i in (1, 2)]
        self.quota.check_upload_quota(rows[:1], retry_document_id=1)
        with self.assertRaises(self.admission.ResourceRejected):
            self.quota.check_upload_quota(rows, retry_document_id=1)

    def test_upload_serialization_rejects_competing_reservation(self):
        with self.admission.user_operation(1, "upload_quota", rate=False):
            with self.assertRaises(self.admission.ResourceRejected):
                with self.admission.user_operation(1, "upload_quota", rate=False):
                    self.fail("concurrent upload quota check admitted")

    def test_configurable_limits_are_central_and_positive(self):
        with patch.dict(os.environ, {"RESOURCE_CHAT_LIMIT": "4", "RESOURCE_CHAT_CONCURRENCY": "3"}):
            self.limits.resource_limits.cache_clear()
            self.assertEqual(self.limits.resource_limits().rates["chat"].limit, 4)
            self.assertEqual(self.limits.resource_limits().concurrency["chat"], 3)
        with patch.dict(os.environ, {"RESOURCE_CHAT_LIMIT": "0"}):
            self.limits.resource_limits.cache_clear()
            with self.assertRaises(ValueError):
                self.limits.resource_limits()

    def test_production_has_no_silent_local_or_memory_fallback(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            self.limits.resource_limits.cache_clear()
            self.assertIsNone(self.limits.resource_limits().redis_url)

    def http_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        for module in (self.auth, self.chats, self.documents, self.summaries, self.assistant):
            app.include_router(module.router)
        app.add_exception_handler(self.admission.ResourceRejected, self.admission.resource_error_response)
        self.db = MagicMock()
        self.db.get.return_value = SimpleNamespace(
            id=1, email="synthetic@example.invalid", name=None, picture=None,
            created_at=datetime.now(timezone.utc),
        )
        app.dependency_overrides[self.database.get_db] = lambda: self.db
        self.stack.enter_context(patch.object(self.auth, "decode_access_token", return_value={"sub": "1"}))
        return self.stack.enter_context(TestClient(app, headers={"Authorization": "Bearer synthetic"}))

    def test_http_429_has_safe_machine_readable_body_and_headers(self):
        client = self.http_client()
        self.exhaust("api")
        response = client.get("/auth/me")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "rate_limit")
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertIsInstance(response.json()["detail"], str)
        self.assertNotIn("resource:v1", response.text)

    def test_http_under_limit_succeeds_and_consumes_once(self):
        client = self.http_client()
        self.assertEqual(client.get("/auth/me").status_code, 200)
        self.assertEqual(self.rates.calls, 1)

    def test_http_search_upload_and_summary_reject_before_expensive_work(self):
        client = self.http_client()
        for category in ("search", "upload", "summary"):
            self.exhaust(category)
        with patch.object(self.chats, "search_similar_chunks") as search, \
             patch.object(self.documents, "validate_file_content") as upload, \
             patch.object(self.documents, "dispatch_uploaded_document") as dispatch, \
             patch.object(self.summaries, "generate_summary_for_record") as summary, \
             patch.object(self.summaries, "start_summary_generation") as stream:
            responses = [
                client.post("/chats/1/search", json={"query": "Synthetic"}),
                client.post("/documents", files={"file": ("synthetic.txt", b"Synthetic", "text/plain")}),
                client.post("/documents/1/summaries/generate", json={"chat_id": 1}),
                client.post("/documents/1/summaries/generate/stream", json={"chat_id": 1, "mode": "transcription"}),
            ]
            self.assertEqual([response.status_code for response in responses], [429] * 4)
            for provider in (search, upload, dispatch, summary, stream):
                provider.assert_not_called()

    def test_http_concurrency_rejects_before_chat_or_summary_setup(self):
        client = self.http_client()
        with self.admission.user_operation(1, "chat", rate=False), \
             self.admission.user_operation(1, "chat", rate=False), \
             self.admission.user_operation(1, "summary", rate=False), \
             patch.object(self.chats, "detect_chat_intent") as intent, \
             patch.object(self.summaries, "get_chat_document") as lookup:
            responses = [
                client.post("/chats/1/ask/stream", json={"question": "Synthetic"}),
                client.post("/documents/1/summaries/generate/stream", json={"chat_id": 1}),
            ]
            for response in responses:
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.json()["code"], "concurrency_limit")
                self.assertEqual(response.headers["Retry-After"], "5")
            intent.assert_not_called()
            lookup.assert_not_called()

    def test_http_backend_outage_returns_safe_503(self):
        client = self.http_client()
        with patch.object(self.rates, "eval", side_effect=RuntimeError("private Redis secret")):
            response = client.post("/chats/1/ask", json={"question": "Synthetic"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "admission_unavailable")
        self.assertEqual(response.headers["Retry-After"], "5")
        self.assertNotIn("private", response.text)

    def test_authentication_limit_blocks_oauth_provider_and_ignores_spoofed_ip(self):
        client = self.http_client()
        # TestClient's non-IP peer maps to the conservative unknown bucket.
        for _ in range(self.limits.resource_limits().rates["auth"].limit):
            self.admission.consume_rate("ip:unknown", "auth")
        with patch.object(self.auth, "verify_google_token") as verify, \
             patch.object(self.auth, "exchange_google_code") as exchange:
            responses = [
                client.post("/auth/google", json={"credential": "synthetic-token"}, headers={"X-Forwarded-For": "198.51.100.1"}),
                client.get("/auth/google/start", follow_redirects=False),
                client.get("/auth/google/callback?code=synthetic&state=synthetic", follow_redirects=False),
                client.post("/auth/logout"),
            ]
            self.assertEqual([response.status_code for response in responses], [429] * 4)
            verify.assert_not_called()
            exchange.assert_not_called()

    def test_under_limit_google_login_and_oauth_start_keep_response_contract(self):
        client = self.http_client()
        with patch.object(self.auth, "verify_google_token", return_value={"sub": "synthetic"}) as verify, \
             patch.object(self.auth, "get_or_create_user", return_value=self.db.get.return_value), \
             patch.object(self.auth, "create_access_token", return_value="synthetic-token"), \
             patch.object(self.auth, "GOOGLE_REDIRECT_URI", "http://test/auth/google/callback"):
            login = client.post("/auth/google", json={"credential": "synthetic-credential"})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.json()["user"]["id"], 1)
            self.assertIn(self.auth.AUTH_COOKIE_NAME, login.cookies)
            verify.assert_called_once_with("synthetic-credential")
            start = client.get("/auth/google/start", follow_redirects=False)
            self.assertEqual(start.status_code, 302)
            self.assertTrue(start.headers["location"].startswith("https://accounts.google.com/"))
            self.assertIn(self.auth.GOOGLE_OAUTH_STATE_COOKIE, start.cookies)
        self.assertEqual(self.rates.calls, 2)

    def test_asgi_stream_keeps_request_permit_until_cleanup(self):
        from fastapi import Depends
        from starlette.requests import ClientDisconnect
        app = self.http_client().app
        closed, chunks = [], []
        failure = None
        def content():
            try:
                yield "first"
                if failure == "provider":
                    raise ValueError("synthetic provider failure")
                yield "second"
            finally:
                closed.append(True)
        @app.post("/_admission-stream", name="ask_chat_stream")
        def endpoint(permit=Depends(self.dependencies.admit_chat, scope="request")):
            return self.admission.AdmittedStreamingResponse(content(), permit)
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                self.assertEqual(len(self.permits.slots), 1)
                chunks.append(message["body"])
                if failure == "disconnect":
                    raise OSError("synthetic disconnect")
                if failure == "cancel":
                    raise asyncio.CancelledError()
        scope = {"type": "http", "asgi": {"spec_version": "2.4"}, "http_version": "1.1",
                 "method": "POST", "path": "/_admission-stream", "root_path": "",
                 "query_string": b"", "headers": [(b"authorization", b"Bearer synthetic")],
                 "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1)}
        for failure, expected in ((None, None), ("provider", ValueError),
                                  ("disconnect", ClientDisconnect), ("cancel", asyncio.CancelledError)):
            with self.subTest(failure=failure):
                before = self.rates.calls
                if expected:
                    with self.assertRaises(expected):
                        asyncio.run(app(dict(scope), receive, send))
                else:
                    asyncio.run(app(dict(scope), receive, send))
                self.assertEqual(self.permits.slots, {})
                self.assertEqual(self.rates.calls, before + 1)
        self.assertEqual(len(closed), 4)
        self.assertEqual(len(chunks), 5)

    def test_over_limit_chat_stops_before_intent_title_or_provider(self):
        client = self.http_client()
        self.exhaust("chat")
        with patch.object(self.chats, "detect_chat_intent") as intent, \
             patch.object(self.chats, "maybe_generate_chat_title") as title, \
             patch.object(self.chats, "answer_question") as answer:
            for route in ("ask", "ask/stream"):
                response = client.post(f"/chats/1/{route}", json={"question": "Synthetic", "allow_general_knowledge": True})
                self.assertEqual(response.status_code, 429)
            intent.assert_not_called()
            title.assert_not_called()
            answer.assert_not_called()

    def answer_generator(self, provider):
        llm = importlib.import_module("app.services.llm_service")
        client = MagicMock()
        client.chat.completions.create.return_value = provider
        self.stack.enter_context(patch.object(llm, "client", client))
        return llm.generate_answer_stream("Synthetic question", "", [], True)

    def provider_stream(self, failure=None):
        def chunks():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="first"))])
            if failure:
                raise failure
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="second"))])
        provider = MagicMock()
        provider.__iter__.side_effect = chunks
        return provider

    def test_answer_sdk_stream_closes_once_on_completion_close_throw_and_iteration_failure(self):
        for mode in ("complete", "close", "throw", "provider_failure"):
            with self.subTest(mode=mode):
                error = ValueError("synthetic private provider marker")
                provider = self.provider_stream(error if mode == "provider_failure" else None)
                answer = self.answer_generator(provider)
                self.assertEqual(next(answer), "first")
                provider.close.assert_not_called()
                if mode == "complete":
                    self.assertEqual(list(answer), ["second"])
                elif mode == "close":
                    answer.close()
                else:
                    with self.assertRaises(ValueError) as caught:
                        answer.throw(error) if mode == "throw" else next(answer)
                    self.assertIs(caught.exception, error)
                answer.close()
                answer.close()
                provider.close.assert_called_once_with()

    def test_answer_sdk_cleanup_error_preserves_original_failure_and_logs_no_payload(self):
        error = ValueError("synthetic private provider marker")
        provider = self.provider_stream(error)
        provider.close.side_effect = RuntimeError("private cleanup secret")
        answer = self.answer_generator(provider)
        next(answer)
        with self.assertLogs("app.services.llm_service", level="ERROR") as captured:
            with self.assertRaises(ValueError) as caught:
                next(answer)
        self.assertIs(caught.exception, error)
        self.assertNotIn("private", str(captured.records[0].__dict__))
        self.assertEqual(captured.records[0].operation, "answer_stream_cleanup")
        answer.close()
        provider.close.assert_called_once_with()

    def test_answer_sdk_stream_closes_after_inflight_read_on_disconnect(self):
        entered, finish = Event(), Event()
        provider = self.provider_stream()
        def chunks():
            entered.set()
            if not finish.wait(5):
                raise AssertionError("Mock provider not released")
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="late"))])
        provider.__iter__.side_effect = chunks
        permit = self.admission.acquire_permit(1, "chat")
        iterator = self.admission.OwnedIterator(self.answer_generator(provider), permit)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(next, iterator)
            try:
                self.assertTrue(entered.wait(5))
                iterator.close()
                permit.release()
                self.assertEqual(len(self.permits.slots), 1)
                provider.close.assert_not_called()
            finally:
                finish.set()
            future.result(timeout=5)
        iterator.close()
        provider.close.assert_called_once_with()
        self.assertEqual(self.permits.slots, {})

    def test_actual_chat_response_closes_sdk_on_normal_disconnect_cancel_and_provider_failure(self):
        from starlette.requests import ClientDisconnect
        for mode in ("complete", "disconnect", "cancel", "provider_failure"):
            with self.subTest(mode=mode):
                error = ValueError("synthetic private provider marker")
                provider = self.provider_stream(error if mode == "provider_failure" else None)
                answer = self.answer_generator(provider)
                db, stream_db = MagicMock(), MagicMock()
                db.refresh.side_effect = lambda row: setattr(row, "id", 13)
                future = Future()
                future.set_result(None)
                events = []
                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}
                async def send(message):
                    if message["type"] == "http.response.body" and message.get("body"):
                        event = json.loads(message["body"])
                        events.append(event)
                        if event["type"] == "token":
                            if mode == "disconnect":
                                raise OSError("synthetic disconnect")
                            if mode == "cancel":
                                raise asyncio.CancelledError()
                with patch.object(self.chats, "get_owned_chat", return_value=SimpleNamespace(id=1, documents=[])), \
                     patch.object(self.chats, "SessionLocal", return_value=stream_db), \
                     patch.object(self.chats, "ThreadPoolExecutor"), \
                     patch.object(self.chats, "submit_observed", return_value=future), \
                     patch.object(self.chats, "generate_answer_stream", return_value=answer), \
                     patch.object(self.chats, "prepare_answer_context", return_value={
                         "immediate_answer": None, "context": "", "conversation_history": [],
                         "candidate_sources": [], "mode": "general",
                     }), self.admission.user_operation(1, "chat", rate=False) as permit:
                    response = self.chats.ask_chat_stream(1, self.chats.AskRequest(
                        question="Synthetic question", allow_general_knowledge=True,
                    ), db, SimpleNamespace(id=1), permit)
                    scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
                    expected = {"disconnect": ClientDisconnect, "cancel": asyncio.CancelledError}.get(mode)
                    if expected:
                        with self.assertRaises(expected):
                            asyncio.run(response(scope, receive, send))
                    else:
                        asyncio.run(response(scope, receive, send))
                    response.owned_iterator.close()
                    response.owned_iterator.close()
                provider.close.assert_called_once_with()
                stream_db.close.assert_called_once_with()
                self.assertEqual(self.permits.slots, {})
                self.assertEqual(any(e["type"] == "done" for e in events), mode == "complete")
                self.assertNotIn("private provider", str(events))
                if mode == "provider_failure":
                    self.assertEqual(events[-1]["type"], "error")

    def test_http_retry_over_limit_stops_before_dispatch(self):
        client = self.http_client()
        self.exhaust("upload")
        with patch.object(self.documents, "dispatch_uploaded_document") as dispatch:
            response = client.post("/documents/1/retry")
        self.assertEqual(response.status_code, 429)
        dispatch.assert_not_called()

    def test_summary_assistant_is_classified_as_paid_chat(self):
        client = self.http_client()
        self.exhaust("chat")
        with patch.object(self.assistant, "send_summary_assistant_message") as provider:
            response = client.post("/documents/1/summary-assistant/messages", json={"chat_id": 1, "content": "Instructions"})
        self.assertEqual(response.status_code, 429)
        provider.assert_not_called()


def test_resource_admission_in_isolated_process():
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
