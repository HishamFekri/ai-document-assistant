"""Order Recent chats by their latest user message.

Revision ID: 4b8d2e6f1a93
Revises: 8e4a1d7c3f25

Additive. The column is added NOT NULL with a now() default (stored in the
catalog, no table rewrite), then backfilled from each chat's latest user
message, falling back to the chat's creation time. The ordering index is built
concurrently, outside a transaction, so ordinary writes are not blocked.

The column step commits before the index build. If the build is interrupted,
the revision stays at its parent; the column step is idempotent so the upgrade
can be re-run after removing any invalid index (docs/database-scalability.md).
"""

from alembic import op
import sqlalchemy as sa


revision = "4b8d2e6f1a93"
down_revision = "8e4a1d7c3f25"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_chats_user_archive_pin_activity_id"

# Same expression as app.database.models.CHAT_LIST_SORT_AT.
INDEX_COLUMNS = [
    "user_id",
    sa.text("is_archived ASC"),
    sa.text("is_pinned DESC"),
    sa.text("(CASE WHEN (is_pinned OR is_archived) THEN created_at ELSE last_activity_at END) DESC"),
    sa.text("id DESC"),
]

BACKFILL = """
UPDATE chats
SET last_activity_at = COALESCE(
    (
        SELECT max(messages.created_at)
        FROM messages
        WHERE messages.chat_id = chats.id
          AND messages.role = 'user'
    ),
    chats.created_at,
    chats.last_activity_at
)
"""


def upgrade():
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS last_activity_at "
        "TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()"
    )
    op.execute(BACKFILL)
    with op.get_context().autocommit_block():
        op.create_index(INDEX_NAME, "chats", INDEX_COLUMNS, unique=False, postgresql_concurrently=True)


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(INDEX_NAME, table_name="chats", postgresql_concurrently=True, if_exists=True)
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS last_activity_at")
