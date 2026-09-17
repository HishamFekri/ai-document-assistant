"""PostgreSQL summary ownership; direct connections or session pooling required."""

from contextlib import contextmanager
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import database


# Distinct from Batch 6's 0x444F4350 and from each other.
GENERATION_NAMESPACE = 0x53554D47
MUTATION_NAMESPACE = 0x53554D54
TRY_GENERATION = text("SELECT pg_try_advisory_lock(:namespace, :key)")
UNLOCK_GENERATION = text("SELECT pg_advisory_unlock(:namespace, :key)")
LOCK_MUTATION = text("SELECT pg_advisory_xact_lock(:namespace, :key)")
TRY_CLEANUP = text("SELECT pg_try_advisory_xact_lock(:namespace, :key)")


class SummaryGenerationBusy(HTTPException):
    def __init__(self):
        super().__init__(409, "Summary generation is already active. Please wait or cancel it.")


class SummaryClaimLost(RuntimeError):
    pass


def context_key(chat_id, document_id, mode):
    # Nullable legacy chat contexts can still be cancelled/failed safely.
    # New generation below requires a real chat.
    if chat_id is not None and (type(chat_id) is not int or chat_id <= 0):
        raise ValueError("Summary chat context is missing")
    if type(document_id) is not int or document_id <= 0:
        raise ValueError("Invalid document context")
    if mode not in {"summary", "transcription"}:
        raise ValueError("Invalid summary mode")
    # Python's hash() differs across processes. Collisions here only serialize
    # unrelated contexts; SQL predicates always use the full context.
    value = f"{chat_id}:{document_id}:{mode}".encode("ascii")
    return int.from_bytes(sha256(value).digest()[:4], "big", signed=True)


def lock_summary_context(db, chat_id, document_id, mode):
    db.execute(LOCK_MUTATION, {
        "namespace": MUTATION_NAMESPACE,
        "key": context_key(chat_id, document_id, mode),
    })


def cleanup_is_safe(db, chat_id, document_id, mode):
    # Reentrant for the owning generation's physical session. Other requests
    # skip cleanup while any provider (including a cancelled one) is in flight.
    return bool(db.scalar(TRY_CLEANUP, {
        "namespace": GENERATION_NAMESPACE,
        "key": context_key(chat_id, document_id, mode),
    }))


def require_generation_owner(db, chat_id, document_id, mode):
    if db.info.get("summary_context") != (chat_id, document_id, mode):
        raise SummaryClaimLost("Summary generation ownership is missing")
    db.get_bind()  # Guard against reconnecting an invalidated physical session.


class ClaimedSummarySession(Session):
    def get_bind(self, *args, **kwargs):
        connection = super().get_bind(*args, **kwargs)
        if connection.closed or connection.invalidated:
            raise SummaryClaimLost("Summary generation ownership was lost")
        return connection


@contextmanager
def summary_generation_session(chat_id, document_id, mode):
    if chat_id is None:
        raise ValueError("Summary chat context is missing")
    parameters = {
        "namespace": GENERATION_NAMESPACE,
        "key": context_key(chat_id, document_id, mode),
    }
    connection = database.engine.connect()
    acquired = False
    try:
        try:
            acquired = bool(connection.scalar(TRY_GENERATION, parameters))
            connection.commit()
        except BaseException:
            connection.invalidate()
            raise
        if not acquired:
            raise SummaryGenerationBusy()
        with ClaimedSummarySession(
            bind=connection, expire_on_commit=False, autoflush=False,
            info={"summary_context": (chat_id, document_id, mode)},
        ) as db:
            yield db
    finally:
        try:
            if acquired and not connection.closed and not connection.invalidated:
                connection.rollback()
                released = connection.scalar(UNLOCK_GENERATION, parameters)
                connection.commit()
                if not released:
                    connection.invalidate()
        except Exception:
            connection.invalidate()
        except BaseException:
            connection.invalidate()
            raise
        finally:
            connection.close()
