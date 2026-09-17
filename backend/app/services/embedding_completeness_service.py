"""SQL-only counts of vectors usable by cosine search; no vector hydration."""

from dataclasses import asdict, dataclass

from sqlalchemy import and_, func, not_, select

from app.database.models import Document, DocumentChunk
from app.services.embedding_contract import (
    EMBEDDING_DIMENSION,
    GENERATION_KEY,
    embedding_generation,
)


def embeddable_chunk(content_types: list[str] | None = None):
    # Ingestion embeds all nonblank chunks, including image descriptions, code
    # and formulae. Search can restrict this same check to its requested types.
    condition = DocumentChunk.content.op("~")(r"[^[:space:]]")
    if content_types is not None:
        condition = and_(condition, DocumentChunk.content_type.in_(content_types))
    return condition


def usable_embedding():
    # PostgreSQL vector storage already rejects nonfinite elements. Zero vectors
    # have no defined cosine similarity and are excluded from the cosine index.
    return and_(
        DocumentChunk.embedding.isnot(None),
        func.vector_dims(DocumentChunk.embedding) == EMBEDDING_DIMENSION,
        func.vector_norm(DocumentChunk.embedding) > 0,
    )


def needs_embedding():
    return not_(func.coalesce(usable_embedding(), False))


@dataclass(frozen=True)
class EmbeddingCompleteness:
    document_id: int
    processing_status: str
    required_chunks: int
    valid_chunks: int
    unverified_generation_chunks: int = 0

    @property
    def missing_chunks(self) -> int:
        return self.required_chunks - self.valid_chunks

    @property
    def state(self) -> str:
        if not self.required_chunks:
            return "no_embeddable_chunks"
        if not self.valid_chunks:
            return "no_valid_embeddings"
        if self.missing_chunks:
            return "partially_embedded"
        return "fully_embedded"

    @property
    def complete(self) -> bool:
        return self.state == "fully_embedded"

    def report(self) -> dict:
        return {
            **asdict(self),
            "missing_chunks": self.missing_chunks,
            "embedding_state": self.state,
            "semantic_ready": self.processing_status == "ready" and self.complete,
        }


def completeness_statement(document_ids: list[int], content_types=None):
    required = embeddable_chunk(content_types)
    valid = and_(required, usable_embedding())
    known_generation = func.coalesce(
        DocumentChunk.chunk_metadata[GENERATION_KEY] == embedding_generation(), False
    )
    return (
        select(
            Document.id.label("document_id"),
            Document.processing_status,
            func.count(DocumentChunk.id).filter(required).label("required_chunks"),
            func.count(DocumentChunk.id).filter(valid).label("valid_chunks"),
            func.count(DocumentChunk.id)
            .filter(and_(valid, not_(known_generation)))
            .label("unverified_generation_chunks"),
        )
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .where(Document.id.in_(document_ids))
        .group_by(Document.id, Document.processing_status)
        .order_by(Document.id)
    )


def inspect_embeddings(db, document_ids: list[int], content_types=None):
    if not document_ids:
        return []
    rows = db.execute(completeness_statement(document_ids, content_types)).mappings()
    return [EmbeddingCompleteness(**row) for row in rows]
