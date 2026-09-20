"""Persist original-document storage metadata.

Revision ID: a6f194c8d2e7
Revises: b11d62a4c901
"""

from alembic import op
import sqlalchemy as sa


revision = "a6f194c8d2e7"
down_revision = "b11d62a4c901"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("file_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("documents", sa.Column("storage_key", sa.String(length=500), nullable=True))
    op.add_column("documents", sa.Column("file_sha256", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_documents_file_size_bytes_positive",
        "documents",
        "file_size_bytes IS NULL OR file_size_bytes > 0",
    )
    op.create_check_constraint(
        "ck_documents_storage_metadata_complete",
        "documents",
        "storage_key IS NULL OR (file_size_bytes IS NOT NULL AND file_sha256 IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_documents_file_sha256_length",
        "documents",
        "file_sha256 IS NULL OR length(file_sha256) = 64",
    )


def downgrade():
    op.drop_constraint(
        "ck_documents_file_sha256_length",
        "documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_documents_storage_metadata_complete",
        "documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_documents_file_size_bytes_positive",
        "documents",
        type_="check",
    )
    op.drop_column("documents", "file_sha256")
    op.drop_column("documents", "storage_key")
    op.drop_column("documents", "file_size_bytes")
