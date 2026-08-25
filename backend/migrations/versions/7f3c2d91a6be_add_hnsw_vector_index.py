"""add hnsw vector index

Revision ID: 7f3c2d91a6be
Revises: c578425966a5
Create Date: 2026-08-25

"""

from typing import (
    Sequence,
    Union,
)

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7f3c2d91a6be"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "c578425966a5"

branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None

depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


def upgrade() -> None:
    """Add HNSW index for vector similarity search."""

    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={
            "embedding": "vector_cosine_ops",
        },
    )


def downgrade() -> None:
    """Remove HNSW vector index."""

    op.drop_index(
        "ix_document_chunks_embedding_hnsw",
        table_name="document_chunks",
    )