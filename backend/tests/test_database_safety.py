"""Run with `python -B tests/test_database_safety.py`; no PostgreSQL required."""

import os
import runpy
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.engine import make_url

import conftest as safety


# Synthetic credentials only. All database engines used below are mocks.
TEST_URL = "postgresql+psycopg://test_role:synthetic_password@127.0.0.1:5432/assistant_test"
APP_URL = "postgresql+psycopg://app:synthetic_password@127.0.0.1:5432/assistant"


class DatabaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        self.engine_factory = patch.object(safety, "create_engine").start()
        self.migrate = patch.object(safety.command, "upgrade").start()
        patch.object(safety, "dotenv_values", return_value={}).start()

    def target(self):
        return safety.TestDatabaseTarget(safety.validate_test_url(TEST_URL))

    def test_missing_url_never_falls_back(self):
        os.environ["DATABASE_URL"] = APP_URL
        with self.assertRaisesRegex(ValueError, "TEST_DATABASE_URL is required"):
            safety.validate_test_url(None)
        self.engine_factory.assert_not_called()

    def test_unsafe_urls_are_rejected_without_echoing_credentials(self):
        base = make_url(TEST_URL)
        unsafe = [
            "not-a-url-with-synthetic_password",
            "postgresql+psycopg://test_role:synthetic_password@127.0.0.1:invalid/assistant_test",
            base.set(drivername="sqlite"),
            base.set(host="production.example.com"),
            base.set(host=""),
            base.set(host="/var/run/postgresql"),
            base.set(port=0),
            base.set(port=65536),
            base.set(username=""),
            base.set(query={"host": "production.example.com"}),
            base.set(query={"service": "production"}),
            base.set(query={"options": "-c search_path=private"}),
        ]
        for name in (
            "", "assistant", "postgres", "template0", "template1", "latest",
            "contest", "prod_test", "test_staging", "test_dev", "TEST_DB",
            "test_bad-name", 'test_name";DROP DATABASE postgres;--', "test_" + "a" * 22,
        ):
            unsafe.append(base.set(database=name))
        for value in unsafe:
            with self.subTest(case=len(str(value))):
                with self.assertRaises(ValueError) as caught:
                    safety.validate_test_url(value)
                self.assertNotIn("synthetic_password", str(caught.exception))
        self.engine_factory.assert_not_called()

    def test_application_name_rejected_even_on_another_host(self):
        normal = make_url(TEST_URL).set(host="application.example.com")
        with self.assertRaisesRegex(ValueError, "application database name"):
            safety.validate_test_url(TEST_URL, [normal])

    def test_invalid_application_url_fails_closed(self):
        for value in ("invalid-synthetic_password", "postgresql://app@localhost"):
            with self.assertRaisesRegex(ValueError, "Cannot safely compare"):
                safety.validate_test_url(TEST_URL, [value])

    def test_libpq_overrides_are_rejected(self):
        for variable in safety.ROUTING_ENV_VARS:
            with self.subTest(variable=variable):
                with self.assertRaisesRegex(ValueError, "routing overrides"):
                    safety.validate_test_url(TEST_URL, environ={variable: "unsafe"})

    def test_supported_hosts_and_default_driver_port(self):
        for host in safety.LOCAL_HOSTS:
            url = make_url(TEST_URL)._replace(host=host, drivername="postgresql", port=None)
            result = safety.validate_test_url(url)
            self.assertEqual(result.host, host)
            self.assertEqual(result.port, 5432)
            self.assertEqual(result.drivername, "postgresql+psycopg")

    def test_run_names_are_unique_safe_and_within_postgres_limit(self):
        base = make_url(TEST_URL).set(database="ai_document_assistant_test")
        names = [safety.TestDatabaseTarget(base).checked_url().database for _ in range(20)]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertLessEqual(len(name.encode("ascii")), 63)
            self.assertRegex(name, r"^ai_document_assistant_test_run_[0-9a-f]{32}$")

    def test_invalid_run_identifier_prevents_engine_creation(self):
        target = safety.TestDatabaseTarget(make_url(TEST_URL), run_id='bad"name')
        with self.assertRaises(ValueError):
            with safety.isolated_database(target):
                self.fail("Unsafe target accepted")
        self.engine_factory.assert_not_called()

    def test_import_has_no_database_or_environment_side_effects(self):
        before = dict(os.environ)
        with patch("sqlalchemy.create_engine") as factory:
            with patch("dotenv.load_dotenv") as loader:
                runpy.run_path(str(safety.BACKEND_DIR / "tests" / "conftest.py"))
        factory.assert_not_called()
        loader.assert_not_called()
        self.migrate.assert_not_called()
        self.assertEqual(before, dict(os.environ))

    def test_configure_missing_url_fails_before_collection(self):
        config = SimpleNamespace(stash=pytest.Stash())
        os.environ["DATABASE_URL"] = APP_URL
        with self.assertRaisesRegex(pytest.UsageError, "TEST_DATABASE_URL is required"):
            safety.pytest_configure(config)
        self.assertEqual(os.environ["DATABASE_URL"], APP_URL)
        self.engine_factory.assert_not_called()
        self.migrate.assert_not_called()

    def test_configure_rejects_application_name_from_dotenv(self):
        config = SimpleNamespace(stash=pytest.Stash())
        os.environ["TEST_DATABASE_URL"] = TEST_URL
        safety.dotenv_values.return_value = {"DATABASE_URL": TEST_URL}
        with self.assertRaisesRegex(pytest.UsageError, "application database name"):
            safety.pytest_configure(config)
        self.engine_factory.assert_not_called()

    def test_configure_rejects_preimported_database_module(self):
        config = SimpleNamespace(stash=pytest.Stash())
        os.environ["TEST_DATABASE_URL"] = TEST_URL
        with patch.dict(sys.modules, {"app.database.database": SimpleNamespace()}):
            with self.assertRaisesRegex(pytest.UsageError, "already imported"):
                safety.pytest_configure(config)
        self.engine_factory.assert_not_called()

    def test_configure_only_redirects_process_and_restores_original(self):
        for original in (None, APP_URL):
            with self.subTest(original_present=original is not None):
                config = SimpleNamespace(stash=pytest.Stash())
                os.environ["TEST_DATABASE_URL"] = TEST_URL
                if original is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = original
                # This check also runs inside the normal integration suite.
                with patch.dict(sys.modules):
                    sys.modules.pop("app.database.database", None)
                    sys.modules.pop("main", None)
                    safety.pytest_configure(config)
                run_url = make_url(os.environ["DATABASE_URL"])
                self.assertRegex(run_url.database, r"^assistant_test_run_[0-9a-f]{32}$")
                header = safety.pytest_report_header(config)
                self.assertIn("host=127.0.0.1", header)
                self.assertNotIn("synthetic_password", header)
                self.assertNotIn("synthetic_password", repr(config.stash[safety.STATE_KEY]))
                safety.pytest_unconfigure(config)
                self.assertEqual(os.environ.get("DATABASE_URL"), original)
        self.engine_factory.assert_not_called()
        self.migrate.assert_not_called()

    def test_create_and_drop_only_the_generated_database(self):
        target = self.target()
        connection = self.engine_factory.return_value.connect.return_value.__enter__.return_value
        with safety.isolated_database(target):
            self.assertEqual(connection.execute.call_count, 1)
        sql = [str(call.args[0]) for call in connection.execute.call_args_list]
        name = target.checked_url().database
        self.assertEqual(sql, [f'CREATE DATABASE "{name}"', f'DROP DATABASE "{name}"'])
        self.assertEqual(self.engine_factory.call_args.args[0].database, "postgres")
        self.engine_factory.return_value.dispose.assert_called_once()

    def test_creation_failure_never_drops_an_existing_database(self):
        connection = self.engine_factory.return_value.connect.return_value.__enter__.return_value
        connection.execute.side_effect = RuntimeError("database already exists")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            with safety.isolated_database(self.target()):
                self.fail("Creation failure ignored")
        self.assertEqual(connection.execute.call_count, 1)
        self.engine_factory.return_value.dispose.assert_called_once()

    def test_failure_after_creation_still_cleans_only_owned_database(self):
        connection = self.engine_factory.return_value.connect.return_value.__enter__.return_value
        with self.assertRaisesRegex(RuntimeError, "migration failed"):
            with safety.isolated_database(self.target()):
                raise RuntimeError("migration failed")
        self.assertEqual(connection.execute.call_count, 2)
        self.assertTrue(str(connection.execute.call_args.args[0]).startswith("DROP DATABASE"))

    def test_busy_database_cleanup_does_not_force_or_terminate(self):
        connection = self.engine_factory.return_value.connect.return_value.__enter__.return_value
        connection.execute.side_effect = [None, RuntimeError("database busy")]
        with self.assertRaisesRegex(RuntimeError, "database busy"):
            with safety.isolated_database(self.target()):
                pass
        self.assertEqual(connection.execute.call_count, 2)
        sql = " ".join(str(call.args[0]) for call in connection.execute.call_args_list)
        self.assertNotIn("FORCE", sql)
        self.assertNotIn("terminate", sql)
        self.engine_factory.return_value.dispose.assert_called_once()

    def database_mock(self, target):
        url = target.checked_url()
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        engine = MagicMock(url=url)
        return SimpleNamespace(engine=engine, DATABASE_URL=os.environ["DATABASE_URL"])

    def test_changed_binding_blocks_truncation_before_connect(self):
        target = self.target()
        database = self.database_mock(target)
        database.engine.url = make_url(APP_URL)
        with self.assertRaisesRegex(ValueError, "not bound"):
            safety.truncate_test_tables(database, target)
        database.engine.begin.assert_not_called()

    def test_actual_database_mismatch_blocks_truncation(self):
        target = self.target()
        database = self.database_mock(target)
        connection = database.engine.begin.return_value.__enter__.return_value
        connection.scalar.return_value = "assistant"
        with self.assertRaisesRegex(ValueError, "connected database"):
            safety.truncate_test_tables(database, target)
        connection.execute.assert_not_called()

    def test_truncation_preserves_migrations_and_resets_test_rows(self):
        target = self.target()
        database = self.database_mock(target)
        connection = database.engine.begin.return_value.__enter__.return_value
        connection.scalar.return_value = target.checked_url().database
        connection.dialect.identifier_preparer.quote_identifier.side_effect = lambda name: f'"{name}"'
        with patch.object(safety, "inspect") as inspector:
            inspector.return_value.get_table_names.return_value = ["users", "messages", "alembic_version"]
            safety.truncate_test_tables(database, target)
        self.assertEqual(
            str(connection.execute.call_args.args[0]),
            'TRUNCATE TABLE public."users", public."messages" RESTART IDENTITY CASCADE',
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
