"""Order Recent chats by their latest user message.

Revision ID: 4b8d2e6f1a93
Revises: 8e4a1d7c3f25

Additive. The column is added NOT NULL with a now() default (stored in the
catalog, no table rewrite), then backfilled from each chat's latest user
message, falling back to the chat's creation time. The ordering index is built
concurrently, outside a transaction, so ordinary writes are not blocked.

Lock waits are bounded. The column and backfill run with a transaction-scoped
lock_timeout, so a deploy that meets a long-running transaction fails fast and
rolls back instead of queueing every chat query behind ALTER TABLE. The
concurrent build has its own, longer limit.

The column step commits before the index build. If the build fails or is
interrupted, the revision stays at its parent and the upgrade can simply be
re-run: the column step is idempotent and an INVALID index left by an earlier
attempt is dropped first. A VALID index of the same name is never dropped
(docs/database-scalability.md, docs/rollback-runbook.md).
"""

import logging
import os
import re

from alembic import op
import sqlalchemy as sa


revision = "4b8d2e6f1a93"
down_revision = "8e4a1d7c3f25"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

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


def lock_timeout(setting, default):
    """A PostgreSQL duration such as '5s' or '500ms'; strictly validated, never raw SQL."""
    value = os.getenv(setting, default).strip()
    if not re.fullmatch(r"[1-9][0-9]{0,5}(ms|s)", value):
        raise ValueError(f"{setting} must look like 5s or 500ms")
    return value


def drop_invalid_index():
    """Remove an INVALID index left by an interrupted concurrent build."""
    context = op.get_context()
    if context.as_sql:
        return
    invalid = context.connection.execute(sa.text(
        "SELECT NOT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = :name AND c.relnamespace = 'public'::regnamespace"
    ), {"name": INDEX_NAME}).scalar()
    if invalid:
        logger.warning("Dropping invalid index %s left by an earlier attempt", INDEX_NAME)
        op.drop_index(INDEX_NAME, table_name="chats", postgresql_concurrently=True, if_exists=True)


def upgrade():
    table_timeout = lock_timeout("MIGRATION_LOCK_TIMEOUT", "5s")
    index_timeout = lock_timeout("MIGRATION_INDEX_LOCK_TIMEOUT", "60s")

    # Scoped to this migration's transaction; a timeout rolls back the column and backfill together.
    op.execute(f"SET LOCAL lock_timeout = '{table_timeout}'")
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS last_activity_at "
        "TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()"
    )
    op.execute(BACKFILL)

    with op.get_context().autocommit_block():
        # Session-level here (no transaction); reset afterwards on this migration connection.
        op.execute(f"SET lock_timeout = '{index_timeout}'")
        try:
            drop_invalid_index()
            op.create_index(INDEX_NAME, "chats", INDEX_COLUMNS, unique=False, postgresql_concurrently=True)
        except Exception:
            logger.error(
                "Building %s did not complete; the revision stays at %s and the upgrade can be re-run",
                INDEX_NAME, down_revision,
            )
            drop_invalid_index()
            raise
        finally:
            op.execute("RESET lock_timeout")


def downgrade():
    table_timeout = lock_timeout("MIGRATION_LOCK_TIMEOUT", "5s")
    index_timeout = lock_timeout("MIGRATION_INDEX_LOCK_TIMEOUT", "60s")

    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{index_timeout}'")
        try:
            op.drop_index(INDEX_NAME, table_name="chats", postgresql_concurrently=True, if_exists=True)
        finally:
            op.execute("RESET lock_timeout")

    op.execute(f"SET LOCAL lock_timeout = '{table_timeout}'")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS last_activity_at")
