"""Record extracted images that could not be stored for a document.

Revision ID: 5c1e8f2a9b47
Revises: d3e7a91c4b62

Nullable with no default: existing rows stay NULL ("not tracked"), and adding
the column is a catalog-only change in PostgreSQL (no table rewrite or backfill).
"""

from alembic import op
import sqlalchemy as sa


revision = "5c1e8f2a9b47"
down_revision = "d3e7a91c4b62"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("missing_image_count", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("documents", "missing_image_count")
