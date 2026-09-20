"""Claimed processing with short transactions and durable chunk checkpoints."""

import logging
from app.services.observability import document_job, log_event
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import func, select

from app.database.models import Document, DocumentChunk
from app.services.assets.asset_extraction_service import ensure_document_assets
from app.services.chunk_service import create_chunks_from_content
from app.services.document_processing_claim import claim_document_processing
from app.services.document_processing_errors import (
    DocumentDeletedDuringProcessing,
    RetryableDocumentProcessingError,
    is_retryable_processing_error,
)
from app.services.embedding_completeness_service import (
    embeddable_chunk, inspect_embeddings, needs_embedding,
)
from app.services.embedding_contract import validate_embeddings
from app.services.embedding_recovery_service import RecoveryChunk, recovery_update_statement
from app.services.embedding_service import create_passage_embeddings
from app.services.error_service import log_generation_failure
from app.services.file_service import extract_content
from app.services.resource_admission import ResourceRejected, user_operation
from app.services.resource_limits import upload_limits
from app.services.document_resource_errors import DocumentResourceError
from app.services.upload_validation import validate_document_source
from app.services.queued_message_service import process_waiting_messages_for_document
from app.services.original_storage import materialize_original


logger = logging.getLogger(__name__)
PROCESSING_EMBEDDING_BATCH_SIZE = 32


def processing_document(db, document_id):
    document = db.get(Document, document_id, with_for_update=True)
    if document is None:
        raise DocumentDeletedDuringProcessing()
    return document


def set_progress(claim, document_id, stage, progress):
    with claim.session() as db:
        document = processing_document(db, document_id)
        document.processing_status = "processing"
        document.processing_stage = stage
        document.processing_progress = progress
        document.processing_error = None


def process_claimed_document(claim, document_id):
    with claim.session() as db:
        document = processing_document(db, document_id)
        completeness = inspect_embeddings(db, [document_id])[0]
        if document.processing_status == "ready":
            if completeness.complete:
                log_event(logger, logging.INFO, "processing_completed_job_skipped", document_id=document_id)
                return "already_complete"
            log_event(logger, logging.WARNING, "processing_requires_explicit_embedding_recovery", document_id=document_id)
            return "embedding_recovery_required"
        if document.processing_status == "failed" and document.processing_stage in ("permanent_failure", "retry_exhausted"):
            log_event(logger, logging.INFO, "processing_permanent_failure_skipped", document_id=document_id)
            return "permanent_failure"
        owner_id = document.user_id
        file_path = document.file_path
        file_type = document.file_type
        storage_key = getattr(document, "storage_key", None)
        file_size_bytes = getattr(document, "file_size_bytes", None)
        file_sha256 = getattr(document, "file_sha256", None)
        has_chunks = db.scalar(select(DocumentChunk.id).where(DocumentChunk.document_id == document_id).limit(1)) is not None
        # Dispatch-failure handling may only change the pre-execution uploaded
        # stage. Publish this transition before leaving the first transaction.
        document.processing_status = "processing"
        document.processing_stage = "starting"
        document.processing_progress = 10
        document.processing_error = None

    try:
        with user_operation(owner_id, "processing", rate=False, connection=claim.connection):
            with materialize_original(
                file_path=file_path,
                storage_key=storage_key,
                file_type=file_type,
                expected_size=file_size_bytes,
                checksum=file_sha256,
                max_size=upload_limits().file_bytes,
            ) as processing_path:
                return process_admitted_document(
                    claim, document_id, processing_path, file_type, has_chunks,
                )
    except ResourceRejected:
        raise RetryableDocumentProcessingError("Document processing admission unavailable") from None


def validate_resumed_processing(claim, document_id, path, file_type):
    validate_document_source(path, "." + file_type)
    limits = upload_limits()
    with claim.session() as db:
        count, characters, size = db.execute(select(
            func.count(DocumentChunk.id),
            func.coalesce(func.sum(func.length(DocumentChunk.content)), 0),
            func.coalesce(func.sum(func.octet_length(DocumentChunk.content)), 0),
        ).where(DocumentChunk.document_id == document_id)).one()
    if count > limits.chunks or characters > limits.text_chars or size > limits.text_bytes:
        raise DocumentResourceError("content_limit")


