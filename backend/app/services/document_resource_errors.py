"""Only code-selected public messages; never expose parser/provider exceptions."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.services.resource_limits import upload_limits


def resource_messages():
    limits = upload_limits()
    return {
        "file_size": f"This file is too large. The maximum allowed size is {limits.file_bytes // 1024**2} MB.",
        "request_size": "This upload request is too large. Upload one file within the displayed size limit.",
        "empty_file": "Uploaded file is empty.",
        "unsupported_file": "Unsupported file type. Allowed: PDF, DOCX, XLSX, TXT.",
        "invalid_pdf": "This PDF could not be read. Please upload a valid, unencrypted PDF.",
        "pdf_pages": f"This PDF has too many pages. The maximum allowed is {limits.pdf_pages} pages.",
        "advanced_pages": "This PDF needs advanced extraction for too many pages. Split it into smaller files.",
        "office_expansion": "This compressed document expands beyond the processing limits. Please use a smaller file.",
        "invalid_office": "This Office document has an invalid or unsafe archive structure.",
        "spreadsheet_limit": "This spreadsheet is too large to process safely. Reduce its sheets, rows or columns.",
        "docx_limit": "This Word document is too large to process safely. Split it into smaller files.",
        "content_limit": "This document contains too much extractable content. Split it into smaller files.",
        "invalid_text": "TXT files must contain valid UTF-8 text without null characters.",
        "upload_form": "Upload exactly one file without additional form fields.",
    }


class DocumentResourceError(HTTPException):
    def __init__(self, code, status_code=413):
        self.code = code
        super().__init__(status_code=status_code, detail=resource_messages()[code])

    def response(self):
        return JSONResponse(status_code=self.status_code, content={"detail": self.detail, "code": self.code})


async def resource_validation_response(request, error):
    return error.response()
