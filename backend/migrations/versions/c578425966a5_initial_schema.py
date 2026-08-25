"""initial schema

Revision ID: c578425966a5
Revises:
Create Date: 2026-08-25 19:18:03.383734

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "c578425966a5"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # pgvector is required before creating VECTOR columns.
    op.execute(
        "CREATE EXTENSION IF NOT EXISTS vector"
    )

    op.create_table(
        "users",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "google_sub",
            sa.String(length=255),
            nullable=False,
        ),

        sa.Column(
            "email",
            sa.String(length=255),
            nullable=False,
        ),

        sa.Column(
            "name",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "picture",
            sa.String(length=500),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),

        sa.UniqueConstraint(
            "email"
        ),

        sa.UniqueConstraint(
            "google_sub"
        ),
    )


    op.create_table(
        "chats",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "title",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "is_pinned",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),

        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),
    )


    op.create_table(
        "documents",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "filename",
            sa.String(length=255),
            nullable=False,
        ),

        sa.Column(
            "file_type",
            sa.String(length=20),
            nullable=True,
        ),

        sa.Column(
            "file_path",
            sa.String(length=500),
            nullable=True,
        ),

        sa.Column(
            "pages_count",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "processing_status",
            sa.String(length=20),
            server_default="ready",
            nullable=False,
        ),

        sa.Column(
            "processing_stage",
            sa.String(length=50),
            nullable=True,
        ),

        sa.Column(
            "processing_progress",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),

        sa.Column(
            "processing_error",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),
    )


    op.create_table(
        "chat_documents",

        sa.Column(
            "chat_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            ondelete="CASCADE",
        ),

        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "chat_id",
            "document_id",
        ),
    )


    op.create_table(
        "document_assets",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "asset_type",
            sa.String(length=30),
            nullable=False,
        ),

        sa.Column(
            "location",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "title",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "caption",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "content",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "file_path",
            sa.String(length=500),
            nullable=True,
        ),

        sa.Column(
            "metadata",
            postgresql.JSONB(
                astext_type=sa.Text()
            ),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),
    )

    op.create_index(
        op.f(
            "ix_document_assets_document_id"
        ),
        "document_assets",
        ["document_id"],
        unique=False,
    )

    op.create_index(
        "ix_document_assets_type",
        "document_assets",
        [
            "document_id",
            "asset_type",
        ],
        unique=False,
    )


    op.create_table(
        "document_chunks",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
        ),

        sa.Column(
            "content_type",
            sa.String(length=50),
            nullable=True,
        ),

        sa.Column(
            "location",
            sa.String(length=255),
            nullable=True,
        ),

        sa.Column(
            "metadata",
            postgresql.JSONB(
                astext_type=sa.Text()
            ),
            nullable=True,
        ),

        sa.Column(
            "embedding",
            Vector(384),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),
    )


    op.create_table(
        "document_summaries",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "chat_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "mode",
            sa.String(length=20),
            server_default="summary",
            nullable=False,
        ),

        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),

        sa.Column(
            "content",
            postgresql.JSONB(
                astext_type=sa.Text()
            ),
            nullable=True,
        ),

        sa.Column(
            "is_selected",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),

        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            ondelete="CASCADE",
        ),

        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),

        sa.UniqueConstraint(
            "chat_id",
            "document_id",
            "mode",
            "version",
            name=(
                "uq_chat_document_summary_"
                "mode_version"
            ),
        ),
    )

    op.create_index(
        op.f(
            "ix_document_summaries_chat_id"
        ),
        "document_summaries",
        ["chat_id"],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_document_summaries_document_id"
        ),
        "document_summaries",
        ["document_id"],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_document_summaries_mode"
        ),
        "document_summaries",
        ["mode"],
        unique=False,
    )

    op.create_index(
        "ix_document_summaries_selected",
        "document_summaries",
        [
            "document_id",
            "is_selected",
        ],
        unique=False,
    )


    op.create_table(
        "messages",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "chat_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
        ),

        sa.Column(
            "status",
            sa.String(length=20),
            server_default="completed",
            nullable=False,
        ),

        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "sources",
            postgresql.JSONB(
                astext_type=sa.Text()
            ),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),
    )


    op.create_table(
        "summary_assistant_messages",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "chat_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_summary_assistant_role",
        ),

        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            ondelete="CASCADE",
        ),

        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),
    )

    op.create_index(
        "ix_summary_assistant_document_created",
        "summary_assistant_messages",
        [
            "document_id",
            "created_at",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_summary_assistant_document_id",
        "summary_assistant_messages",
        ["document_id"],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_summary_assistant_messages_chat_id"
        ),
        "summary_assistant_messages",
        ["chat_id"],
        unique=False,
    )


    op.create_table(
        "message_documents",

        sa.Column(
            "message_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),

        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "message_id",
            "document_id",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_table(
        "message_documents"
    )

    op.drop_index(
        op.f(
            "ix_summary_assistant_messages_chat_id"
        ),
        table_name=(
            "summary_assistant_messages"
        ),
    )

    op.drop_index(
        "ix_summary_assistant_document_id",
        table_name=(
            "summary_assistant_messages"
        ),
    )

    op.drop_index(
        "ix_summary_assistant_document_created",
        table_name=(
            "summary_assistant_messages"
        ),
    )

    op.drop_table(
        "summary_assistant_messages"
    )

    op.drop_table(
        "messages"
    )

    op.drop_index(
        "ix_document_summaries_selected",
        table_name="document_summaries",
    )

    op.drop_index(
        op.f(
            "ix_document_summaries_mode"
        ),
        table_name="document_summaries",
    )

    op.drop_index(
        op.f(
            "ix_document_summaries_document_id"
        ),
        table_name="document_summaries",
    )

    op.drop_index(
        op.f(
            "ix_document_summaries_chat_id"
        ),
        table_name="document_summaries",
    )

    op.drop_table(
        "document_summaries"
    )

    op.drop_table(
        "document_chunks"
    )

    op.drop_index(
        "ix_document_assets_type",
        table_name="document_assets",
    )

    op.drop_index(
        op.f(
            "ix_document_assets_document_id"
        ),
        table_name="document_assets",
    )

    op.drop_table(
        "document_assets"
    )

    op.drop_table(
        "chat_documents"
    )

    op.drop_table(
        "documents"
    )

    op.drop_table(
        "chats"
    )

    op.drop_table(
        "users"
    )