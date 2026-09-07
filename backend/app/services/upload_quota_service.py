"""Serialize reservations using existing rows and shared, immutable originals."""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Document
from app.services.resource_admission import AdmissionUnavailable, ResourceRejected, user_operation
from app.services.resource_limits import resource_limits


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
        for row in rows:
            if row.file_path:
                path = Path(row.file_path).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise AdmissionUnavailable()
                total += path.stat().st_size
    except (OSError, ValueError):
        raise AdmissionUnavailable() from None
    if total > limits.max_original_bytes:
        raise ResourceRejected("storage_quota", "Your document storage limit has been reached. Delete documents before uploading more.", 60)


@contextmanager
def upload_quota_session(user_id, *, incoming_bytes=None, retry_document_id=None):
    with user_operation(user_id, "upload_quota", rate=False) as permit:
        with Session(bind=permit.connection, expire_on_commit=False, autoflush=False) as db:
            rows = db.execute(select(
                Document.id, Document.file_path, Document.processing_status,
            ).where(Document.user_id == user_id)).all()
            db.rollback()
            check_upload_quota(rows, incoming_bytes, retry_document_id)
            yield db
