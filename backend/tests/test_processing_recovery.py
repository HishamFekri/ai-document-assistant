"""Processing recovery settings, scanner lifecycle and heartbeat, offline.

Run: python -B tests/test_processing_recovery.py
No database: sessions are fakes. PostgreSQL behaviour (advisory locks, concurrent
scanners, end-to-end recovery) is covered by test_processing_recovery_postgres.py.
"""

import importlib
import os
import sys
import threading
import time
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch


class RecoveryOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.stack.enter_context(patch.dict(os.environ, {
            "PYTHON_DOTENV_DISABLED": "1", "ENVIRONMENT": "test", "TASK_QUEUE": "background",
            "DATALAB_API_KEY": "offline", "CLOUDINARY_URL": "cloudinary://key:secret@demo",
            "VOYAGE_API_KEY": "offline", "DEEPSEEK_API_KEY": "offline",
            "GOOGLE_CLIENT_ID": "offline.apps.googleusercontent.com", "JWT_SECRET_KEY": "offline-" * 8,
            # Engines connect lazily; nothing listens on port 1, so any use fails loudly.
            "DATABASE_URL": "postgresql+psycopg://offline@127.0.0.1:1/offline_test",
        }))
        for name in ("DOCUMENT_RECOVERY_ENABLED", "DOCUMENT_RECOVERY_STALE_SECONDS",
                     "DOCUMENT_RECOVERY_INTERVAL_SECONDS", "DOCUMENT_RECOVERY_MAX_AGE_SECONDS"):
            os.environ.pop(name, None)
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        cls.settings_module = importlib.import_module("app.services.recovery_settings")
        cls.recovery = importlib.import_module("app.services.processing_recovery")

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def settings(self, **environment):
        with patch.dict(os.environ, environment):
            return self.settings_module.recovery_settings()

    def test_defaults_enable_recovery_outside_tests_in_background_mode_only(self):
        self.assertFalse(self.settings().enabled)                                  # ENVIRONMENT=test
        production = self.settings(ENVIRONMENT="production")
        self.assertTrue(production.enabled)
        self.assertEqual((production.stale_seconds, production.interval_seconds, production.max_age_seconds),
                         (300, 60, 86400))
        self.assertTrue(self.settings(ENVIRONMENT="development").enabled)
        self.assertFalse(self.settings(ENVIRONMENT="production", TASK_QUEUE="celery").enabled)
        self.assertFalse(self.settings(ENVIRONMENT="production", DOCUMENT_RECOVERY_ENABLED="false").enabled)
        self.assertTrue(self.settings(DOCUMENT_RECOVERY_ENABLED="true").enabled)
        self.assertFalse(self.settings(DOCUMENT_RECOVERY_ENABLED="true", TASK_QUEUE="celery").enabled)

    def test_invalid_settings_fail_startup_validation(self):
        for name, value in [("DOCUMENT_RECOVERY_ENABLED", "yes"),
                            ("DOCUMENT_RECOVERY_STALE_SECONDS", "179"),   # too close to the 60s refresh
                            ("DOCUMENT_RECOVERY_STALE_SECONDS", "3601"),
                            ("DOCUMENT_RECOVERY_INTERVAL_SECONDS", "9"),
                            ("DOCUMENT_RECOVERY_MAX_AGE_SECONDS", "299"), # below the stale threshold
                            ("DOCUMENT_RECOVERY_INTERVAL_SECONDS", "abc")]:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.settings(**{name: value})
        runtime = importlib.import_module("app.services.runtime_config")
        with patch.dict(os.environ, {"DOCUMENT_RECOVERY_STALE_SECONDS": "10"}), self.assertRaises(ValueError):
            runtime.validate_runtime()

    def test_recovery_and_processing_claims_share_one_advisory_lock_key(self):
        claim = importlib.import_module("app.services.document_processing_claim")
        quota = importlib.import_module("app.services.upload_quota_service")
        self.assertEqual(self.recovery.LOCK_NAMESPACE, claim.LOCK_NAMESPACE)
        self.assertEqual(claim.LOCK_NAMESPACE, quota.PROCESSING_CLAIM_NAMESPACE)
        self.assertEqual(self.recovery.MAX_AUTOMATIC_RECOVERIES, 2)

    def test_disabled_settings_start_no_scanner(self):
        with patch.object(self.recovery, "recovery_settings", lambda: self.settings()):
            self.assertIsNone(self.recovery.start_recovery_scanner())

    def test_scanner_scans_at_startup_then_periodically_and_stops_promptly(self):
        settings = self.settings_module.RecoverySettings(True, 300, 10, 86400)
        calls = []
        scanner = self.recovery.RecoveryScanner(settings, initial_delay=0.01,
                                                scan=lambda settings: calls.append(time.monotonic()))
        scanner.start()
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(calls), 1)                     # first scan right after start
        started = time.monotonic()
        self.assertTrue(scanner.stop(timeout=2))            # interrupts the 10s wait
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(len(calls), 1)

    def test_scanner_keeps_running_after_a_failed_scan(self):
        settings = self.settings_module.RecoverySettings(True, 300, 0.01, 86400)
        calls = []

        def scan(settings):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("database unavailable")
        scanner = self.recovery.RecoveryScanner(settings, initial_delay=0.01, scan=scan)
        with patch.object(self.recovery, "log_exception") as logged:
            scanner.start()
            deadline = time.monotonic() + 5
            while len(calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(scanner.stop(timeout=2))
        self.assertGreaterEqual(len(calls), 2)
        logged.assert_called()

    def test_application_startup_is_not_blocked_by_a_slow_scan(self):
        from fastapi.testclient import TestClient
        main = importlib.import_module("main")
        entered, release = threading.Event(), threading.Event()

        def slow_scan(settings):
            entered.set()
            release.wait(10)
        enabled = self.settings_module.RecoverySettings(True, 300, 60, 86400)
        original = self.recovery.RecoveryScanner

        def immediate(settings):
            return original(settings, initial_delay=0.0, scan=slow_scan)
        with patch.object(self.recovery, "recovery_settings", lambda: enabled), \
                patch.object(self.recovery, "RecoveryScanner", immediate):
            started = time.monotonic()
            with TestClient(main.app) as client:
                self.assertTrue(entered.wait(5))           # the scan is running...
                self.assertEqual(client.get("/health").status_code, 200)   # ...and the app already serves
                self.assertLess(time.monotonic() - started, 5)
                release.set()

    def test_heartbeat_refreshes_while_running_and_stops_with_the_job(self):
        statements = []

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            @contextmanager
            def begin(self):
                yield

            def execute(self, statement):
                statements.append(str(statement))
        database = importlib.import_module("app.database.database")
        with patch.object(database, "SessionLocal", FakeSession):
            with self.recovery.processing_heartbeat(7, interval=0.02):
                time.sleep(0.15)
            count = len(statements)
            time.sleep(0.1)
        self.assertGreaterEqual(count, 3)
        self.assertEqual(len(statements), count)            # nothing after the job ended
        self.assertIn("processing_updated_at", statements[0])
        self.assertIn("processing_status", statements[0])   # only while still processing

    def test_heartbeat_failure_never_breaks_the_job(self):
        class Broken:
            def __enter__(self):
                raise RuntimeError("database unavailable")

            def __exit__(self, *exc):
                return False
        database = importlib.import_module("app.database.database")
        with patch.object(database, "SessionLocal", Broken), \
                patch.object(self.recovery, "log_exception") as logged:
            with self.recovery.processing_heartbeat(7, interval=0.02):
                time.sleep(0.08)
        logged.assert_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
