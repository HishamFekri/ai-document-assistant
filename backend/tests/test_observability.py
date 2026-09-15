"""Batch 14 offline checks, using the existing blocked-network/DB import harness."""

import asyncio
from contextlib import ExitStack
import importlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import test_resource_admission as harness


SECRET = "Bearer secret.jwt cookie=password document text prompt provider payload"


class ObservabilityTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        harness.ResourceAdmissionTests.setUpClass.__func__(cls)
        cls.obs = importlib.import_module("app.services.observability")
        cls.ready = importlib.import_module("app.services.readiness")
        cls.runtime = importlib.import_module("app.services.runtime_config")
        cls.config = importlib.import_module("app.services.auth_config")
        cls.worker = importlib.import_module("app.worker")
        with patch.object(cls.obs, "configure_logging"):
            cls.main = importlib.import_module("main")

    def setUp(self):
        harness.ResourceAdmissionTests.setUp(self)
        self.obs.install_observers()
        self.addCleanup(self.obs.install_observers)
        self.ready._cached, self.ready._expires = None, 0
        self.config.auth_settings.cache_clear()
        self.addCleanup(self.config.auth_settings.cache_clear)
        self.output = io.StringIO()
        self.handler = logging.StreamHandler(self.output)
        self.handler.setFormatter(self.obs.SafeJSONFormatter())
        self.logger = logging.getLogger("batch14_test")
        self.logger.handlers = [self.handler]
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.addCleanup(self.logger.handlers.clear)

    def test_structured_fields_drop_all_unknown_payloads(self):
        identifier = str(uuid4())
        with self.obs.log_context(request_id=identifier, document_id=7):
            self.obs.log_event(self.logger, logging.ERROR, "operation_failed", operation="summary",
                               summary_id=9, token=SECRET, headers={"Authorization": SECRET},
                               prompt=SECRET, document_text=SECRET, provider_payload=SECRET,
                               exception_type="builtins.RuntimeError")
        row = json.loads(self.output.getvalue())
        self.assertEqual((row["event"], row["document_id"], row["summary_id"]), ("operation_failed", 7, 9))
        self.assertEqual(row["request_id"], identifier)
        self.assertNotIn(SECRET, self.output.getvalue())
        self.assertNotIn("token", row)
        self.assertEqual(self.obs.current_context(), {})

    def test_library_logs_never_format_messages_arguments_or_tracebacks(self):
        class Unprintable(Exception):
            def __str__(self):
                raise AssertionError("Must never stringify exception")
        try:
            raise Unprintable(SECRET)
        except Unprintable:
            self.logger.error(SECRET, object(), exc_info=True, stack_info=True,
                              extra={"cookies": SECRET, "provider": SECRET})
        row = json.loads(self.output.getvalue())
        self.assertEqual(row["event"], "library_log")
        self.assertTrue(row["exception_type"].endswith("Unprintable"))
        self.assertNotIn(SECRET, self.output.getvalue())
        self.assertNotIn("Traceback", self.output.getvalue())

    def test_error_adapter_only_gets_safe_copies_and_cannot_break_logging(self):
        observed = []
        def adapter(fields):
            observed.append(dict(fields))
            fields["token"] = SECRET
            raise RuntimeError(SECRET)
        self.obs.install_observers(error_reporter=adapter)
        self.obs.log_exception(self.logger, "summary", ValueError(SECRET), document_id=2)
        self.assertEqual(observed[0]["exception_type"], "builtins.ValueError")
        self.assertNotIn(SECRET, self.output.getvalue())
        self.assertNotIn("token", json.loads(self.output.getvalue()))

    def test_malformed_library_extras_cannot_trigger_raw_logging_error_output(self):
        record = logging.LogRecord("library", logging.ERROR, "private/path", 1, SECRET, (), None)
        record.safe_event = None
        self.assertEqual(json.loads(self.obs.SafeJSONFormatter().format(record))["event"], "logging_record_rejected")
        self.assertEqual(self.obs.safe_fields({"document_id": 10**1000, "method": [], "duration_ms": float("nan")}), {})

    def test_batch4_diagnostics_survive_structured_formatter(self):
        errors = importlib.import_module("app.services.error_service")
        observed = []
        self.obs.install_observers(error_reporter=observed.append)
        with self.assertLogs(errors.logger, level="ERROR") as captured:
            try:
                raise RuntimeError(SECRET)
            except RuntimeError as error:
                public = errors.log_generation_failure(error, "summary", document_id=7, summary_id=9)
        row = json.loads(self.obs.SafeJSONFormatter().format(captured.records[0]))
        self.assertEqual(public, errors.GENERATION_FAILED["summary"])
        self.assertEqual(row["job_id"], self.obs.summary_job_id(9))
        self.assertTrue(row["diagnostics"][0]["frames"])
        self.assertNotIn(SECRET, json.dumps(row))
        self.assertNotIn("diagnostics", observed[0])
        self.assertEqual(observed[0]["exception_type"], "builtins.RuntimeError")

    def test_logging_setup_covers_uvicorn_celery_sqlalchemy_and_http_clients(self):
        names = ["uvicorn.error", "uvicorn.access", "celery.task", "sqlalchemy.engine", "httpx", "openai"]
        with ExitStack() as stack:
            for target in [logging.getLogger(), *[logging.getLogger(n) for n in names]]:
                stack.enter_context(patch.object(target, "handlers", list(target.handlers)))
                stack.enter_context(patch.object(target, "propagate", target.propagate))
                stack.enter_context(patch.object(target, "level", target.level))
            stack.enter_context(patch.object(sys, "stdout", self.output))
            self.obs.configure_logging()
            for name in names:
                logging.getLogger(name).error(SECRET)
        rows = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual(len(rows), len(names))
        self.assertNotIn(SECRET, self.output.getvalue())

    def test_liveness_is_unchanged_and_does_not_probe_dependencies(self):
        from fastapi.testclient import TestClient
        with patch.object(self.main, "readiness_status", side_effect=AssertionError("No probes")), \
                TestClient(self.main.app) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIsNotNone(self.obs.valid_id(response.headers["x-request-id"]))

    def test_readiness_http_has_safe_503_and_no_cache(self):
        from fastapi.testclient import TestClient
        with patch.object(self.ready, "check_database", side_effect=RuntimeError(SECRET)), \
                patch.object(self.ready, "check_redis"), TestClient(self.main.app) as client:
            response = client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["database"], "unavailable")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn(SECRET, response.text)

    def test_request_id_survives_origin_rejection_and_is_exposed_to_browser(self):
        from fastapi.testclient import TestClient
        with TestClient(self.main.app) as client:
            denied = client.post("/auth/logout", headers={"Origin": "https://untrusted.invalid"})
            allowed = client.get("/health", headers={"Origin": self.main.allowed_origins[0]})
        self.assertEqual(denied.status_code, 403)
        self.assertIn("x-request-id", denied.headers)
        self.assertIn("X-Request-ID", allowed.headers["access-control-expose-headers"])

    def test_unhandled_500_has_request_id_without_raw_exception(self):
        from fastapi.testclient import TestClient
        app = self.main.ObservedFastAPI()
        @app.get("/failure")
        def failure():
            raise RuntimeError(SECRET)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/failure")
        self.assertEqual(response.status_code, 500)
        self.assertIn("x-request-id", response.headers)
        self.assertNotIn(SECRET, response.text)

    def test_readiness_caches_success_failure_and_recovers(self):
        with patch.object(self.ready, "check_database") as database, \
                patch.object(self.ready, "check_redis") as redis, \
                patch.object(self.ready, "monotonic", return_value=10) as clock:
            self.assertEqual(self.ready.readiness_status()[1], 200)
            self.assertEqual(self.ready.readiness_status()[1], 200)
            database.assert_called_once(); redis.assert_called_once()
            clock.return_value = 16
            database.side_effect = RuntimeError(SECRET)
            self.assertEqual(self.ready.readiness_status()[1], 503)
            self.assertEqual(self.ready.readiness_status()[1], 503)
            self.assertEqual(database.call_count, 2)
            database.side_effect = None; clock.return_value = 22
            self.assertEqual(self.ready.readiness_status()[1], 200)

    def test_readiness_inflight_probe_does_not_queue_connections(self):
        with self.ready._lock, patch.object(self.ready, "check_database") as probe:
            self.assertEqual(self.ready.readiness_status()[1], 503)
            probe.assert_not_called()

    def test_readiness_checks_required_queue_dependencies_and_deduplicates(self):
        with patch.dict(os.environ, {"TASK_QUEUE": "celery", "CELERY_BROKER_URL": "redis://queue/0",
                                    "CELERY_RESULT_BACKEND": "redis://queue/0",
                                    "RESOURCE_REDIS_URL": "redis://admission/0"}), \
                patch.object(self.ready, "check_database"), patch.object(self.ready, "check_redis") as redis:
            body, code = self.ready.readiness_status()
        self.assertEqual(code, 200)
        self.assertEqual(set(body["checks"]), {"database", "resource_redis", "celery_broker", "celery_backend"})
        self.assertEqual(redis.call_count, 2)

    def test_database_probe_uses_bounded_read_only_connection_and_closes(self):
        import psycopg
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://test:synthetic@127.0.0.1/test"}), \
                patch.object(psycopg, "connect") as connect:
            connection = connect.return_value.__enter__.return_value
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (1,)
            self.ready.check_database()
            self.assertEqual(connect.call_args.kwargs["dbname"], "test")
            self.assertEqual(connect.call_args.kwargs["connect_timeout"], 2)
            self.assertIn("statement_timeout=2000", connect.call_args.kwargs["options"])
            self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])
            cursor.execute.assert_called_once_with("SELECT 1")
            connect.return_value.__exit__.assert_called_once()
            cursor.execute.side_effect = RuntimeError(SECRET)
            with self.assertRaises(RuntimeError):
                self.ready.check_database()
            self.assertEqual(connect.return_value.__exit__.call_count, 2)

    def test_redis_probe_has_no_retries_and_closes_on_failure(self):
        import redis
        with patch.object(redis.Redis, "from_url") as factory:
            factory.return_value.ping.side_effect = RuntimeError(SECRET)
            with self.assertRaises(RuntimeError):
                self.ready.check_redis("redis://synthetic/0")
            self.assertEqual(factory.call_args.kwargs["socket_timeout"], 2)
            self.assertEqual(factory.call_args.kwargs["retry"].get_retries(), 0)
            factory.return_value.close.assert_called_once()
            factory.return_value.connection_pool.disconnect.assert_called_once()
        # Construct the real pool without connecting; prove URL query arguments
        # cannot override the probe's timeouts/retries after redis-py parses them.
        pool = redis.ConnectionPool.from_url(
            "redis://synthetic/0?socket_timeout=90&socket_connect_timeout=90&retry_on_timeout=True")
        client = redis.Redis(connection_pool=pool)
        with patch.object(redis.Redis, "from_url", return_value=client), patch.object(client, "ping", return_value=True):
            self.ready.check_redis("redis://synthetic/0")
        self.assertEqual(pool.connection_kwargs["socket_timeout"], 2)
        self.assertEqual(pool.connection_kwargs["socket_connect_timeout"], 2)
        self.assertFalse(pool.connection_kwargs["retry_on_timeout"])
        self.assertEqual(pool.connection_kwargs["retry"].get_retries(), 0)

    def test_document_job_binds_id_through_processing_and_restores_context(self):
        captured = []
        @self.obs.document_job
        def work(document_id):
            captured.append(self.obs.current_context())
            return "completed"
        request_id, job_id = str(uuid4()), str(uuid4())
        with self.obs.log_context(request_id=request_id, job_id=job_id):
            self.assertEqual(work(17), "completed")
            self.assertNotIn("document_id", self.obs.current_context())
        self.assertEqual(captured[0]["document_id"], 17)
        self.assertEqual(captured[0]["job_id"], job_id)
        self.assertEqual(captured[0]["request_id"], request_id)
        self.assertEqual(self.obs.current_context(), {})

    def test_celery_publish_and_worker_preserve_only_valid_correlation_ids(self):
        request_id, correlation_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
        headers = {}
        with self.obs.log_context(request_id=request_id, correlation_id=correlation_id):
            self.worker.correlate_document_task(sender=self.worker.process_document_task.name, headers=headers)
        self.assertEqual(headers, {"request_id": request_id, "correlation_id": correlation_id})
        task = self.worker.process_document_task
        task.push_request(id=job_id, headers=headers, retries=0)
        observed = []
        try:
            with patch.object(self.worker, "_process_document_task", side_effect=lambda *a: observed.append(self.obs.current_context())):
                task.run(7, "private-filename.txt")
        finally:
            task.pop_request()
        self.assertEqual(observed[0]["job_id"], job_id)
        self.assertEqual(observed[0]["request_id"], request_id)
        self.assertNotIn("private-filename", str(observed))
        self.assertEqual(self.obs.current_context(), {})

    def test_explicit_thread_jobs_inherit_ids_without_leaking_to_next_job(self):
        from concurrent.futures import ThreadPoolExecutor
        identifier = str(uuid4())
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.obs.log_context(request_id=identifier, document_id=7):
                result = self.obs.submit_observed(executor, self.obs.current_context).result()
            self.assertEqual(result["request_id"], identifier)
            self.assertEqual(result["document_id"], 7)
            self.assertEqual(executor.submit(self.obs.current_context).result(), {})

    def test_runtime_rejects_bad_production_settings_without_echoing_values(self):
        values = {"ENVIRONMENT": "production", "FRONTEND_URL": "https://frontend.example.test",
                  "FRONTEND_URLS": "https://frontend.example.test", "TASK_QUEUE": "celery",
                  "DATABASE_URL": "postgresql+psycopg://test:synthetic@127.0.0.1/app",
                  "CELERY_BROKER_URL": "redis://127.0.0.1/0", "CELERY_RESULT_BACKEND": "redis://127.0.0.1/1",
                  "JWT_SECRET_KEY": "synthetic-long-deployment-secret-" * 2}
        with patch.dict(os.environ, values):
            self.runtime.validate_runtime()
            for name, value in [("TASK_QUEUE", "background"), ("DATABASE_URL", SECRET),
                                ("CELERY_BROKER_URL", SECRET), ("CELERY_RESULT_BACKEND", ""),
                                ("JWT_SECRET_KEY", "short")]:
                with patch.dict(os.environ, {name: value}), self.assertRaises(ValueError) as caught:
                    self.runtime.validate_runtime()
                self.assertNotIn(SECRET, str(caught.exception))

    def test_runtime_keeps_proxy_disabled_and_validates_port(self):
        with patch.dict(os.environ, {"PORT": "8123", "FORWARDED_ALLOW_IPS": "*"}):
            args = self.runtime.server_arguments()
            self.assertIn("--no-proxy-headers", args)
            self.assertIn("--no-access-log", args)
            self.assertEqual(args[args.index("--port") + 1], "8123")
        for port in ("0", "65536", SECRET):
            with patch.dict(os.environ, {"PORT": port}), self.assertRaises(ValueError):
                self.runtime.server_arguments()

    def test_normalized_environment_enforces_runtime_and_resource_security_consistently(self):
        values = {"FRONTEND_URL": "https://frontend.example.test", "FRONTEND_URLS": "https://frontend.example.test",
                  "GOOGLE_REDIRECT_URI": "https://frontend.example.test/auth/google/callback", "TASK_QUEUE": "celery",
                  "DATABASE_URL": "postgresql+psycopg://test:synthetic@127.0.0.1/app",
                  "CELERY_BROKER_URL": "redis://127.0.0.1/0", "CELERY_RESULT_BACKEND": "redis://127.0.0.1/1",
                  "JWT_SECRET_KEY": "synthetic-long-deployment-secret-" * 2}
        for environment in ("production", " production ", "PRODUCTION", "ProDuction", "staging", " StAgInG "):
            with self.subTest(environment=environment), patch.dict(os.environ, {**values, "ENVIRONMENT": environment}):
                self.config.auth_settings.cache_clear()
                self.limits.resource_limits.cache_clear()
                self.assertTrue(self.config.auth_settings().secure)
                self.runtime.validate_runtime()
                for name, value in (("TASK_QUEUE", "background"), ("JWT_SECRET_KEY", "short"),
                                    ("DATABASE_URL", SECRET), ("CELERY_RESULT_BACKEND", "")):
                    with patch.dict(os.environ, {name: value}), self.assertRaises(ValueError) as caught:
                        self.runtime.validate_runtime()
                    self.assertNotIn(SECRET, str(caught.exception))
                with patch.dict(os.environ, {"RESOURCE_REDIS_URL": "", "CELERY_BROKER_URL": ""}):
                    self.limits.resource_limits.cache_clear()
                    self.assertIsNone(self.limits.resource_limits().redis_url)
                    with self.assertRaises(ValueError):
                        self.runtime.validate_runtime()
        for environment in ("development", " DeVeLoPmEnT ", "test", " TEST "):
            with self.subTest(environment=environment), patch.dict(os.environ, {
                "ENVIRONMENT": environment, "TASK_QUEUE": "background", "RESOURCE_REDIS_URL": "", "CELERY_BROKER_URL": "",
            }):
                self.config.auth_settings.cache_clear()
                self.limits.resource_limits.cache_clear()
                self.runtime.validate_runtime()
                self.assertFalse(self.config.auth_settings().secure)
                self.assertEqual(self.limits.resource_limits().redis_url, "redis://127.0.0.1:6379/2")

    def test_invalid_environment_fails_consistently_without_echoing_input(self):
        for environment in ("", "  ", SECRET, "prod"):
            with self.subTest(environment=environment), patch.dict(os.environ, {"ENVIRONMENT": environment}):
                self.config.auth_settings.cache_clear()
                self.limits.resource_limits.cache_clear()
                for check in (self.config.auth_settings, self.limits.resource_limits, self.runtime.validate_runtime):
                    with self.assertRaises(RuntimeError) as caught:
                        check()
                    self.assertNotIn(SECRET, str(caught.exception))

    def test_worker_configuration_failure_escapes_celery_signal_dispatch(self):
        from celery import signals
        with patch.object(self.worker, "validate_runtime", side_effect=ValueError(SECRET)):
            with self.assertRaises(SystemExit) as caught:
                signals.worker_init.send(sender=object())
        self.assertEqual(caught.exception.code, 1)

    def test_stream_instrumentation_is_incremental_and_has_no_payloads_or_id_labels(self):
        observed, emitted = [], []
        self.obs.install_observers(request_observer=observed.append)
        correlation = str(uuid4())
        async def run():
            async def application(scope, receive, send):
                scope["route"] = SimpleNamespace(name="ask_chat_stream")
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": SECRET.encode(), "more_body": True})
                self.assertEqual(len(emitted), 2)  # No buffering until EOF.
                await send({"type": "http.response.body", "body": b"done", "more_body": False})
            async def receive():
                raise AssertionError("Middleware must not consume request bodies")
            async def send(message):
                emitted.append(message)
            await self.obs.RequestObservabilityMiddleware(application)(
                {"type": "http", "method": "POST", "path": "/chat/private", "query_string": SECRET.encode(),
                 "headers": [(b"authorization", SECRET.encode()), (b"x-correlation-id", correlation.encode())]}, receive, send)
        asyncio.run(run())
        self.assertEqual(observed[0]["outcome"], "complete")
        self.assertEqual(observed[0]["operation"], "ask_chat_stream")
        self.assertEqual(observed[0]["response_bytes"], len(SECRET.encode()) + 4)
        self.assertNotIn("request_id", observed[0])
        self.assertNotIn(SECRET, str(observed))

    def test_concurrent_request_context_isolated_and_untrusted_ids_replaced(self):
        contexts = []
        async def run():
            ready = asyncio.Event()
            async def app(scope, receive, send):
                before = self.obs.current_context()
                if len(contexts) == 0:
                    contexts.append(before); await ready.wait()
                else:
                    contexts.append(before); ready.set()
                self.assertEqual(before, self.obs.current_context())
                await send({"type": "http.response.start", "status": 204})
                await send({"type": "http.response.body", "body": b""})
            async def unused():
                raise AssertionError()
            async def send(message):
                pass
            middleware = self.obs.RequestObservabilityMiddleware(app)
            scope = {"type": "http", "method": "GET", "headers": [(b"x-correlation-id", SECRET.encode())]}
            await asyncio.gather(middleware(dict(scope), unused, send), middleware(dict(scope), unused, send))
            self.assertEqual(self.obs.current_context(), {})
        asyncio.run(run())
        self.assertNotEqual(contexts[0]["request_id"], contexts[1]["request_id"])
        self.assertNotIn(SECRET, str(contexts))

    def test_disconnect_and_cancellation_are_observed_without_swallowing_cleanup(self):
        for cancel in (False, True):
            observed, cleaned = [], []
            self.obs.install_observers(request_observer=observed.append)
            async def run():
                async def app(scope, receive, send):
                    try:
                        if cancel:
                            raise asyncio.CancelledError()
                        self.assertEqual((await receive())["type"], "http.disconnect")
                    finally:
                        cleaned.append(True)
                async def receive():
                    return {"type": "http.disconnect"}
                async def send(message):
                    pass
                await self.obs.RequestObservabilityMiddleware(app)({"type": "http", "method": "GET"}, receive, send)
            if cancel:
                with self.assertRaises(asyncio.CancelledError):
                    asyncio.run(run())
            else:
                asyncio.run(run())
            self.assertEqual(cleaned, [True])
            self.assertEqual(observed[0]["outcome"], "incomplete" if cancel else "disconnected")


def test_observability_in_isolated_process():
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
