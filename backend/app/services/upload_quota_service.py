"""Serialize reservations using existing rows and shared, immutable originals."""

from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.database.models import Document
from app.services.resource_admission import AdmissionUnavailable, ResourceRejected, user_operation
from app.services.resource_limits import resource_limits, upload_limits


PROCESSING_STALE_SECONDS = 15 * 60
PROCESSING_CLAIM_NAMESPACE = 0x444F4350
TRY_STALE_PROCESSING_CLAIM = text(
    "SELECT pg_try_advisory_xact_lock(:namespace, :document_id)"
)
STALE_PROCESSING_ERROR = "Document processing was interrupted. Please retry."


def stale_processing_cutoff():
    return func.now() - timedelta(seconds=PROCESSING_STALE_SECONDS)


def reconcile_stale_processing(db, user_id):
    """Fail old processing rows only while atomically proving no worker owns them."""
    candidate_ids = db.scalars(select(Document.id).where(
        Document.user_id == user_id,
        Document.processing_status == "processing",
        Document.processing_stage.is_distinct_from("queued"),
        Document.processing_updated_at <= stale_processing_cutoff(),
    )).all()
    db.rollback()

    for document_id in candidate_ids:
        try:
            acquired = db.scalar(TRY_STALE_PROCESSING_CLAIM, {
                "namespace": PROCESSING_CLAIM_NAMESPACE,
                "document_id": document_id,
            })
            if not acquired:
                db.rollback()
                continue
            db.execute(update(Document).where(
                Document.id == document_id,
                Document.user_id == user_id,
                Document.processing_status == "processing",
                Document.processing_stage.is_distinct_from("queued"),
                Document.processing_updated_at <= stale_processing_cutoff(),
            ).values(
                processing_status="failed",
                processing_stage="retry_exhausted",
                processing_error=STALE_PROCESSING_ERROR,
            ).execution_options(synchronize_session=False))
            # The transaction-scoped document lock releases with this commit.
            db.commit()
        except BaseException:
            db.rollback()
            raise


def check_upload_quota(rows, incoming_bytes=None, retry_document_id=None, upload_root=Path("uploads")):
    limits = resource_limits()
    if incoming_bytes is None:
        return
    if len(rows) >= limits.max_documents:
        raise ResourceRejected("document_quota", "Your document limit has been reached. Delete a document before uploading another.", 60)
    total = incoming_bytes
    try:
        root = upload_root.resolve()
    except (OSError, ValueError):
        raise AdmissionUnavailable() from None
    for row in rows:
        recorded_size = getattr(row, "file_size_bytes", None)
        if recorded_size is not None:
            if type(recorded_size) is not int or recorded_size <= 0:
                raise AdmissionUnavailable()
            total += recorded_size
        elif row.file_path:
            try:
                path = Path(row.file_path).resolve()
            except (OSError, ValueError, TypeError):
                raise AdmissionUnavailable() from None
            if not path.is_relative_to(root):
                raise AdmissionUnavailable()
            # Render-local originals can disappear on a restart or deploy.
            # Legacy rows have no durable byte count, so reserve the full
            # per-file allowance rather than undercounting retained storage.
            try:
                total += path.stat().st_size if path.is_file() else upload_limits().file_bytes
            except OSError:
                total += upload_limits().file_bytes
        else:
            total += upload_limits().file_bytes
    if total > limits.max_original_bytes:
        raise ResourceRejected("storage_quota", "Your document storage limit has been reached. Delete documents before uploading more.", 60)


@contextmanager
def upload_quota_session(user_id, *, incoming_bytes=None, retry_document_id=None):
    with user_operation(user_id, "upload_quota", rate=False) as permit:
        with Session(bind=permit.connection, expire_on_commit=False, autoflush=False) as db:
            reconcile_stale_processing(db, user_id)
            rows = db.execute(select(
                Document.id, Document.file_path, Document.file_size_bytes,
                Document.processing_status,
            ).where(Document.user_id == user_id)).all()
            db.rollback()
            check_upload_quota(rows, incoming_bytes, retry_document_id)
            yield db