def process_admitted_document(claim, document_id, file_path, file_type, has_chunks):
    if not file_path:
        raise FileNotFoundError("Document source is unavailable")
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError("Document source is unavailable")
    if has_chunks:
        validate_resumed_processing(claim, document_id, path, file_type)

    if not has_chunks:
        set_progress(claim, document_id, "analyzing_document", 20)
        # No ORM session or transaction spans extraction / Datalab / Cloudinary.
        content = extract_content(file_path=path, document_id=document_id)
        if not content:
            raise ValueError("No readable content found in file")
        chunks = create_chunks_from_content(content)
        if not chunks:
            raise ValueError("Could not create chunks from file")
        with claim.session() as db:
            document = processing_document(db, document_id)
            # All assets and chunks form one checkpoint. Never delete prior work.
            ensure_document_assets(db, document_id, content)
            for chunk in chunks:
                db.add(DocumentChunk(
                    document_id=document_id,
                    content=chunk["content"], content_type=chunk["content_type"],
                    location=chunk["location"], chunk_metadata=chunk["metadata"],
                    embedding=None,
                ))
            document.processing_stage = "chunks_saved"
            document.processing_progress = 72
        log_event(logger, logging.INFO, "processing_checkpoint_saved", document_id=document_id, chunks=len(chunks))

    set_progress(claim, document_id, "creating_embeddings", 78)
    while True:
        with claim.session() as db:
            processing_document(db, document_id)
            rows = db.execute(
                select(DocumentChunk.id, DocumentChunk.document_id, DocumentChunk.content,
                       func.jsonb_typeof(DocumentChunk.chunk_metadata).label("metadata_type"))
                .where(DocumentChunk.document_id == document_id, embeddable_chunk(), needs_embedding())
                .order_by(DocumentChunk.id).limit(PROCESSING_EMBEDDING_BATCH_SIZE)
            ).mappings()
            pending = [RecoveryChunk(**row) for row in rows]
        if not pending:
            break
        if any(chunk.metadata_type not in (None, "null", "object") for chunk in pending):
            raise ValueError("Chunk metadata requires explicit repair")
        vectors = validate_embeddings(
            create_passage_embeddings([chunk.content for chunk in pending], batch_size=PROCESSING_EMBEDDING_BATCH_SIZE),
            len(pending),
        )
        with claim.session() as db:
            processing_document(db, document_id)
            for chunk, vector in zip(pending, vectors):
                # Maintenance keeps its original eligible statuses; this claimed
                # worker uses the same conditional update while processing.
                result = db.execute(recovery_update_statement(chunk, vector, statuses=("processing",)))
                if result.rowcount != 1:
                    raise ValueError("Chunk changed during processing; explicit retry required")
        log_event(logger, logging.INFO, "processing_embedding_batch_committed", document_id=document_id, chunks=len(pending))

    # Read source metadata outside the final transaction.
    pages_count = len(PdfReader(path).pages) if file_type == "pdf" else None
    with claim.session() as db:
        document = processing_document(db, document_id)
        db.flush()
        completeness = inspect_embeddings(db, [document_id])[0]
        if not completeness.complete:
            raise ValueError("Document embeddings are incomplete")
        if pages_count is not None:
            document.pages_count = pages_count
        document.processing_status = "ready"
        document.processing_stage = "ready"
        document.processing_progress = 100
        document.processing_error = None
    log_event(logger, logging.INFO, "processing_completed", document_id=document_id)
    return "completed"


@document_job
def process_document(document_id: int, file_path: str | None = None):
    # Keep the old task signature compatible; the stored source path is authoritative.
    outcome = None
    try:
        with claim_document_processing(document_id) as claim:
            if claim is None:
                log_event(logger, logging.INFO, "processing_duplicate_invocation_skipped", document_id=document_id)
                return "busy"
            log_event(logger, logging.INFO, "processing_claim_acquired", document_id=document_id)
            try:
                outcome = process_claimed_document(claim, document_id)
            except DocumentDeletedDuringProcessing:
                log_event(logger, logging.INFO, "processing_deleted_document_skipped", document_id=document_id)
                return "deleted"
            except Exception as error:
                retryable = is_retryable_processing_error(error)
                public_error = log_generation_failure(error, "document", document_id=document_id)
                try:
                    with claim.session() as db:
                        document = processing_document(db, document_id)
                        document.processing_status = "failed"
                        document.processing_stage = "retryable_failure" if retryable else "permanent_failure"
                        document.processing_error = public_error
                except DocumentDeletedDuringProcessing:
                    return "deleted"
                except Exception as state_error:
                    log_generation_failure(state_error, "document", document_id=document_id)
                    # A lost connection cannot reconnect and write without ownership.
                    retryable = retryable or is_retryable_processing_error(state_error)
                if retryable:
                    raise RetryableDocumentProcessingError("Document processing temporarily unavailable") from None
                log_event(logger, logging.WARNING, "processing_permanent_failure", document_id=document_id)
                outcome = "permanent_failure"
    except RetryableDocumentProcessingError:
        raise
    except Exception as error:
        log_generation_failure(error, "document", document_id=document_id)
        if is_retryable_processing_error(error):
            raise RetryableDocumentProcessingError("Document processing temporarily unavailable") from None
        # Sanitize errors escaping to Celery/result storage.
        raise RuntimeError("Could not start document processing") from None

    if outcome in ("completed", "already_complete", "permanent_failure"):
        try:
            process_waiting_messages_for_document(document_id)
        except Exception as error:
            # A queue wake-up error must never repeat successful document processing.
            log_generation_failure(error, "document", document_id=document_id)
    return outcome


def mark_processing_retries_exhausted(document_id):
    with claim_document_processing(document_id) as claim:
        if claim is None:
            return
        with claim.session() as db:
            document = db.get(Document, document_id, with_for_update=True)
            if (document is not None and document.processing_status == "failed"
                    and document.processing_stage == "retryable_failure"):
                document.processing_stage = "retry_exhausted"
