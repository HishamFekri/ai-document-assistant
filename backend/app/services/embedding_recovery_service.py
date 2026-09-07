"""Explicit, bounded maintenance of existing chunks; never invoked by workers."""

from dataclasses import asdict, dataclass

from sqlalchemy import case, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB

from app.database.models import Document, DocumentChunk
from app.services.embedding_completeness_service import (
    embeddable_chunk,
    inspect_embeddings,
    needs_embedding,
)
from app.services.embedding_contract import (
    EMBEDDING_DIMENSION,
    VOYAGE_MODEL,
    validate_embeddings,
    with_embedding_generation,
)


RECOVERABLE_STATUSES = ("ready", "failed")


@dataclass(frozen=True)
class RecoveryLimits:
    max_documents: int = 10
    max_chunks: int = 256
    batch_size: int = 32

    def __post_init__(self):
        for name, ceiling in (("max_documents", 100), ("max_chunks", 10000), ("batch_size", 128)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be between 1 and {ceiling}")


@dataclass(frozen=True)
class RecoveryChunk:
    id: int
    document_id: int
    content: str
    metadata_type: str | None


def recovery_chunk_statement(document_ids, limit, after_chunk_id=0):
    return (
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            DocumentChunk.content,
            func.jsonb_typeof(DocumentChunk.chunk_metadata).label("metadata_type"),
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.document_id.in_(document_ids),
            Document.processing_status.in_(RECOVERABLE_STATUSES),
            DocumentChunk.id > after_chunk_id,
            embeddable_chunk(),
            needs_embedding(),
        )
        .order_by(DocumentChunk.id)
        .limit(limit)
    )


def recovery_update_statement(chunk, vector):
    metadata_type = func.jsonb_typeof(DocumentChunk.chunk_metadata)
    metadata = case(
        (metadata_type == "object", DocumentChunk.chunk_metadata),
        else_=cast({}, JSONB),
    )
    # Recheck eligibility atomically after the paid request. Merge into CURRENT
    # metadata so concurrent source/asset metadata edits are not overwritten.
    return (
        update(DocumentChunk)
        .where(
            DocumentChunk.id == chunk.id,
            DocumentChunk.document_id == chunk.document_id,
            DocumentChunk.content == chunk.content,
            needs_embedding(),
            or_(metadata_type.is_(None), metadata_type.in_(("null", "object"))),
            select(Document.id).where(
                Document.id == DocumentChunk.document_id,
                Document.processing_status.in_(RECOVERABLE_STATUSES),
            ).exists(),
        )
        .values(
            embedding=vector,
            chunk_metadata=metadata.op("||")(cast(with_embedding_generation(None), JSONB)),
        )
        .execution_options(synchronize_session=False)
    )


class PostgresRecoveryStore:
    """The caller supplies a dedicated session, never a request's pending work."""

    def __init__(self, db):
        self.db = db

    def document_ids(self, requested_ids, after_document_id, limit):
        statement = select(Document.id).where(Document.id > after_document_id)
        if requested_ids is not None:
            statement = statement.where(Document.id.in_(requested_ids))
        return list(self.db.scalars(statement.order_by(Document.id).limit(limit)))

    def inspect(self, document_ids):
        return inspect_embeddings(self.db, document_ids)

    def select_chunks(self, document_ids, limit, after_chunk_id=0):
        rows = self.db.execute(
            recovery_chunk_statement(document_ids, limit, after_chunk_id)
        ).mappings()
        return [RecoveryChunk(**row) for row in rows]

    def persist_batch(self, chunks, vectors):
        written = 0
        for chunk, vector in zip(chunks, vectors):
            written += self.db.execute(recovery_update_statement(chunk, vector)).rowcount
        self.db.commit()
        return written

    def rollback(self):
        self.db.rollback()


def recover_embeddings(
    store,
    *,
    limits: RecoveryLimits | None = None,
    document_ids: list[int] | None = None,
    after_document_id: int = 0,
    execute: bool = False,
    embed=None,
    emit=None,
) -> dict:
    """Default to read-only inspection. Re-read missing rows between commits.

    `embed`, when injected, must return vectors in input order. The real Voyage
    adapter validates response indices before this independent count/format check.
    """
    limits = limits or RecoveryLimits()
    emit = emit or (lambda event: None)
    if after_document_id < 0 or (document_ids is not None and any(i <= 0 for i in document_ids)):
        raise ValueError("Document IDs must be positive and cursor nonnegative")
    selected = store.document_ids(document_ids, after_document_id, limits.max_documents)
    before = store.inspect(selected)
    preview = store.select_chunks(selected, limits.max_chunks) if selected else []
    report = {
        "dry_run": not execute,
        "model": VOYAGE_MODEL,
        "dimension": EMBEDDING_DIMENSION,
        "limits": asdict(limits),
        "documents": [item.report() for item in before],
        "next_after_document_id": max(selected) if selected else after_document_id,
        "uninspected_requested_document_ids": sorted(set(document_ids or []) - set(selected)),
        "selected_missing_chunks": sum(item.missing_chunks for item in before),
        "planned_chunks": len(preview),
        "planned_input_characters": sum(len(item.content.strip()) for item in preview),
        "skipped_document_ids": [item.document_id for item in before if item.processing_status not in RECOVERABLE_STATUSES],
        "attempted_chunks": 0,
        "written_chunks": 0,
        "conflicted_chunks": 0,
        "failed": False,
    }
    emit({"event": "plan", **report})
    if not execute:
        return report

    # Lazy import keeps dry runs independent of provider credentials.
    if preview and embed is None:
        from app.services.embedding_service import create_passage_embeddings

        embed = lambda texts: create_passage_embeddings(texts, batch_size=limits.batch_size)

    after_chunk_id = 0
    while report["attempted_chunks"] < limits.max_chunks and preview:
        phase = "selection"
        try:
            chunks = store.select_chunks(
                selected,
                min(limits.batch_size, limits.max_chunks - report["attempted_chunks"]),
                after_chunk_id,
            )
            if not chunks:
                break
            if any(chunk.metadata_type not in (None, "null", "object") for chunk in chunks):
                raise ValueError("Recovery requires object or null chunk metadata")
            after_chunk_id = chunks[-1].id
            phase = "provider_or_validation"
            report["attempted_chunks"] += len(chunks)
            vectors = validate_embeddings(embed([chunk.content.strip() for chunk in chunks]), len(chunks))
            phase = "persistence"
            written = store.persist_batch(chunks, vectors)
            report["written_chunks"] += written
            report["conflicted_chunks"] += len(chunks) - written
            emit({"event": "batch_committed", "written_chunks": written, "last_chunk_id": after_chunk_id})
        except Exception:
            store.rollback()
            report["failed"] = True
            # Never print provider bodies, document content, credentials or SQL.
            emit({"event": "batch_failed", "phase": phase, "attempted_chunks": report["attempted_chunks"]})
            break

    report["documents"] = [item.report() for item in store.inspect(selected)]
    report["remaining_missing_chunks"] = sum(item["missing_chunks"] for item in report["documents"])
    emit({"event": "verified", **report})
    return report
