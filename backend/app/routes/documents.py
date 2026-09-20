from app.services.observability import log_event, log_exception
from fastapi import Response
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginated
from fastapi import Query
import os
import logging

from pathlib import Path

from dotenv import load_dotenv

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
)

from sqlalchemy.orm import Session
from sqlalchemy import update

from app.database.database import get_db

from app.database.models import (
    Document,
    User,
)

from app.schemas.schemas import (
    DocumentResponse,
)

from app.routes.auth import (
    get_current_user,
)

from app.services.file_service import (
    SUPPORTED_FILE_TYPES,
)

from app.services.task_queue import (
    enqueue_document_processing,
)
from app.services.document_processing_claim import claim_document_processing
from app.services.embedding_completeness_service import inspect_embeddings
from app.services.error_service import log_generation_failure
from app.services.upload_quota_service import upload_quota_session
from app.services.resource_limits import upload_limits
from app.services.upload_validation import validate_upload_source
from app.services.upload_ingress import UploadRoute, upload_phase
from app.services.document_resource_errors import DocumentResourceError
from app.services.original_storage import (
    delete_stored_original,
    store_original,
)


load_dotenv()
logger = logging.getLogger(__name__)


# Compatibility aliases; values come from the centralized upload policy.
MAX_UPLOAD_SIZE_MB = upload_limits().file_bytes // 1024**2
MAX_UPLOAD_SIZE_BYTES = upload_limits().file_bytes


MAX_FILENAME_LENGTH = 255


router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
    route_class=UploadRoute,
)


UPLOAD_DIR = Path("uploads")

UPLOAD_DIR.mkdir(
    exist_ok=True
)


def get_owned_document(
    db: Session,
    document_id: int,
    current_user: User,
) -> Document:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
        .first()
    )

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    return document


def get_file_size(
    file: UploadFile,
) -> int:
    file.file.seek(
        0,
        2,
    )

    file_size = (
        file.file.tell()
    )

    file.file.seek(
        0
    )

    return file_size


def validate_file_size(
    file: UploadFile,
):
    file_size = get_file_size(
        file
    )

    if file_size <= 0:
        raise DocumentResourceError("empty_file", 400)

    if file_size > MAX_UPLOAD_SIZE_BYTES:
        raise DocumentResourceError("file_size")

    return file_size



def validate_file_content(file: UploadFile, extension: str):
    validate_upload_source(file.file, extension)


@router.get("/upload-policy")
def get_upload_policy(current_user: User = Depends(get_current_user)):
    return upload_limits().public_policy()


@router.post(
    "",
    response_model=DocumentResponse,
)
def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    owner_id = current_user.id
    db.rollback()
    original_filename = Path(
        file.filename or "document"
    ).name

    if (
        len(original_filename)
        > MAX_FILENAME_LENGTH
    ):
        raise HTTPException(
            status_code=400,
            detail="Filename is too long",
        )

    extension = Path(
        original_filename
    ).suffix.lower()

    if extension not in SUPPORTED_FILE_TYPES:
        raise DocumentResourceError("unsupported_file", 400)

    file_size = validate_file_size(
        file
    )

    with upload_quota_session(owner_id, incoming_bytes=file_size) as quota_db:
        # FastAPI runs this synchronous acceptance path in its thread pool:
        # filesystem, quota/DB and broker waits must not block the event loop.
        with upload_phase("upload_validation"):
            validate_file_content(file, extension)

        try:
            with upload_phase("upload_persistence"):
                stored_original = store_original(
                    file.file,
                    extension,
                    file_size,
                    MAX_UPLOAD_SIZE_BYTES,
                    UPLOAD_DIR,
                )

        except Exception as error:
            if isinstance(
                error,
                HTTPException,
            ):
                raise

            raise HTTPException(
                status_code=500,
                detail=(
                    "Could not save uploaded file"
                ),
            ) from error

        document = Document(
            user_id=owner_id,
            filename=original_filename,
            file_type=extension.lstrip("."),
            file_path=stored_original.file_path,
            file_size_bytes=stored_original.file_size_bytes,
            storage_key=stored_original.storage_key,
            file_sha256=stored_original.file_sha256,
            pages_count=None,

            processing_status="processing",
            processing_stage="uploaded",
            processing_progress=5,
            processing_error=None,
        )

        try:
            with upload_phase("upload_record"):
                quota_db.add(
                    document
                )

                quota_db.commit()

                quota_db.refresh(
                    document
                )

        except Exception as error:
            quota_db.rollback()
            cleanup_original_storage(
                stored_original.file_path,
                stored_original.storage_key,
            )

            raise HTTPException(
                status_code=500,
                detail="Could not create document",
            ) from error

        # Copy the response before ending the refresh transaction. Dispatch must not
        # retain a database transaction while waiting on the broker.
        response = DocumentResponse.model_validate(document)
        document_id = document.id
        quota_db.rollback()
    return dispatch_uploaded_document(
        db=db,
        background_tasks=background_tasks,
        document_id=document_id,
        file_path=stored_original.file_path,
        response=response,
    )


