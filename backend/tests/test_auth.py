from app.database.models import User

from app.services.auth_service import (
    create_access_token,
)


def test_auth_me_requires_authentication(
    client,
):
    response = client.get(
        "/auth/me"
    )

    assert response.status_code == 401

    assert response.json() == {
        "detail": "Authentication required"
    }


def test_auth_me_rejects_invalid_token(
    client,
):
    response = client.get(
        "/auth/me",
        headers={
            "Authorization":
                "Bearer definitely-not-a-valid-token"
        },
    )

    assert response.status_code == 401


def test_auth_me_accepts_valid_token(
    client,
    db,
):
    user = User(
        google_sub="test-google-sub-1",
        email="testuser@example.com",
        name="Test User",
        picture=None,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(
        user
    )

    response = client.get(
        "/auth/me",
        headers={
            "Authorization":
                f"Bearer {access_token}"
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == user.id
    assert data["email"] == "testuser@example.com"
    assert data["name"] == "Test User"