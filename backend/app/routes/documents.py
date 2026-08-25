import os
import zipfile

from pathlib import Path
from uuid import uuid4

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


load_dotenv()


MAX_UPLOAD_SIZE_MB = int(
    os.getenv(
        "MAX_UPLOAD_SIZE_MB",
        "50",
    )
)

MAX_UPLOAD_SIZE_BYTES = (
    MAX_UPLOAD_SIZE_MB
    * 1024
    * 1024
)

MAX_FILENAME_LENGTH = 255


router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
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


async def validate_file_size(
    file: UploadFile,
):
    file_size = get_file_size(
        file
    )

    if file_size <= 0:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty",
        )

    if file_size > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File is too large. "
                f"Maximum allowed size is "
                f"{MAX_UPLOAD_SIZE_MB} MB."
            ),
        )


def validate_pdf(
    file: UploadFile,
):
    file.file.seek(0)

    header = file.file.read(
        5
    )

    file.file.seek(0)

    if header != b"%PDF-":
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF file",
        )


def validate_office_zip(
    file: UploadFile,
    extension: str,
):
    file.file.seek(0)

    try:
        if not zipfile.is_zipfile(
            file.file
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid "
                    f"{extension.upper().lstrip('.')} "
                    f"file"
                ),
            )

        file.file.seek(0)

        with zipfile.ZipFile(
            file.file
        ) as archive:
            names = (
                archive.namelist()
            )

            if (
                "[Content_Types].xml"
                not in names
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Invalid Office document"
                    ),
                )

            if extension == ".docx":
                valid = any(
                    name.startswith(
                        "word/"
                    )
                    for name in names
                )

            elif extension == ".xlsx":
                valid = any(
                    name.startswith(
                        "xl/"
                    )
                    for name in names
                )

            else:
                valid = False

            if not valid:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"File content does not "
                        f"match {extension}"
                    ),
                )

    except zipfile.BadZipFile as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid Office document",
        ) from error

    finally:
        file.file.seek(0)


def validate_txt(
    file: UploadFile,
):
    file.file.seek(0)

    sample = file.file.read(
        8192
    )

    file.file.seek(0)

    if b"\x00" in sample:
        raise HTTPException(
            status_code=400,
            detail="Invalid TXT file",
        )

    try:
        sample.decode(
            "utf-8-sig"
        )

    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "TXT files must use "
                "UTF-8 encoding"
            ),
        ) from error


def validate_file_content(
    file: UploadFile,
    extension: str,
):
    if extension == ".pdf":
        validate_pdf(
            file
        )

    elif extension in {
        ".docx",
        ".xlsx",
    }:
        validate_office_zip(
            file,
            extension,
        )

    elif extension == ".txt":
        validate_txt(
            file
        )

    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type",
        )


@router.post(
    "",
    response_model=DocumentResponse,
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
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
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. "
                "Allowed: PDF, DOCX, XLSX, TXT"
            ),
        )

    await validate_file_size(
        file
    )

    validate_file_content(
        file,
        extension,
    )

    stored_filename = (
        f"{uuid4().hex}"
        f"{extension}"
    )

    file_path = (
        UPLOAD_DIR
        / stored_filename
    )

    try:
        bytes_written = 0

        file.file.seek(0)

        with open(
            file_path,
            "wb",
        ) as buffer:
            while True:
                chunk = file.file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                bytes_written += len(
                    chunk
                )

                if (
                    bytes_written
                    > MAX_UPLOAD_SIZE_BYTES
                ):
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "File is too large. "
                            "Maximum allowed size is "
                            f"{MAX_UPLOAD_SIZE_MB} MB."
                        ),
                    )

                buffer.write(
                    chunk
                )

    except Exception as error:
        file_path.unlink(
            missing_ok=True
        )

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
        user_id=current_user.id,
        filename=original_filename,
        file_type=extension.lstrip("."),
        file_path=str(file_path),
        pages_count=None,

        processing_status="processing",
        processing_stage="uploaded",
        processing_progress=5,
        processing_error=None,
    )

    try:
        db.add(
            document
        )

        db.commit()

        db.refresh(
            document
        )

    except Exception as error:
        db.rollback()

        file_path.unlink(
            missing_ok=True
        )

        raise HTTPException(
            status_code=500,
            detail="Could not create document",
        ) from error

    enqueue_document_processing(
        background_tasks=background_tasks,
        document_id=document.id,
        file_path=str(file_path),
    )

    return document


@router.get(
    "",
    response_model=list[DocumentResponse],
)
def get_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    documents = (
        db.query(Document)
        .filter(
            Document.user_id
            == current_user.id
        )
        .order_by(
            Document.created_at.desc()
        )
        .all()
    )

    return documents


@router.get(
    "/{document_id}",
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

    if file_path:
        try:
            file_path.unlink(
                missing_ok=True
            )

        except Exception as error:
            print(
                "[WARNING] Could not "
                "delete physical file: "
                f"{error}"
            )

    return {
        "message": (
            "Document deleted successfully"
        ),
        "document_id": document_id,
    }