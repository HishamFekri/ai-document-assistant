from pathlib import Path

from app.services.hybrid_pdf_service import (
    extract_content_from_hybrid_pdf,
)

from app.services.word_service import (
    extract_content_from_word,
)

from app.services.excel_service import (
    extract_content_from_excel,
)

from app.services.text_service import (
    extract_content_from_text,
)


SUPPORTED_FILE_TYPES = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".txt",
}


def extract_content(
    file_path,
    document_id: int | None = None,
):
    extension = Path(
        file_path
    ).suffix.lower()

    if extension == ".pdf":
        return extract_content_from_hybrid_pdf(
            file_path=file_path,
            document_id=document_id,
        )

    if extension == ".docx":
        return extract_content_from_word(
            file_path
        )

    if extension == ".xlsx":
        return extract_content_from_excel(
            file_path
        )

    if extension == ".txt":
        return extract_content_from_text(
            file_path
        )

    raise ValueError(
        f"Unsupported file type: {extension}"
    )