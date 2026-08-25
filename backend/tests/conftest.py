import os

import pytest

from dotenv import load_dotenv

from sqlalchemy import (
    create_engine,
    inspect,
    text,
)

from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config

from fastapi.testclient import TestClient


load_dotenv()


ORIGINAL_DATABASE_URL = os.getenv(
    "DATABASE_URL"
)

if not ORIGINAL_DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set"
    )


TEST_DATABASE_NAME = (
    "ai_document_assistant_test"
)


original_url = make_url(
    ORIGINAL_DATABASE_URL
)

test_url = original_url.set(
    database=TEST_DATABASE_NAME
)


# مهم جدًا:
# نغيّر DATABASE_URL قبل استيراد التطبيق
# حتى كل شيء يتصل بقاعدة بيانات الاختبارات.
os.environ["DATABASE_URL"] = (
    test_url.render_as_string(
        hide_password=False
    )
)


def create_test_database():
    admin_engine = create_engine(
        original_url.set(
            database="postgres"
        ),
        isolation_level="AUTOCOMMIT",
    )

    with admin_engine.connect() as conn:
        conn.execute(
            text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :database_name
                  AND pid <> pg_backend_pid()
                """
            ),
            {
                "database_name":
                    TEST_DATABASE_NAME
            },
        )

        conn.execute(
            text(
                f'DROP DATABASE IF EXISTS '
                f'"{TEST_DATABASE_NAME}"'
            )
        )

        conn.execute(
            text(
                f'CREATE DATABASE '
                f'"{TEST_DATABASE_NAME}"'
            )
        )

    admin_engine.dispose()


def drop_test_database():
    admin_engine = create_engine(
        original_url.set(
            database="postgres"
        ),
        isolation_level="AUTOCOMMIT",
    )

    with admin_engine.connect() as conn:
        conn.execute(
            text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :database_name
                  AND pid <> pg_backend_pid()
                """
            ),
            {
                "database_name":
                    TEST_DATABASE_NAME
            },
        )

        conn.execute(
            text(
                f'DROP DATABASE IF EXISTS '
                f'"{TEST_DATABASE_NAME}"'
            )
        )

    admin_engine.dispose()


# إنشاء Test Database
create_test_database()


# تشغيل كل Alembic migrations عليها
alembic_config = Config(
    "alembic.ini"
)

command.upgrade(
    alembic_config,
    "head",
)


# مهم:
# الاستيراد يكون بعد تغيير DATABASE_URL
from main import app

from app.database.database import (
    engine,
    SessionLocal,
)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    db_session = SessionLocal()

    try:
        yield db_session
    finally:
        db_session.close()


@pytest.fixture(autouse=True)
def clean_database():
    yield

    inspector = inspect(engine)

    table_names = [
        table_name
        for table_name
        in inspector.get_table_names()
        if table_name != "alembic_version"
    ]

    if not table_names:
        return

    quoted_tables = ", ".join(
        f'"{table_name}"'
        for table_name in table_names
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                TRUNCATE TABLE
                {quoted_tables}
                RESTART IDENTITY
                CASCADE
                """
            )
        )


@pytest.fixture(
    scope="session",
    autouse=True,
)
def cleanup_test_database():
    yield

    engine.dispose()

    drop_test_database()

    os.environ[
        "DATABASE_URL"
    ] = ORIGINAL_DATABASE_URL