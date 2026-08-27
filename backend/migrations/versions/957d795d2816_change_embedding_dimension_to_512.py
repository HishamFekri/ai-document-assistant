"""Change embedding dimension to 512

Revision ID: 957d795d2816
Revises: 7f3c2d91a6be
Create Date: 2026-08-27 06:14:00.394879
"""

from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector


revision: str = "957d795d2816"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "7f3c2d91a6be"

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
    # Old E5 embeddings are 384 dimensions
    # and cannot be reused with the new model.
    op.execute(
        """
        UPDATE document_chunks
        SET embedding = NULL
        WHERE embedding IS NOT NULL
        """
    )

    op.alter_column(
        "document_chunks",
        "embedding",
        existing_type=Vector(384),
        type_=Vector(512),
        existing_nullable=True,
    )


def downgrade() -> None:
    # 512-dimensional embeddings also cannot
    # be reused after reverting to 384 dimensions.
    op.execute(
        """
        UPDATE document_chunks
        SET embedding = NULL
        WHERE embedding IS NOT NULL
        """
    )

    op.alter_column(
        "document_chunks",
        "embedding",
        existing_type=Vector(512),
        type_=Vector(384),
        existing_nullable=True,
    )