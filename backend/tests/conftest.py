"""Database integration fixtures. Importing this module never connects to a DB."""

import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url


BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ROUTING_ENV_VARS = {"PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"}
PROTECTED_NAME_PARTS = {"dev", "development", "stage", "staging", "prod", "production"}


def validate_test_url(value, application_urls=(), environ=None) -> URL:
    """Validate without connecting, loading .env into os.environ, or echoing URLs."""
    if not value:
        raise ValueError(
            "TEST_DATABASE_URL is required; DATABASE_URL is never used as a fallback."
        )
    environ = os.environ if environ is None else environ
    if any(environ.get(name) for name in ROUTING_ENV_VARS):
        raise ValueError("Unset libpq routing overrides before running database tests.")
    try:
        url = make_url(value)
        port = url.port if url.port is not None else 5432
    except Exception:
        raise ValueError("TEST_DATABASE_URL is not a valid PostgreSQL URL.") from None

    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL with psycopg.")
    if url.host not in LOCAL_HOSTS or not 1 <= port <= 65535:
        raise ValueError("TEST_DATABASE_URL must specify a local loopback host and valid port.")
    if not url.username or url.query:
        raise ValueError("TEST_DATABASE_URL requires a username and must not contain query options.")

    name = url.database or ""
    parts = set(name.split("_"))
    # Leave room for _run_ plus a full UUID within PostgreSQL's 63-byte limit.
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,25}", name) or "test" not in parts:
        raise ValueError(
            "Test database base name must be 1-26 lowercase letters/digits/underscores, "
            "start with a letter, and contain a separate 'test' name segment."
        )
    if parts & PROTECTED_NAME_PARTS:
        raise ValueError("Test database base name must not identify development, staging, or production.")
    for application_url in application_urls:
        if not application_url:
            continue
        try:
            application_name = make_url(application_url).database
        except Exception:
            raise ValueError("Cannot safely compare the application database configuration.") from None
        if not application_name:
            raise ValueError("Cannot safely compare an application URL without an explicit database name.")
        if name.casefold() == application_name.casefold():
            raise ValueError("TEST_DATABASE_URL must not use the application database name.")
    return url.set(drivername="postgresql+psycopg", port=port)


@dataclass(frozen=True)
class TestDatabaseTarget:
    __test__ = False
    base_url: URL = field(repr=False)
    run_id: str = field(default_factory=lambda: uuid4().hex)

    def checked_url(self) -> URL:
        base_url = validate_test_url(self.base_url)
        if not re.fullmatch(r"[0-9a-f]{32}", self.run_id):
            raise ValueError("Invalid test database run identifier.")
        return base_url.set(database=f"{base_url.database}_run_{self.run_id}")


@dataclass
class TestDatabaseState:
    __test__ = False
    target: TestDatabaseTarget
    original_database_url: str | None = field(repr=False)


STATE_KEY = pytest.StashKey[TestDatabaseState]()


def pytest_configure(config):
    # Runs before test-module collection. No database is contacted here.
    try:
        value = os.environ.get("TEST_DATABASE_URL")
        validate_test_url(value)
        application_urls = (
            os.environ.get("DATABASE_URL"),
            dotenv_values(BACKEND_DIR / ".env").get("DATABASE_URL"),
        )
        base_url = validate_test_url(value, application_urls)
        if "app.database.database" in sys.modules or "main" in sys.modules:
            raise ValueError("Application already imported; start pytest in a fresh process.")
        target = TestDatabaseTarget(base_url)
        run_url = target.checked_url()
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None

    config.stash[STATE_KEY] = TestDatabaseState(target, os.environ.get("DATABASE_URL"))
    # Production modules read DATABASE_URL at import time. Redirect only this
    # pytest process before collection, and restore it in pytest_unconfigure.
    os.environ["DATABASE_URL"] = run_url.render_as_string(hide_password=False)


def pytest_report_header(config):
    state = config.stash.get(STATE_KEY, None)
    if state:
        url = state.target.checked_url()
        return f"Isolated test database: host={url.host} port={url.port} name={url.database}"


def pytest_unconfigure(config):
    state = config.stash.get(STATE_KEY, None)
    if state is None:
        return
    if state.original_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = state.original_database_url
    del config.stash[STATE_KEY]


@contextmanager
def isolated_database(target):
    """Create/drop only a fresh per-run DB; never drop a pre-existing database."""
    run_url = target.checked_url()
    admin_engine = create_engine(
        run_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    created = False
    try:
        with admin_engine.connect() as connection:
            target.checked_url()
            # Name is validated ASCII plus a generated UUID, never user SQL.
            # No IF EXISTS: a collision fails without touching that database.
            connection.execute(text(f'CREATE DATABASE "{run_url.database}"'))
            created = True
        yield
    finally:
        try:
            if created:
                if target.checked_url() != run_url:
                    raise ValueError("Refusing cleanup: test database target changed.")
                with admin_engine.connect() as connection:
                    # No connection termination or FORCE. A busy DB is left
                    # behind for manual investigation rather than forced down.
                    connection.execute(text(f'DROP DATABASE "{run_url.database}"'))
        finally:
            admin_engine.dispose()


def assert_test_binding(database, target):
    run_url = target.checked_url()
    if (
        database.engine.url != run_url
        or make_url(database.DATABASE_URL) != run_url
        or make_url(os.environ.get("DATABASE_URL", "")) != run_url
    ):
        raise ValueError("Refusing database operation: application is not bound to this test run.")


@pytest.fixture(scope="session", autouse=True)
def test_database(request):
    target = request.config.stash[STATE_KEY].target
    database = None
    try:
        with isolated_database(target):
            try:
                from app.database import database

                assert_test_binding(database, target)
                command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
                yield database
            finally:
                if database is not None:
                    database.engine.dispose()
    except Exception:
        pytest.fail(
            "Isolated test database setup/cleanup failed. Check the local test server, "
            "role permissions, pgvector, and open connections; details withheld to protect credentials.",
            pytrace=False,
        )


@pytest.fixture
def client(test_database):
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(test_database):
    with test_database.SessionLocal() as db_session:
        yield db_session


def truncate_test_tables(database, target):
    assert_test_binding(database, target)
    with database.engine.begin() as connection:
        if connection.scalar(text("SELECT current_database()")) != target.checked_url().database:
            raise ValueError("Refusing truncation: connected database does not match this test run.")
        tables = [
            name for name in inspect(connection).get_table_names(schema="public")
            if name != "alembic_version"
        ]
        if tables:
            quote = connection.dialect.identifier_preparer.quote_identifier
            names = ", ".join(f"public.{quote(name)}" for name in tables)
            connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def clean_database(test_database, request):
    yield
    try:
        truncate_test_tables(test_database, request.config.stash[STATE_KEY].target)
    except Exception:
        pytest.fail("Isolated test database cleanup failed; no other database will be used.", pytrace=False)
