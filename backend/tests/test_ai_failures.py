from app.database.models import (
    Chat,
    Message,
    User,
)

from app.services.auth_service import (
    create_access_token,
)


def create_user(db):
    user = User(
        google_sub="ai-failure-google",
        email="ai-failure@example.com",
        name="AI Failure Test",
        picture=None,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def create_chat(db, user):
    chat = Chat(
        user_id=user.id,
        title="AI Failure Chat",
    )

    db.add(chat)
    db.commit()
    db.refresh(chat)

    return chat


def auth_headers(user):
    token = create_access_token(user)

    return {
        "Authorization": f"Bearer {token}"
    }


def test_ai_failure_returns_safe_error(
    client,
    db,
    monkeypatch,
):
    user = create_user(db)
    chat = create_chat(db, user)

    monkeypatch.setattr(
        "app.routes.chats.maybe_generate_chat_title",
        lambda **kwargs: None,
    )

    def fail_answer(*args, **kwargs):
        raise RuntimeError(
            "SECRET INTERNAL AI ERROR"
        )

    monkeypatch.setattr(
        "app.routes.chats.answer_question",
        fail_answer,
    )

    response = client.post(
        f"/chats/{chat.id}/ask",
        headers=auth_headers(user),
        json={
            "question": "Test question",
            "allow_general_knowledge": True,
            "document_ids": [],
        },
    )

    assert response.status_code == 500

    assert response.json() == {
        "detail": "Could not generate answer"
    }

    assert (
        "SECRET INTERNAL AI ERROR"
        not in response.text
    )


def test_failed_ai_marks_user_message_failed(
    client,
    db,
    monkeypatch,
):
    user = create_user(db)
    chat = create_chat(db, user)

    monkeypatch.setattr(
        "app.routes.chats.maybe_generate_chat_title",
        lambda **kwargs: None,
    )

    def fail_answer(*args, **kwargs):
        raise RuntimeError(
            "AI provider unavailable"
        )

    monkeypatch.setattr(
        "app.routes.chats.answer_question",
        fail_answer,
    )

    response = client.post(
        f"/chats/{chat.id}/ask",
        headers=auth_headers(user),
        json={
            "question": "Hello?",
            "allow_general_knowledge": True,
            "document_ids": [],
        },
    )

    assert response.status_code == 500

    message = (
        db.query(Message)
        .filter(
            Message.chat_id == chat.id,
            Message.role == "user",
        )
        .first()
    )

    db.refresh(message)

    assert message is not None
    assert message.status == "failed"

    assert message.error == (
        "Answer generation failed"
    )