"""Five approved query indexes, built without blocking ordinary table writes.

Revision ID: b11d62a4c901
Revises: 957d795d2816

Concurrent DDL is non-atomic. See docs/database-scalability.md for failed-build
inspection/recovery; never blindly skip an existing or invalid index.
"""

from alembic import op
import sqlalchemy as sa

revision = "b11d62a4c901"
down_revision = "957d795d2816"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_documents_user_created_id", "documents", ["user_id", sa.text("created_at DESC"), sa.text("id DESC")]),
    ("ix_chats_user_archive_pin_created_id", "chats", ["user_id", sa.text("is_archived ASC"), sa.text("is_pinned DESC"), sa.text("created_at DESC"), sa.text("id DESC")]),
    ("ix_messages_chat_created_id", "messages", ["chat_id", sa.text("created_at ASC"), sa.text("id ASC")]),
    ("ix_document_chunks_document_id_id", "document_chunks", ["document_id", "id"]),
    ("ix_chat_documents_document_chat", "chat_documents", ["document_id", "chat_id"]),
)


def upgrade():
    with op.get_context().autocommit_block():
        for name, table, columns in INDEXES:
            op.create_index(name, table, columns, unique=False, postgresql_concurrently=True)


def downgrade():
    with op.get_context().autocommit_block():
        for name, table, _ in reversed(INDEXES):
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
