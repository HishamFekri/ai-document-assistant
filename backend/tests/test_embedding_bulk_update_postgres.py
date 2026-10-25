"""Live bulk embedding persistence checks, ONLY through conftest's isolated
TEST_DATABASE_URL fixture (loopback PostgreSQL with pgvector).

Regression: the VALUES-based bulk UPDATE must assign a real vector, not text.
Synthetic chunks and vectors only; no embedding provider is called.
"""

import struct

import pytest
from sqlalchemy import text


def float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


def synthetic_vector(seed):
    return [0.001 * ((seed * 7 + index) % 97 + 1) for index in range(512)]


def create_document(db, status, chunk_metadata=('{"page": 1}', "null", None, '{"page": 2}')):
    from app.services.embedding_recovery_service import RecoveryChunk

    db.execute(text(
        "INSERT INTO users (id, google_sub, email) "
        "VALUES (1, 'synthetic-sub', 'synthetic@example.invalid')"
    ))
    db.execute(text(
        "INSERT INTO documents (id, user_id, filename, processing_status) "
        "VALUES (1, 1, 'synthetic.pdf', :status)"
    ), {"status": status})
    for index, metadata in enumerate(chunk_metadata, start=1):
        db.execute(text(
            "INSERT INTO document_chunks (id, document_id, content, content_type, metadata) "
            "VALUES (:id, 1, :content, 'text', CAST(:metadata AS jsonb))"
        ), {"id": index, "content": f"synthetic chunk {index}", "metadata": metadata})
    db.commit()
    rows = db.execute(text(
        "SELECT id, document_id, content, jsonb_typeof(metadata) "
        "FROM document_chunks ORDER BY id"
    )).all()
    return [RecoveryChunk(id=row[0], document_id=row[1], content=row[2], metadata_type=row[3])
            for row in rows]


def stored_chunks(db):
    rows = db.execute(text(
        "SELECT id, embedding::text, metadata FROM document_chunks ORDER BY id"
    )).all()
    return {
        row[0]: (
            None if row[1] is None else [float(value) for value in row[1].strip("[]").split(",")],
            row[2],
        )
        for row in rows
    }


def assert_vectors_stored(db, chunks):
    from app.services.embedding_contract import GENERATION_KEY, embedding_generation

    stored = stored_chunks(db)
    for chunk in chunks:
        vector, metadata = stored[chunk.id]
        assert vector is not None and len(vector) == 512
        assert vector == pytest.approx([float32(value) for value in synthetic_vector(chunk.id)], abs=1e-6)
        assert metadata[GENERATION_KEY] == embedding_generation()
    return stored


def test_bulk_update_persists_every_vector_and_provenance(db):
    from app.services.embedding_recovery_service import recovery_bulk_update_statement

    chunks = create_document(db, "processing")
    result = db.execute(recovery_bulk_update_statement(
        chunks, [synthetic_vector(chunk.id) for chunk in chunks], statuses=("processing",),
    ))
    db.commit()

    assert result.rowcount == len(chunks) == 4
    stored = assert_vectors_stored(db, chunks)
    # Existing object metadata is merged, never replaced.
    assert stored[1][1]["page"] == 1
    assert stored[4][1]["page"] == 2
    # Distinct inputs remain distinct rows, not one broadcast value.
    assert len({tuple(vector) for vector, _ in stored.values()}) == 4


@pytest.mark.parametrize("status", ["ready", "failed"])
def test_bulk_update_rejects_document_in_disallowed_status(db, status):
    from app.services.embedding_recovery_service import recovery_bulk_update_statement

    chunks = create_document(db, status)
    result = db.execute(recovery_bulk_update_statement(
        chunks, [synthetic_vector(chunk.id) for chunk in chunks], statuses=("processing",),
    ))
    db.commit()

    assert result.rowcount == 0
    assert all(vector is None for vector, _ in stored_chunks(db).values())


def test_bulk_update_rejects_stale_chunk_content(db):
    from app.services.embedding_recovery_service import RecoveryChunk, recovery_bulk_update_statement

    chunks = create_document(db, "processing")
    stale = [RecoveryChunk(id=chunk.id, document_id=chunk.document_id,
                           content=chunk.content + " (edited)", metadata_type=chunk.metadata_type)
             for chunk in chunks]
    result = db.execute(recovery_bulk_update_statement(
        stale, [synthetic_vector(chunk.id) for chunk in stale], statuses=("processing",),
    ))
    db.commit()

    assert result.rowcount == 0
    assert all(vector is None for vector, _ in stored_chunks(db).values())


def test_bulk_update_never_overwrites_an_existing_valid_vector(db):
    from app.services.embedding_recovery_service import recovery_bulk_update_statement

    chunks = create_document(db, "processing")
    existing = "[" + ",".join(["0.5"] * 512) + "]"
    db.execute(text("UPDATE document_chunks SET embedding = CAST(:vector AS vector) WHERE id = 2"),
               {"vector": existing})
    db.commit()

    result = db.execute(recovery_bulk_update_statement(
        chunks, [synthetic_vector(chunk.id) for chunk in chunks], statuses=("processing",),
    ))
    db.commit()

    assert result.rowcount == 3
    stored = stored_chunks(db)
    assert stored[2][0] == pytest.approx([0.5] * 512)
    assert_vectors_stored(db, [chunk for chunk in chunks if chunk.id != 2])


def test_recovery_store_persist_batch_writes_recoverable_document(db):
    from app.services.embedding_recovery_service import PostgresRecoveryStore

    chunks = create_document(db, "ready")
    written = PostgresRecoveryStore(db).persist_batch(
        chunks, [synthetic_vector(chunk.id) for chunk in chunks],
    )

    assert written == len(chunks)
    assert_vectors_stored(db, chunks)


def test_recovery_store_persist_batch_skips_processing_document(db):
    from app.services.embedding_recovery_service import PostgresRecoveryStore

    chunks = create_document(db, "processing")
    written = PostgresRecoveryStore(db).persist_batch(
        chunks, [synthetic_vector(chunk.id) for chunk in chunks],
    )

    assert written == 0
    assert all(vector is None for vector, _ in stored_chunks(db).values())
