"""Recent chats ordered by latest user message, ONLY through conftest's isolated
TEST_DATABASE_URL fixture (loopback PostgreSQL with pgvector).

Providers are replaced with an immediate answer; no network access is used.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "8e4a1d7c3f25"
BASE = datetime(2026, 10, 1, 9, 0, 0)


def alembic_config():
    return Config(str(BACKEND_DIR / "alembic.ini"))


def bearer(user_id):
    from app.database.models import User
    from app.services.auth_service import create_access_token
    return {"Authorization": f"Bearer {create_access_token(User(id=user_id, email='owner@example.invalid'))}"}


def create_user(connection, label):
    return connection.execute(text(
        "INSERT INTO users (google_sub, email, name) VALUES (:sub, :email, 'Owner') RETURNING id"),
        {"sub": f"activity-{label}", "email": f"{label}@example.invalid"}).scalar()


def create_chat(connection, user_id, title, created, pinned=False, archived=False):
    return connection.execute(text(
        "INSERT INTO chats (user_id, title, is_pinned, is_archived, created_at) "
        "VALUES (:user, :title, :pinned, :archived, :created) RETURNING id"),
        {"user": user_id, "title": title, "pinned": pinned, "archived": archived, "created": created}).scalar()


def add_message(connection, chat_id, role, at):
    connection.execute(text(
        "INSERT INTO messages (chat_id, role, content, status, created_at) "
        "VALUES (:chat, :role, 'Synthetic', 'completed', :at)"),
        {"chat": chat_id, "role": role, "at": at})


def listed_titles(client, user_id, **params):
    response = client.get("/chats", headers=bearer(user_id), params=params)
    assert response.status_code == 200
    return [chat["title"] for chat in response.json()], response


def all_pages(client, user_id, limit):
    titles, cursor = [], None
    while True:
        page, response = listed_titles(client, user_id, limit=limit, **({"cursor": cursor} if cursor else {}))
        titles.extend(page)
        cursor = response.headers.get("X-Next-Cursor")
        if not cursor:
            return titles


def send(client, user_id, chat_id, question="What changed?"):
    """POST /ask/stream with the answer pipeline replaced by an immediate answer."""
    prepared = {"immediate_answer": "Synthetic answer.", "candidate_sources": [], "mode": "general"}
    with patch("app.routes.chats.prepare_answer_context", return_value=prepared), \
         patch("app.routes.chats.maybe_generate_chat_title", return_value=None):
        response = client.post(f"/chats/{chat_id}/ask/stream", headers=bearer(user_id),
                               json={"question": question, "allow_general_knowledge": True})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events[-1]["type"] == "done", events
    return events


def seed(engine, label):
    """Pinned, archived and Recent chats whose creation and activity orders differ."""
    with engine.begin() as connection:
        user_id = create_user(connection, label)
        chats = {
            "pinned old": create_chat(connection, user_id, "pinned old", BASE, pinned=True),
            "pinned new": create_chat(connection, user_id, "pinned new", BASE + timedelta(days=5), pinned=True),
            "archived old": create_chat(connection, user_id, "archived old", BASE + timedelta(days=1), archived=True),
            "archived new": create_chat(connection, user_id, "archived new", BASE + timedelta(days=6), archived=True),
            "created first, active last": create_chat(connection, user_id, "created first, active last", BASE + timedelta(days=2)),
            "created last, idle": create_chat(connection, user_id, "created last, idle", BASE + timedelta(days=4)),
            "assistant only": create_chat(connection, user_id, "assistant only", BASE + timedelta(days=3)),
        }
        add_message(connection, chats["pinned old"], "user", BASE + timedelta(days=9))
        add_message(connection, chats["pinned new"], "user", BASE + timedelta(days=5))
        add_message(connection, chats["archived old"], "user", BASE + timedelta(days=9))
        add_message(connection, chats["archived new"], "user", BASE + timedelta(days=6))
        add_message(connection, chats["created first, active last"], "user", BASE + timedelta(days=2))
        add_message(connection, chats["created first, active last"], "user", BASE + timedelta(days=8))
        add_message(connection, chats["created first, active last"], "assistant", BASE + timedelta(days=8, minutes=1))
        add_message(connection, chats["created last, idle"], "user", BASE + timedelta(days=4))
        add_message(connection, chats["assistant only"], "assistant", BASE + timedelta(days=10))
        # At head, give rows the activity the application would have recorded.
        if "last_activity_at" in {c["name"] for c in inspect(connection).get_columns("chats")}:
            connection.execute(text(migration().BACKFILL))
    return user_id, chats


def migration():
    import importlib.util
    path = BACKEND_DIR / "migrations" / "versions" / "4b8d2e6f1a93_add_chat_last_activity_at.py"
    spec = importlib.util.spec_from_file_location("chat_activity_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPECTED_ORDER = [
    "pinned new", "pinned old",                                   # pinned: creation order, as before
    "created first, active last", "created last, idle", "assistant only",  # Recent: latest user message
    "archived new", "archived old",                               # archived: creation order, as before
]


def test_migration_backfills_activity_and_round_trips(test_database, client):
    config = alembic_config()
    engine = test_database.engine
    engine.dispose()
    command.downgrade(config, PREVIOUS_REVISION)
    try:
        with engine.connect() as connection:
            assert "last_activity_at" not in {c["name"] for c in inspect(connection).get_columns("chats")}
        # Data written by the previous release.
        user_id, chats = seed(engine, "legacy")
    finally:
        engine.dispose()
        command.upgrade(config, "head")

    with engine.connect() as connection:
        column = {c["name"]: c for c in inspect(connection).get_columns("chats")}["last_activity_at"]
        assert column["nullable"] is False
        assert "now()" in str(column["default"])
        activity = dict(connection.execute(text("SELECT title, last_activity_at FROM chats")).all())
        index = connection.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_chats_user_archive_pin_activity_id'"))
    # Latest user message; assistant replies do not count; no user message falls back to creation.
    assert activity["created first, active last"] == BASE + timedelta(days=8)
    assert activity["created last, idle"] == BASE + timedelta(days=4)
    assert activity["assistant only"] == BASE + timedelta(days=3)
    assert activity["pinned old"] == BASE + timedelta(days=9)
    assert "CASE WHEN (is_pinned OR is_archived) THEN created_at ELSE last_activity_at END) DESC, id DESC" in " ".join(index.split())

    titles, _ = listed_titles(client, user_id)
    assert titles == EXPECTED_ORDER

    # Downgrade with activity present, then upgrade again: clean both ways, data kept.
    engine.dispose()
    command.downgrade(config, PREVIOUS_REVISION)
    with engine.connect() as connection:
        assert "last_activity_at" not in {c["name"] for c in inspect(connection).get_columns("chats")}
        assert connection.scalar(text(
            "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_chats_user_archive_pin_activity_id'")) == 0
        assert connection.scalar(text("SELECT count(*) FROM chats")) == len(chats)
    engine.dispose()
    command.upgrade(config, "head")
    titles, _ = listed_titles(client, user_id)
    assert titles == EXPECTED_ORDER


def test_sending_moves_a_chat_to_the_top_and_persists_across_reloads(test_database, client):
    user_id, chats = seed(test_database.engine, "send")
    events = send(client, user_id, chats["created last, idle"])

    activity_event = events[0]
    assert activity_event["type"] == "chat_activity"
    assert activity_event["chat_id"] == chats["created last, idle"]
    with test_database.engine.connect() as connection:
        stored = connection.scalar(text("SELECT last_activity_at FROM chats WHERE id = :id"),
                                   {"id": chats["created last, idle"]})
        message_time = connection.scalar(text(
            "SELECT max(created_at) FROM messages WHERE chat_id = :id AND role = 'user'"),
            {"id": chats["created last, idle"]})
    # Explicit UTC on the wire; the stored value is the same instant without a zone.
    assert activity_event["last_activity_at"].endswith("Z")
    assert datetime.fromisoformat(activity_event["last_activity_at"]) == stored.replace(tzinfo=timezone.utc)
    assert stored == message_time, "stamped in the same transaction as the message"

    titles, _ = listed_titles(client, user_id)
    assert titles[:3] == ["pinned new", "pinned old", "created last, idle"]
    # A fresh request (refresh or sign-in again) sees the same order.
    assert listed_titles(client, user_id)[0] == titles


def test_pinned_and_archived_chats_keep_their_place_when_used(test_database, client):
    user_id, chats = seed(test_database.engine, "groups")
    send(client, user_id, chats["pinned old"])
    send(client, user_id, chats["archived old"])
    titles, _ = listed_titles(client, user_id)
    assert titles == EXPECTED_ORDER, "pinned and archived stay in creation order and their own section"

    # Unpinning returns a chat to Recent at its real activity position.
    assert client.patch(f"/chats/{chats['pinned old']}/pin", headers=bearer(user_id)).status_code == 200
    titles, _ = listed_titles(client, user_id)
    assert titles[:2] == ["pinned new", "pinned old"], "its latest message is the newest in Recent"


def test_opening_and_reading_a_chat_does_not_change_its_position(test_database, client):
    user_id, chats = seed(test_database.engine, "read")
    before, _ = listed_titles(client, user_id)
    chat_id = chats["created last, idle"]
    assert client.get(f"/chats/{chat_id}", headers=bearer(user_id)).status_code == 200
    assert client.get(f"/chats/{chat_id}/messages", headers=bearer(user_id)).status_code == 200
    assert client.patch(f"/chats/{chat_id}", headers=bearer(user_id), json={"title": "renamed"}).status_code == 200
    after, _ = listed_titles(client, user_id)
    assert after == [("renamed" if title == "created last, idle" else title) for title in before]


def test_pagination_follows_activity_order_and_rejects_old_cursors(test_database, client):
    user_id, chats = seed(test_database.engine, "pages")
    send(client, user_id, chats["assistant only"])
    single_page, _ = listed_titles(client, user_id)
    for limit in (1, 2, 3):
        assert all_pages(client, user_id, limit) == single_page
    assert single_page[2] == "assistant only"

    from app.services.pagination import encode_cursor
    old_cursor = encode_cursor(user_id, "chats", [False, False, BASE, 1])
    response = client.get("/chats", headers=bearer(user_id), params={"cursor": old_cursor})
    assert response.status_code == 400


def test_activity_rolls_back_with_a_failed_message(test_database):
    from app.database.models import Chat, Message
    from app.routes.chats import record_chat_activity
    user_id, chats = seed(test_database.engine, "atomic")
    chat_id = chats["created last, idle"]
    with test_database.SessionLocal() as db:
        db.add(Message(chat_id=chat_id, role="user", content="Never stored", status="processing"))
        record_chat_activity(db, chat_id)
        db.flush()
        db.rollback()
    with test_database.SessionLocal() as db:
        assert db.get(Chat, chat_id).last_activity_at == BASE + timedelta(days=4)

    # A later timestamp already stored (an earlier transaction committing late) is never moved back.
    future = datetime(2099, 1, 1)
    with test_database.engine.begin() as connection:
        connection.execute(text("UPDATE chats SET last_activity_at = :t WHERE id = :id"), {"t": future, "id": chat_id})
    with test_database.SessionLocal() as db:
        record_chat_activity(db, chat_id)
        db.commit()
        assert db.get(Chat, chat_id).last_activity_at == future


def test_new_chats_start_with_activity_equal_to_creation(test_database, client):
    with test_database.engine.begin() as connection:
        user_id = create_user(connection, "new")
    response = client.post("/chats", headers=bearer(user_id), json={"title": "New chat"})
    assert response.status_code == 200
    body = response.json()
    assert body["last_activity_at"] == body["created_at"]


# ---- Migration lock safety -------------------------------------------------------

def revision_and_schema(engine):
    with engine.connect() as connection:
        return {
            "revision": connection.scalar(text("SELECT version_num FROM alembic_version")),
            "column": "last_activity_at" in {c["name"] for c in inspect(connection).get_columns("chats")},
            "index": connection.execute(text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'ix_chats_user_archive_pin_activity_id'")).scalar(),
        }


def at_previous_revision(test_database):
    engine = test_database.engine
    engine.dispose()
    command.downgrade(alembic_config(), PREVIOUS_REVISION)
    return engine


def test_migration_fails_fast_under_table_lock_and_retries_cleanly(test_database, client, monkeypatch):
    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT", "1s")
    engine = at_previous_revision(test_database)
    user_id, _ = seed(engine, "table-lock")

    holder = engine.connect()
    held = holder.begin()
    holder.execute(text("SELECT count(*) FROM chats"))   # a transaction still reading chats
    reads = []

    def chat_read_during_migration():
        time.sleep(0.3)
        start = time.monotonic()
        with engine.connect() as connection:
            connection.execute(text("SELECT count(*) FROM chats"))
        reads.append(time.monotonic() - start)

    reader = threading.Thread(target=chat_read_during_migration)
    reader.start()
    started = time.monotonic()
    try:
        with pytest.raises(Exception, match="lock timeout"):
            command.upgrade(alembic_config(), "head")
        elapsed = time.monotonic() - started
        reader.join(10)
    finally:
        held.rollback()
        holder.close()

    assert elapsed < 6, "fails fast instead of waiting for the blocking transaction"
    assert reads and reads[0] < 3, "queued chat reads are released when the migration gives up"
    # Nothing applied: the column and backfill rolled back together; the revision is unchanged.
    assert revision_and_schema(engine) == {"revision": PREVIOUS_REVISION, "column": False, "index": None}

    engine.dispose()
    command.upgrade(alembic_config(), "head")
    assert revision_and_schema(engine) == {"revision": "4b8d2e6f1a93", "column": True, "index": True}
    assert listed_titles(client, user_id)[0] == EXPECTED_ORDER


def test_blocked_index_build_leaves_no_invalid_index_and_retries_cleanly(test_database, client, monkeypatch):
    monkeypatch.setenv("MIGRATION_INDEX_LOCK_TIMEOUT", "1s")
    engine = at_previous_revision(test_database)
    user_id, _ = seed(engine, "index-lock")

    # An old snapshot that a concurrent build must wait for (no lock on chats itself).
    holder = engine.connect()
    held = holder.begin()
    holder.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    holder.execute(text("SELECT count(*) FROM users"))
    try:
        with pytest.raises(Exception, match="lock timeout"):
            command.upgrade(alembic_config(), "head")
    finally:
        held.rollback()
        holder.close()

    # The column step committed; the failed build left nothing behind; the revision is unchanged.
    assert revision_and_schema(engine) == {"revision": PREVIOUS_REVISION, "column": True, "index": None}

    engine.dispose()
    command.upgrade(alembic_config(), "head")
    assert revision_and_schema(engine) == {"revision": "4b8d2e6f1a93", "column": True, "index": True}
    assert listed_titles(client, user_id)[0] == EXPECTED_ORDER


def test_invalid_leftover_index_is_replaced_but_a_valid_one_is_never_dropped(test_database):
    engine = at_previous_revision(test_database)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE chats ADD COLUMN last_activity_at TIMESTAMP NOT NULL DEFAULT now()"))
        connection.execute(text("CREATE INDEX ix_chats_user_archive_pin_activity_id ON chats (user_id)"))
        # What an interrupted CREATE INDEX CONCURRENTLY leaves behind.
        connection.execute(text("UPDATE pg_index SET indisvalid = false "
                                "WHERE indexrelid = 'public.ix_chats_user_archive_pin_activity_id'::regclass"))
    engine.dispose()
    command.upgrade(alembic_config(), "head")
    assert revision_and_schema(engine) == {"revision": "4b8d2e6f1a93", "column": True, "index": True}
    with engine.connect() as connection:
        definition = connection.scalar(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_chats_user_archive_pin_activity_id'"))
    assert "CASE" in definition, "rebuilt with the real definition"

    # A valid index of the same name is someone else's: fail loudly, never drop it.
    engine = at_previous_revision(test_database)
    with engine.begin() as connection:
        connection.execute(text("CREATE INDEX ix_chats_user_archive_pin_activity_id ON chats (user_id)"))
    engine.dispose()
    with pytest.raises(Exception, match="already exists"):
        command.upgrade(alembic_config(), "head")
    assert revision_and_schema(engine)["index"] is True
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_chats_user_archive_pin_activity_id"))
    engine.dispose()
    command.upgrade(alembic_config(), "head")
    assert revision_and_schema(engine) == {"revision": "4b8d2e6f1a93", "column": True, "index": True}


def test_lock_timeout_settings_are_validated():
    module = migration()
    for bad in ("0s", "5 minutes", "5s; DROP TABLE chats", "", "-1s"):
        with patch.dict("os.environ", {"MIGRATION_LOCK_TIMEOUT": bad}):
            with pytest.raises(ValueError):
                module.lock_timeout("MIGRATION_LOCK_TIMEOUT", "5s")
    with patch.dict("os.environ", {"MIGRATION_LOCK_TIMEOUT": "750ms"}):
        assert module.lock_timeout("MIGRATION_LOCK_TIMEOUT", "5s") == "750ms"


def test_restoring_from_the_archive_returns_a_chat_to_its_activity_position(test_database, client):
    # A: 15:00 today, B: 13:00 today, C: yesterday (all created earlier, in a different order).
    with test_database.engine.begin() as connection:
        user_id = create_user(connection, "restore")
        ids = {
            "C": create_chat(connection, user_id, "C", BASE),
            "A": create_chat(connection, user_id, "A", BASE + timedelta(hours=1)),
            "B": create_chat(connection, user_id, "B", BASE + timedelta(hours=2)),
        }
        today = BASE + timedelta(days=20)
        add_message(connection, ids["A"], "user", today.replace(hour=15))
        add_message(connection, ids["B"], "user", today.replace(hour=13))
        add_message(connection, ids["C"], "user", today - timedelta(days=1))
        connection.execute(text(migration().BACKFILL))

    def activity():
        with test_database.engine.connect() as connection:
            return dict(connection.execute(text("SELECT title, last_activity_at FROM chats")).all())

    before = activity()
    assert listed_titles(client, user_id)[0] == ["A", "B", "C"]
    for title, expected in (("A", ["B", "C", "A"]), ("C", ["A", "B", "C"])):
        assert client.patch(f"/chats/{ids[title]}/archive", headers=bearer(user_id)).status_code == 200
        assert listed_titles(client, user_id)[0] == expected, f"{title} archived"
        assert client.patch(f"/chats/{ids[title]}/archive", headers=bearer(user_id)).status_code == 200
        assert listed_titles(client, user_id)[0] == ["A", "B", "C"], f"{title} restored to its activity position"
    assert activity() == before, "archiving and restoring never change last_activity_at"
    for limit in (1, 2, 3):
        assert all_pages(client, user_id, limit) == ["A", "B", "C"]


# ---- UTC sessions and explicit offsets ----------------------------------------------

@pytest.fixture
def foreign_timezone(test_database, monkeypatch):
    """A database default and a client PGTZ that both disagree with UTC."""
    engine = test_database.engine
    with engine.begin() as connection:
        name = connection.scalar(text("SELECT current_database()"))
        connection.execute(text(f'ALTER DATABASE "{name}" SET timezone = \'Asia/Tokyo\''))
    monkeypatch.setenv("PGTZ", "America/New_York")
    engine.dispose()
    try:
        yield engine
    finally:
        monkeypatch.delenv("PGTZ")
        engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'ALTER DATABASE "{name}" RESET timezone'))
        engine.dispose()


def near_utc_now(value):
    parsed = datetime.fromisoformat(value)
    assert value.endswith("Z") and parsed.tzinfo is not None, value
    return abs(datetime.now(timezone.utc) - parsed) < timedelta(minutes=2)


def test_every_connection_runs_in_utc_whatever_the_server_says(foreign_timezone, client):
    engine = foreign_timezone
    for _ in range(2):   # a fresh connection, then the same one reused from the pool
        with engine.connect() as connection:
            assert connection.scalar(text("SHOW timezone")) == "UTC"
            session_now = connection.scalar(text("SELECT now()::timestamp"))
        assert abs(session_now - datetime.now(timezone.utc).replace(tzinfo=None)) < timedelta(minutes=2)

    with engine.begin() as connection:
        user_id = create_user(connection, "utc")
    created = client.post("/chats", headers=bearer(user_id), json={"title": "UTC"}).json()
    assert near_utc_now(created["created_at"]) and near_utc_now(created["last_activity_at"])
    events = send(client, user_id, created["id"])
    activity = next(event for event in events if event["type"] == "chat_activity")
    assert near_utc_now(activity["last_activity_at"])
    messages = client.get(f"/chats/{created['id']}/messages", headers=bearer(user_id)).json()
    assert messages and all(near_utc_now(message["created_at"]) for message in messages)
    assert near_utc_now(client.get("/auth/me", headers=bearer(user_id)).json()["created_at"])


def test_offsets_leave_ordering_and_cursors_unchanged(test_database, client):
    user_id, _ = seed(test_database.engine, "offsets")
    titles, response = listed_titles(client, user_id)
    assert titles == EXPECTED_ORDER
    assert all(chat["created_at"].endswith("Z") and chat["last_activity_at"].endswith("Z")
               for chat in response.json())
    # Stored values are unchanged: the wire value is the stored wall time, labelled UTC.
    with test_database.engine.connect() as connection:
        stored = connection.scalar(text("SELECT last_activity_at FROM chats WHERE title = 'created first, active last'"))
    listed = next(chat for chat in response.json() if chat["title"] == "created first, active last")
    assert datetime.fromisoformat(listed["last_activity_at"]) == stored.replace(tzinfo=timezone.utc)
    assert stored == BASE + timedelta(days=8)
    for limit in (1, 2, 3):
        assert all_pages(client, user_id, limit) == EXPECTED_ORDER


def test_migrations_run_in_utc_whatever_the_server_says(foreign_timezone, client):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    seen = set()

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.add(connection.connection.dbapi_connection.info.parameter_status("TimeZone"))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        command.downgrade(alembic_config(), PREVIOUS_REVISION)
        user_id, _ = seed(foreign_timezone, "migration-utc")
        foreign_timezone.dispose()
        command.upgrade(alembic_config(), "head")
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    assert seen == {"UTC"}
    titles, _ = listed_titles(client, user_id)
    assert titles == EXPECTED_ORDER
