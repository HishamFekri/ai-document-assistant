from app.database.models import (
    Chat,
    User,
)

from app.services.auth_service import (
    create_access_token,
)


def create_user(
    db,
    number: int,
):
    user = User(
        google_sub=f"google-sub-{number}",
        email=f"user{number}@example.com",
        name=f"User {number}",
        picture=None,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def auth_headers(user):
    token = create_access_token(
        user
    )

    return {
        "Authorization":
            f"Bearer {token}"
    }


def create_chat(
    db,
    user,
    title="Test Chat",
):
    chat = Chat(
        user_id=user.id,
        title=title,
    )

    db.add(chat)
    db.commit()
    db.refresh(chat)

    return chat


def test_chat_requires_authentication(
    client,
):
    response = client.get(
        "/chats/1"
    )

    assert response.status_code == 401


def test_user_can_access_own_chat(
    client,
    db,
):
    user = create_user(
        db,
        1,
    )

    chat = create_chat(
        db,
        user,
    )

    response = client.get(
        f"/chats/{chat.id}",
        headers=auth_headers(user),
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == chat.id
    assert data["title"] == "Test Chat"


def test_user_cannot_access_other_users_chat(
    client,
    db,
):
    user_a = create_user(
        db,
        1,
    )

    user_b = create_user(
        db,
        2,
    )

    chat_b = create_chat(
        db,
        user_b,
    )

    response = client.get(
        f"/chats/{chat_b.id}",
        headers=auth_headers(user_a),
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Chat not found"
    }


def test_user_cannot_update_other_users_chat(
    client,
    db,
):
    user_a = create_user(
        db,
        1,
    )

    user_b = create_user(
        db,
        2,
    )

    chat_b = create_chat(
        db,
        user_b,
    )

    response = client.patch(
        f"/chats/{chat_b.id}",
        headers=auth_headers(user_a),
        json={
            "title": "Hacked title"
        },
    )

    assert response.status_code == 404


def test_user_cannot_pin_other_users_chat(
    client,
    db,
):
    user_a = create_user(
        db,
        1,
    )

    user_b = create_user(
        db,
        2,
    )

    chat_b = create_chat(
        db,
        user_b,
    )

    response = client.patch(
        f"/chats/{chat_b.id}/pin",
        headers=auth_headers(user_a),
    )

    assert response.status_code == 404


def test_user_cannot_archive_other_users_chat(
    client,
    db,
):
    user_a = create_user(
        db,
        1,
    )

    user_b = create_user(
        db,
        2,
    )

    chat_b = create_chat(
        db,
        user_b,
    )

    response = client.patch(
        f"/chats/{chat_b.id}/archive",
        headers=auth_headers(user_a),
    )

    assert response.status_code == 404


def test_user_cannot_delete_other_users_chat(
    client,
    db,
):
    user_a = create_user(
        db,
        1,
    )

    user_b = create_user(
        db,
        2,
    )

    chat_b = create_chat(
        db,
        user_b,
    )

    response = client.delete(
        f"/chats/{chat_b.id}",
        headers=auth_headers(user_a),
    )

    assert response.status_code == 404

    existing_chat = db.get(
        Chat,
        chat_b.id,
    )

    assert existing_chat is not None