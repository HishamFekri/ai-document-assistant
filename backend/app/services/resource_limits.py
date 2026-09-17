"""Central resource policy. No network access or secrets printed at import."""

from dataclasses import dataclass, field
from functools import lru_cache
import os
from app.services.environment import is_production_environment


@dataclass(frozen=True)
class RatePolicy:
    limit: int
    seconds: int


RATE_DEFAULTS = {
    "api": (120, 60), "auth": (30, 60), "recovery": (30, 60),
    "search": (20, 60), "chat": (10, 60),
    "upload": (5, 3600), "summary": (3, 3600),
}
CONCURRENCY_DEFAULTS = {"chat": 2, "search": 2, "summary": 1, "processing": 1}
ROUTE_POLICIES = {
    "search_chat_documents": "search",
    "ask_chat": "chat", "ask_chat_stream": "chat",
    "create_summary_assistant_message": "chat",
    "upload_document": "upload", "retry_document_processing": "upload",
    "create_document_summary": "summary", "stream_document_summary": "summary",
    "cancel_document_summary": "recovery",
}


def positive_setting(name, default, maximum=1_000_000):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"Invalid resource setting: {name}") from None
    if not 0 < value <= maximum:
        raise ValueError(f"Invalid resource setting: {name}")
    return value


@dataclass(frozen=True)
class ResourceLimits:
    rates: dict
    concurrency: dict
    max_documents: int
    max_original_bytes: int
    redis_url: str | None = field(repr=False)


@lru_cache(maxsize=1)
def resource_limits():
    production = is_production_environment()
    return ResourceLimits(
        rates={name: RatePolicy(
            positive_setting(f"RESOURCE_{name.upper()}_LIMIT", limit),
            positive_setting(f"RESOURCE_{name.upper()}_WINDOW_SECONDS", seconds, 86400),
        ) for name, (limit, seconds) in RATE_DEFAULTS.items()},
        concurrency={name: positive_setting(
            f"RESOURCE_{name.upper()}_CONCURRENCY", count, 64,
        ) for name, count in CONCURRENCY_DEFAULTS.items()},
        max_documents=positive_setting("RESOURCE_MAX_DOCUMENTS", 100),
        max_original_bytes=positive_setting("RESOURCE_MAX_ORIGINAL_BYTES", 1024**3, 1024**5),
        redis_url=(os.getenv("RESOURCE_REDIS_URL") or os.getenv("CELERY_BROKER_URL")
                   or (None if production else "redis://127.0.0.1:6379/2")),
    )


def request_policy(request):
    if request.method == "DELETE":
        return "recovery"
    route = request.scope.get("route")
    return ROUTE_POLICIES.get(getattr(route, "name", ""), "api")


SUPPORTED_UPLOAD_TYPES = (".pdf", ".docx", ".xlsx", ".txt")


@dataclass(frozen=True)
class UploadLimits:
    file_bytes: int
    multipart_overhead_bytes: int
    pdf_pages: int
    datalab_batch_size: int
    datalab_document_pages: int
    datalab_parallel_batches: int
    datalab_response_bytes: int
    zip_entries: int
    zip_expanded_bytes: int
    zip_entry_bytes: int
    zip_ratio: int
    xml_nodes: int
    xml_depth: int
    worksheets: int
    sheet_rows: int
    total_rows: int
    columns: int
    cells: int
    docx_paragraphs: int
    docx_tables: int
    text_chars: int
    text_bytes: int
    blocks: int
    chunks: int
    pdf_page_content_bytes: int
    pdf_content_bytes: int

    @property
    def request_bytes(self):
        return self.file_bytes + self.multipart_overhead_bytes

    def public_policy(self):
        return {"supported_extensions": list(SUPPORTED_UPLOAD_TYPES),
                "max_file_bytes": self.file_bytes, "max_pdf_pages": self.pdf_pages}


@lru_cache(maxsize=1)
def upload_limits():
    mib = 1024**2
    size = lambda name, default: positive_setting(name, default, 1024**4)
    count = positive_setting
    return UploadLimits(
        file_bytes=count("MAX_UPLOAD_SIZE_MB", 50, 1024) * mib,
        multipart_overhead_bytes=size("MAX_MULTIPART_OVERHEAD_BYTES", mib),
        pdf_pages=count("MAX_PDF_PAGES", 500),
        # Preserve the old batch-size setting as an explicit compatibility alias.
        datalab_batch_size=count("DATALAB_BATCH_SIZE", count("MAX_DATALAB_PAGES", 20)),
        datalab_document_pages=count("MAX_DATALAB_PAGES_PER_DOCUMENT", 40),
        datalab_parallel_batches=count("DATALAB_PARALLEL_BATCHES", 2, 8),
        datalab_response_bytes=size("MAX_DATALAB_RESPONSE_BYTES", 32 * mib),
        zip_entries=count("MAX_OFFICE_ZIP_ENTRIES", 1000),
        zip_expanded_bytes=size("MAX_OFFICE_EXPANDED_BYTES", 200 * mib),
        zip_entry_bytes=size("MAX_OFFICE_ENTRY_BYTES", 32 * mib),
        zip_ratio=count("MAX_OFFICE_COMPRESSION_RATIO", 200),
        xml_nodes=count("MAX_OFFICE_XML_NODES", 2_000_000, 10_000_000),
        xml_depth=count("MAX_OFFICE_XML_DEPTH", 64, 256),
        worksheets=count("MAX_XLSX_WORKSHEETS", 20),
        sheet_rows=count("MAX_XLSX_ROWS_PER_SHEET", 20_000),
        total_rows=count("MAX_XLSX_TOTAL_ROWS", 50_000),
        columns=count("MAX_XLSX_COLUMNS", 256, 16384),
        cells=count("MAX_XLSX_CELLS", 500_000, 10_000_000),
        docx_paragraphs=count("MAX_DOCX_PARAGRAPHS", 20_000),
        docx_tables=count("MAX_DOCX_TABLES", 1000),
        text_chars=count("MAX_EXTRACTED_CHARACTERS", 2_000_000, 100_000_000),
        text_bytes=size("MAX_EXTRACTED_TEXT_BYTES", 8 * mib),
        blocks=count("MAX_EXTRACTED_BLOCKS", 10_000),
        chunks=count("MAX_DOCUMENT_CHUNKS", 10_000),
        pdf_page_content_bytes=size("MAX_PDF_PAGE_CONTENT_BYTES", 8 * mib),
        pdf_content_bytes=size("MAX_PDF_CONTENT_BYTES", 64 * mib),
    )