def cleanup_original_storage(file_path, storage_key, document_id=None):
    if storage_key:
        try:
            delete_stored_original(storage_key)
        except Exception as error:
            log_exception(logger, "delete_document_original", error, document_id=document_id)
    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception as error:
            log_exception(logger, "delete_document_file", error, document_id=document_id)


def dispatch_uploaded_document(db, background_tasks, document_id, file_path, response):
    log_event(logger, logging.INFO, "document_dispatch_attempted", document_id=document_id)
    try:
        with upload_phase("upload_enqueue", document_id=document_id):
            enqueue_document_processing(background_tasks, document_id, file_path)
    except Exception as error:
        log_generation_failure(error, "document", document_id=document_id)
        # An ambiguous broker response may already have reached a worker. Never
        # overwrite a worker's progress or successful completion.
        db.execute(update(Document).where(
            Document.id == document_id,
            Document.processing_status == "processing",
            Document.processing_stage == "uploaded",
        ).values(
            processing_status="failed", processing_stage="dispatch_failed",
            processing_error="Document processing could not be scheduled. Please retry.",
        ).execution_options(synchronize_session=False))
        db.commit()
        document = db.get(Document, document_id, populate_existing=True)
        if document is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="Document not found") from None
        response = DocumentResponse.model_validate(document)
        db.rollback()
        log_event(logger, logging.WARNING, "document_dispatch_failed", document_id=document_id)
    return response


@router.post("/{document_id}/retry", response_model=DocumentResponse)
def retry_document_processing(
    document_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    owner_id = current_user.id
    get_owned_document(db, document_id, current_user)
    db.rollback()
    with claim_document_processing(document_id) as claim:
        if claim is None:
            raise HTTPException(status_code=409, detail="Document processing is already active")
        with upload_quota_session(owner_id, retry_document_id=document_id):
            with claim.session() as processing_db:
                document = processing_db.get(Document, document_id, with_for_update=True)
                if document is None or document.user_id != owner_id:
                    raise HTTPException(status_code=404, detail="Document not found")
                if document.processing_status == "ready":
                    if inspect_embeddings(processing_db, [document_id])[0].complete:
                        return DocumentResponse.model_validate(document)
                    raise HTTPException(status_code=409, detail="Document requires explicit embedding recovery")
                if not (getattr(document, "storage_key", None) or document.file_path):
                    raise HTTPException(status_code=409, detail="Document source is unavailable")
                document.processing_status = "processing"
                document.processing_stage = "uploaded"
                document.processing_progress = 5
                document.processing_error = None
                file_path = document.file_path
                response = DocumentResponse.model_validate(document)
    # Release the claim before submission so an immediately delivered task can run.
    return dispatch_uploaded_document(db, background_tasks, document_id, file_path, response)


@router.get(
    "",
    response_model=list[DocumentResponse],
)
def get_documents(response: Response, limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
                  cursor: str | None = Query(None, max_length=2048),
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(Document).filter(Document.user_id == current_user.id)
    return paginated(query, [(Document.created_at, True), (Document.id, True)],
                     owner=current_user.id, scope='documents', response=response, limit=limit, cursor=cursor)


@router.get(
    "/{document_id:int}",
    response_model=DocumentResponse,
)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    return get_owned_document(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )


@router.delete(
    "/{document_id}"
)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    document = get_owned_document(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )

    file_path = None
    storage_key = getattr(document, "storage_key", None)

    if document.file_path:
        file_path = Path(
            document.file_path
        )

    try:
        db.delete(
            document
        )

        db.commit()

    except Exception as error:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not delete document"
            ),
        ) from error

    cleanup_original_storage(file_path, storage_key, document_id=document_id)

    return {
        "message": (
            "Document deleted successfully"
        ),
        "document_id": document_id,
    }
