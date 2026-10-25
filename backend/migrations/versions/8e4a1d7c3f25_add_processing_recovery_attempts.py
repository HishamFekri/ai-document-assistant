"""Count automatic recoveries of interrupted background processing.

Revision ID: 8e4a1d7c3f25
Revises: 5c1e8f2a9b47

NOT NULL with a constant default: PostgreSQL 11+ records the default in the
catalog, so existing rows read 0 without a table rewrite or backfill.
"""

from alembic import op
import sqlalchemy as sa


revision = "8e4a1d7c3f25"
down_revision = "5c1e8f2a9b47"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("processing_recovery_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade():
    op.drop_column("documents", "processing_recovery_attempts")
