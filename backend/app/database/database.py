import os

from dotenv import load_dotenv
from app.database.pool_config import pool_options
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


load_dotenv()


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Render provides postgresql://, but this project uses Psycopg 3
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1,
    )


def enforce_utc_session(dbapi_connection, connection_record):
    """Run every database session in UTC.

    Timestamp columns store now() without a time zone, so their meaning depends
    on the session time zone. Setting it after connecting overrides the server,
    database and role settings and a PGTZ environment variable (which beats a
    startup "-c timezone" option). Stored values are not touched.
    """
    previous = dbapi_connection.autocommit
    dbapi_connection.autocommit = True
    try:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")
    finally:
        dbapi_connection.autocommit = previous


engine = create_engine(DATABASE_URL, **pool_options())
event.listen(engine, "connect", enforce_utc_session)


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()