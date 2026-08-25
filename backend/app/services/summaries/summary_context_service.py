import re

from sqlalchemy.orm import Session

from app.database.models import (
    Document,
    DocumentChunk,
)

from app.database.document_asset_models import (
    DocumentAsset,
)


DEFAULT_MAX_TEXT_CHARS = 32000
DEFAULT_MAX_ASSET_CHARS = 12000

DEFAULT_MAX_TRANSCRIPTION_PAGE_TEXT_CHARS = 16000
DEFAULT_MAX_TRANSCRIPTION_PAGE_ASSET_CHARS = 10000


def normalize_text(
    value: str | None,
) -> str:
    if not value:
        return ""

    return (
        str(value)
        .replace("\x00", "")
        .strip()
    )


def trim_text(
    value: str,
    max_chars: int,
) -> str:
    value = normalize_text(
        value
    )

    if len(value) <= max_chars:
        return value

    return (
        value[:max_chars]
        .rstrip()
        + "\n\n[Content truncated]"
    )


def extract_page_number(
    location: str | None,
    metadata: dict | None = None,
) -> int | None:
    metadata = (
        metadata
        if isinstance(
            metadata,
            dict,
        )
        else {}
    )

    metadata_page = (
        metadata.get("page")
        or metadata.get("page_number")
        or metadata.get("page_num")
    )

    if metadata_page is not None:
        try:
            page_number = int(
                metadata_page
            )

            if page_number > 0:
                return page_number

        except (
            TypeError,
            ValueError,
        ):
            pass

    normalized_location = (
        normalize_text(
            location
        )
    )

    if not normalized_location:
        return None

    patterns = [
        r"\bpage\s*[:#\-]?\s*(\d+)\b",
        r"\bpage\s+(\d+)\b",
        r"صفحة\s*[:#\-]?\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            normalized_location,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        try:
            page_number = int(
                match.group(1)
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if page_number > 0:
            return page_number

    return None


def get_chunk_page_number(
    chunk: DocumentChunk,
) -> int | None:
    return extract_page_number(
        location=chunk.location,
        metadata=(
            chunk.chunk_metadata
            or {}
        ),
    )


def get_asset_page_number(
    asset: DocumentAsset,
) -> int | None:
    return extract_page_number(
        location=asset.location,
        metadata=(
            asset.asset_metadata
            or {}
        ),
    )


def format_chunk(
    chunk: DocumentChunk,
) -> str:
    location = normalize_text(
        chunk.location
    )

    content_type = normalize_text(
        chunk.content_type
    )

    content = normalize_text(
        chunk.content
    )

    parts = []

    if location:
        parts.append(
            f"Location: {location}"
        )

    if content_type:
        parts.append(
            f"Type: {content_type}"
        )

    if content:
        parts.append(
            content
        )

    return "\n".join(
        parts
    )


def format_image_asset(
    asset: DocumentAsset,
) -> str:
    location = normalize_text(
        asset.location
    )

    title = normalize_text(
        asset.title
    )

    caption = normalize_text(
        asset.caption
    )

    parts = [
        "[IMAGE]"
    ]

    if location:
        parts.append(
            f"Location: {location}"
        )

    if title:
        parts.append(
            f"Title: {title}"
        )

    if caption:
        parts.append(
            f"Description: {caption}"
        )

    parts.append(
        f"Asset ID: {asset.id}"
    )

    return "\n".join(
        parts
    )


def format_table_asset(
    asset: DocumentAsset,
) -> str:
    location = normalize_text(
        asset.location
    )

    title = normalize_text(
        asset.title
    )

    caption = normalize_text(
        asset.caption
    )

    content = normalize_text(
        asset.content
    )

    parts = [
        "[TABLE]"
    ]

    if location:
        parts.append(
            f"Location: {location}"
        )

    if title:
        parts.append(
            f"Title: {title}"
        )

    if caption:
        parts.append(
            f"Caption: {caption}"
        )

    if content:
        parts.append(
            content
        )

    parts.append(
        f"Asset ID: {asset.id}"
    )

    return "\n".join(
        parts
    )


def format_equation_asset(
    asset: DocumentAsset,
) -> str:
    location = normalize_text(
        asset.location
    )

    title = normalize_text(
        asset.title
    )

    caption = normalize_text(
        asset.caption
    )

    content = normalize_text(
        asset.content
    )

    parts = [
        "[EQUATION]"
    ]

    if location:
        parts.append(
            f"Location: {location}"
        )

    if title:
        parts.append(
            f"Title: {title}"
        )

    if caption:
        parts.append(
            f"Caption: {caption}"
        )

    if content:
        parts.append(
            content
        )

    parts.append(
        f"Asset ID: {asset.id}"
    )

    return "\n".join(
        parts
    )


def format_asset(
    asset: DocumentAsset,
) -> str:
    if asset.asset_type == "image":
        return format_image_asset(
            asset
        )

    if asset.asset_type == "table":
        return format_table_asset(
            asset
        )

    if asset.asset_type == "equation":
        return format_equation_asset(
            asset
        )

    return ""


def build_text_context(
    db: Session,
    document_id: int,
    max_chars: int,
) -> str:
    chunks = (
        db.query(
            DocumentChunk
        )
        .filter(
            DocumentChunk.document_id
            == document_id
        )
        .order_by(
            DocumentChunk.id.asc()
        )
        .all()
    )

    if not chunks:
        return ""

    collected = []

    current_length = 0

    for chunk in chunks:
        formatted = format_chunk(
            chunk
        )

        if not formatted:
            continue

        block = (
            "\n\n---\n\n"
            + formatted
        )

        if (
            current_length
            + len(block)
            > max_chars
        ):
            remaining = (
                max_chars
                - current_length
            )

            if remaining > 300:
                collected.append(
                    block[:remaining]
                )

            break

        collected.append(
            block
        )

        current_length += len(
            block
        )

    return "".join(
        collected
    ).strip()


def build_asset_context(
    db: Session,
    document_id: int,
    max_chars: int,
) -> str:
    assets = (
        db.query(
            DocumentAsset
        )
        .filter(
            DocumentAsset.document_id
            == document_id
        )
        .order_by(
            DocumentAsset.id.asc()
        )
        .all()
    )

    if not assets:
        return ""

    collected = []

    current_length = 0

    for asset in assets:
        formatted = format_asset(
            asset
        )

        if not formatted:
            continue

        block = (
            "\n\n---\n\n"
            + formatted
        )

        if (
            current_length
            + len(block)
            > max_chars
        ):
            remaining = (
                max_chars
                - current_length
            )

            if remaining > 300:
                collected.append(
                    block[:remaining]
                )

            break

        collected.append(
            block
        )

        current_length += len(
            block
        )

    return "".join(
        collected
    ).strip()


def build_transcription_pages(
    db: Session,
    document: Document,
    max_page_text_chars: int = (
        DEFAULT_MAX_TRANSCRIPTION_PAGE_TEXT_CHARS
    ),
    max_page_asset_chars: int = (
        DEFAULT_MAX_TRANSCRIPTION_PAGE_ASSET_CHARS
    ),
) -> list[dict]:
    chunks = (
        db.query(
            DocumentChunk
        )
        .filter(
            DocumentChunk.document_id
            == document.id
        )
        .order_by(
            DocumentChunk.id.asc()
        )
        .all()
    )

    assets = (
        db.query(
            DocumentAsset
        )
        .filter(
            DocumentAsset.document_id
            == document.id
        )
        .order_by(
            DocumentAsset.id.asc()
        )
        .all()
    )

    chunks_by_page: dict[
        int,
        list[DocumentChunk],
    ] = {}

    assets_by_page: dict[
        int,
        list[DocumentAsset],
    ] = {}

    unassigned_chunks = []
    unassigned_assets = []

    for chunk in chunks:
        page_number = (
            get_chunk_page_number(
                chunk
            )
        )

        if page_number is None:
            unassigned_chunks.append(
                chunk
            )

            continue

        chunks_by_page.setdefault(
            page_number,
            [],
        ).append(
            chunk
        )

    for asset in assets:
        page_number = (
            get_asset_page_number(
                asset
            )
        )

        if page_number is None:
            unassigned_assets.append(
                asset
            )

            continue

        assets_by_page.setdefault(
            page_number,
            [],
        ).append(
            asset
        )

    discovered_pages = set(
        chunks_by_page.keys()
    ) | set(
        assets_by_page.keys()
    )

    pages_count = (
        document.pages_count
        or 0
    )

    if pages_count > 0:
        page_numbers = list(
            range(
                1,
                pages_count + 1,
            )
        )

    else:
        page_numbers = sorted(
            discovered_pages
        )

    pages = []

    for page_number in page_numbers:
        page_chunks = (
            chunks_by_page.get(
                page_number,
                [],
            )
        )

        page_assets = (
            assets_by_page.get(
                page_number,
                [],
            )
        )

        text_parts = []

        for chunk in page_chunks:
            formatted = format_chunk(
                chunk
            )

            if formatted:
                text_parts.append(
                    formatted
                )

        asset_parts = []

        asset_items = []

        for asset in page_assets:
            formatted = format_asset(
                asset
            )

            if formatted:
                asset_parts.append(
                    formatted
                )

            asset_items.append(
                {
                    "id":
                        asset.id,

                    "type":
                        asset.asset_type,

                    "location":
                        asset.location,

                    "title":
                        asset.title,

                    "caption":
                        asset.caption,

                    "content":
                        asset.content,

                    "file_path":
                        asset.file_path,
                }
            )

        text_context = trim_text(
            "\n\n---\n\n".join(
                text_parts
            ),
            max_page_text_chars,
        )

        asset_context = trim_text(
            "\n\n---\n\n".join(
                asset_parts
            ),
            max_page_asset_chars,
        )

        pages.append(
            {
                "page_number":
                    page_number,

                "text_context":
                    text_context,

                "asset_context":
                    asset_context,

                "assets":
                    asset_items,

                "has_content":
                    bool(
                        text_context
                        or asset_context
                    ),
            }
        )

    if (
        not pages
        and (
            unassigned_chunks
            or unassigned_assets
        )
    ):
        text_parts = [
            format_chunk(
                chunk
            )
            for chunk
            in unassigned_chunks
        ]

        asset_parts = [
            format_asset(
                asset
            )
            for asset
            in unassigned_assets
        ]

        asset_items = [
            {
                "id":
                    asset.id,

                "type":
                    asset.asset_type,

                "location":
                    asset.location,

                "title":
                    asset.title,

                "caption":
                    asset.caption,

                "content":
                    asset.content,

                "file_path":
                    asset.file_path,
            }
            for asset
            in unassigned_assets
        ]

        pages.append(
            {
                "page_number":
                    1,

                "text_context":
                    trim_text(
                        "\n\n---\n\n".join(
                            part
                            for part
                            in text_parts
                            if part
                        ),
                        max_page_text_chars,
                    ),

                "asset_context":
                    trim_text(
                        "\n\n---\n\n".join(
                            part
                            for part
                            in asset_parts
                            if part
                        ),
                        max_page_asset_chars,
                    ),

                "assets":
                    asset_items,

                "has_content":
                    True,
            }
        )

    return pages


def get_document_summary_context(
    db: Session,
    document: Document,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    max_asset_chars: int = DEFAULT_MAX_ASSET_CHARS,
) -> dict:
    text_context = build_text_context(
        db=db,
        document_id=document.id,
        max_chars=max_text_chars,
    )

    asset_context = build_asset_context(
        db=db,
        document_id=document.id,
        max_chars=max_asset_chars,
    )

    return {
        "document": {
            "id": document.id,
            "filename": document.filename,
            "file_type": document.file_type,
            "pages_count": document.pages_count,
        },
        "text_context": trim_text(
            text_context,
            max_text_chars,
        ),
        "asset_context": trim_text(
            asset_context,
            max_asset_chars,
        ),
    }


def get_document_transcription_context(
    db: Session,
    document: Document,
) -> dict:
    return {
        "document": {
            "id":
                document.id,

            "filename":
                document.filename,

            "file_type":
                document.file_type,

            "pages_count":
                document.pages_count,
        },

        "pages":
            build_transcription_pages(
                db=db,
                document=document,
            ),
    }