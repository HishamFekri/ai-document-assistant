"""Recent chats ordered by latest user message, ONLY through conftest's isolated
TEST_DATABASE_URL fixture (loopback PostgreSQL with pgvector).

Providers are replaced with an immediate answer; no network access is used.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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
    assert datetime.fromisoformat(activity_event["last_activity_at"]) == stored
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
