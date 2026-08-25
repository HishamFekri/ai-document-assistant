from app.database.models import (
    Document,
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
        google_sub=f"doc-google-{number}",
        email=f"doc-user{number}@example.com",
        name=f"Document User {number}",
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


def create_document(
    db,
    user,
    filename="test.pdf",
):
    document = Document(
        user_id=user.id,
        filename=filename,
        file_type="pdf",
        file_path=None,
        pages_count=1,
        processing_status="ready",
        processing_stage="completed",
        processing_progress=100,
        processing_error=None,
    )

    db.add(document)
    db.commit()
    db.refresh(document)

    return document


def test_document_requires_authentication(
    client,
):
    response = client.get(
        "/documents/1"
    )

    assert response.status_code == 401


def test_user_can_access_own_document(
    client,
    db,
):
    user = create_user(
        db,
        1,
    )

    document = create_document(
        db,
        user,
    )

    response = client.get(
        f"/documents/{document.id}",
        headers=auth_headers(user),
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == document.id
    assert data["filename"] == "test.pdf"


def test_user_cannot_access_other_users_document(
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

    document_b = create_document(
        db,
        user_b,
    )

    response = client.get(
        f"/documents/{document_b.id}",
        headers=auth_headers(user_a),
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Document not found"
    }


def test_user_only_sees_own_documents(
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

    document_a = create_document(
        db,
        user_a,
        "user-a.pdf",
    )

    create_document(
        db,
        user_b,
        "user-b.pdf",
    )

    response = client.get(
        "/documents",
        headers=auth_headers(user_a),
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 1
    assert data[0]["id"] == document_a.id
    assert data[0]["filename"] == "user-a.pdf"


def test_user_cannot_delete_other_users_document(
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

    document_b = create_document(
        db,
        user_b,
    )

    response = client.delete(
        f"/documents/{document_b.id}",
        headers=auth_headers(user_a),
    )

    assert response.status_code == 404

    existing_document = db.get(
        Document,
        document_b.id,
    )

    assert existing_document is not None