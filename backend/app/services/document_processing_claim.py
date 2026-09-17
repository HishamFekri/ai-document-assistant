"""Session advisory locks serialize a document without a long transaction.

Requires direct PostgreSQL or session pooling, not transaction pooling. A physical
connection is retained solely to own the lock. Never reconnect a lost claim.
"""

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.database import engine
from app.services.document_processing_errors import ProcessingClaimLost


LOCK_NAMESPACE = 0x444F4350
TRY_CLAIM = text("SELECT pg_try_advisory_lock(:namespace, :document_id)")
RELEASE_CLAIM = text("SELECT pg_advisory_unlock(:namespace, :document_id)")


class DocumentProcessingClaim:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def session(self):
        if self.connection.closed or self.connection.invalidated:
            raise ProcessingClaimLost("Document processing ownership was lost")
        # The session owns this short transaction; it never escapes this scope.
        with Session(bind=self.connection, expire_on_commit=False, autoflush=False) as db:
            with db.begin():
                yield db


@contextmanager
def claim_document_processing(document_id: int):
    if type(document_id) is not int or not 0 < document_id < 2**31:
        raise ValueError("Invalid document ID")
    connection = engine.connect()
    acquired = False
    parameters = {"namespace": LOCK_NAMESPACE, "document_id": document_id}
    try:
        try:
            acquired = bool(connection.execute(TRY_CLAIM, parameters).scalar_one())
            connection.commit()
        except BaseException:
            # Acquisition may have succeeded server-side before the reply failed.
            connection.invalidate()
            raise
        yield DocumentProcessingClaim(connection) if acquired else None
    finally:
        try:
            if acquired and not connection.closed and not connection.invalidated:
                connection.rollback()
                released = connection.execute(RELEASE_CLAIM, parameters).scalar_one()
                connection.commit()
                if not released:
                    connection.invalidate()
        except Exception:
            # A pooled connection must never retain an advisory lock.
            connection.invalidate()
        except BaseException:
            connection.invalidate()
            raise
        finally:
            connection.close()
