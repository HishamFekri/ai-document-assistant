"""Serialize reservations using existing rows and shared, immutable originals."""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Document
from app.services.resource_admission import AdmissionUnavailable, ResourceRejected, user_operation
from app.services.resource_limits import resource_limits, upload_limits


def check_upload_quota(rows, incoming_bytes=None, retry_document_id=None, upload_root=Path("uploads")):
    limits = resource_limits()
    active = sum(row.processing_status == "processing" and row.id != retry_document_id for row in rows)
    if active >= limits.concurrency["processing"]:
        raise ResourceRejected("processing_quota", "Please wait for your current document processing to finish.", 5)
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
            rows = db.execute(select(
                Document.id, Document.file_path, Document.file_size_bytes,
                Document.processing_status,
            ).where(Document.user_id == user_id)).all()
            db.rollback()
            check_upload_quota(rows, incoming_bytes, retry_document_id)
            yield db
