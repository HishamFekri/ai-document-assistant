"""Track document processing liveness.

Revision ID: d3e7a91c4b62
Revises: a6f194c8d2e7
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e7a91c4b62"
down_revision = "a6f194c8d2e7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("processing_updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
    )
    # Preserve the real age of existing processing rows so already-stale work is
    # eligible for reconciliation immediately after deployment.
    op.execute(
        "UPDATE documents SET processing_updated_at = created_at "
        "WHERE processing_status = 'processing'"
    )
    op.alter_column("documents", "processing_updated_at", nullable=False)


def downgrade():
    op.drop_column("documents", "processing_updated_at")
