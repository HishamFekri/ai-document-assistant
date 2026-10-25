"""Missing-image tracking: migration, end-to-end count and API, ONLY through conftest's
isolated TEST_DATABASE_URL fixture (loopback PostgreSQL with pgvector).

Providers are simulated by scripts/benchmark_processing.py with network access
refused. Run in the same pytest process as the benchmark test or its own.
"""

import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("benchmark_processing", BACKEND_DIR / "scripts" / "benchmark_processing.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
# Applied at collection, before the database fixture imports the application.
benchmark.configure_environment()

PREVIOUS_REVISION = "d3e7a91c4b62"


def alembic_config():
    return Config(str(BACKEND_DIR / "alembic.ini"))


def bearer(user_id):
    from app.database.models import User
    from app.services.auth_service import create_access_token
    return {"Authorization": f"Bearer {create_access_token(User(id=user_id, email='owner@example.invalid'))}"}


def api_client():
    import importlib
    from fastapi.testclient import TestClient
    return TestClient(importlib.import_module("main").app)


def create_user(connection, label):
    return connection.execute(text(
        "INSERT INTO users (google_sub, email, name) VALUES (:sub, :email, 'Owner') RETURNING id"),
        {"sub": f"missing-images-{label}", "email": f"{label}@example.invalid"}).scalar()


def test_failed_images_are_counted_per_occurrence_across_batches(test_database):
    result = benchmark.run_benchmark(["missing_images"], latency="zero")
    document = result["scenarios"][0]["documents"][0]
    fingerprint = document["fingerprint"]

    # Still a complete, usable document...
    assert document["status_code"] == 200
    assert (fingerprint["status"]["processing_status"], fingerprint["status"]["processing_progress"]) == ("ready", 100)
    assert fingerprint["status"]["processing_error"] is None
    # ...that records every image occurrence without a stored asset: the shared image
    # on all 22 pages (both Datalab batches) plus one unique figure.
    assert document["missing_image_count"] == 23
    # 22 unique figures, minus the rejected one, are still mapped to stored assets.
    assert fingerprint["counts"]["mapped_assets"] == 21
    # Rejected uploads were not retried (permanent 400); the shared image was
    # attempted once per batch, since failures are never cached across batches.
    assert result["provider_calls"]["image_upload"] == (20 + 1) + (2 + 1)   # per batch: figures + shared

    with test_database.engine.connect() as connection:
        chunks = connection.execute(text(
            "SELECT content, metadata FROM document_chunks WHERE document_id = :id AND content_type = 'image'"),
            {"id": document["document_id"]}).mappings().all()
        user_id = connection.scalar(text("SELECT user_id FROM documents WHERE id = :id"), {"id": document["document_id"]})
    without_asset = [chunk for chunk in chunks if not (chunk["metadata"] or {}).get("asset_path")]
    assert len(without_asset) == 23
    assert all(chunk["content"].startswith("Image extracted from the document") for chunk in without_asset)
    rejected = [f"img_{benchmark.hashlib.sha256(benchmark.png_bytes(seed)).hexdigest()[:40]}" for seed in (999, 10)]
    assert not any(public_id in str(chunk["metadata"]) for chunk in chunks for public_id in rejected)

    with api_client() as client:
        single = client.get(f"/documents/{document['document_id']}", headers=bearer(user_id))
        listed = client.get("/documents", headers=bearer(user_id))
    assert single.status_code == 200
    assert (single.json()["processing_status"], single.json()["missing_image_count"]) == ("ready", 23)
    assert [item["missing_image_count"] for item in listed.json()] == [23]


def test_documents_without_failures_record_zero(test_database):
    result = benchmark.run_benchmark(["small_text", "duplicate_images"], latency="zero")
    counts = [document["missing_image_count"] for scenario in result["scenarios"] for document in scenario["documents"]]
    assert counts == [0, 0]


def test_migration_keeps_existing_documents_working(test_database):
    config = alembic_config()
    engine = test_database.engine
    engine.dispose()
    command.downgrade(config, PREVIOUS_REVISION)
    try:
        with engine.connect() as connection:
            assert "missing_image_count" not in {column["name"] for column in inspect(connection).get_columns("documents")}
        # A document written by the previous release.
        with engine.begin() as connection:
            user_id = create_user(connection, "legacy")
            legacy_id = connection.execute(text(
                "INSERT INTO documents (user_id, filename, file_type, processing_status, processing_stage, "
                "processing_progress) VALUES (:user, 'legacy.pdf', 'pdf', 'ready', 'ready', 100) RETURNING id"),
                {"user": user_id}).scalar()
    finally:
        engine.dispose()
        command.upgrade(config, "head")

    with engine.connect() as connection:
        column = {column["name"]: column for column in inspect(connection).get_columns("documents")}["missing_image_count"]
        assert column["nullable"] is True and column["default"] is None
        assert str(column["type"]) == "INTEGER"
        assert connection.scalar(text("SELECT missing_image_count FROM documents WHERE id = :id"), {"id": legacy_id}) is None

    with api_client() as client:
        response = client.get(f"/documents/{legacy_id}", headers=bearer(user_id))
    assert response.status_code == 200
    body = response.json()
    assert (body["processing_status"], body["processing_progress"], body["missing_image_count"]) == ("ready", 100, None)

    # Downgrade with tracked values present, then upgrade again: clean both ways.
    with engine.begin() as connection:
        connection.execute(text("UPDATE documents SET missing_image_count = 4 WHERE id = :id"), {"id": legacy_id})
    engine.dispose()
    command.downgrade(config, PREVIOUS_REVISION)
    engine.dispose()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT missing_image_count FROM documents WHERE id = :id"), {"id": legacy_id}) is None
        assert connection.scalar(text("SELECT processing_status FROM documents WHERE id = :id"), {"id": legacy_id}) == "ready"
