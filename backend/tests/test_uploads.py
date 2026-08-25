from app.database.models import User

from app.services.auth_service import (
    create_access_token,
)


def create_user(db):
    user = User(
        google_sub="upload-test-google",
        email="upload-test@example.com",
        name="Upload Test User",
        picture=None,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def auth_headers(user):
    token = create_access_token(user)

    return {
        "Authorization": f"Bearer {token}"
    }


def test_upload_requires_authentication(
    client,
):
    response = client.post(
        "/documents",
        files={
            "file": (
                "test.pdf",
                b"fake pdf",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 401


def test_upload_rejects_unsupported_file_type(
    client,
    db,
):
    user = create_user(db)

    response = client.post(
        "/documents",
        headers=auth_headers(user),
        files={
            "file": (
                "malware.exe",
                b"fake executable",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 400

    assert response.json() == {
        "detail": (
            "Unsupported file type. "
            "Allowed: PDF, DOCX, XLSX, TXT"
        )
    }


def test_upload_rejects_file_over_size_limit(
    client,
    db,
    monkeypatch,
):
    user = create_user(db)

    monkeypatch.setattr(
        "app.routes.documents.MAX_UPLOAD_SIZE_BYTES",
        5,
    )

    response = client.post(
        "/documents",
        headers=auth_headers(user),
        files={
            "file": (
                "large.pdf",
                b"123456",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 413