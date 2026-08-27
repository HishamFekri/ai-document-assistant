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



SCOPE_STOPWORDS = {
    "the",
    "this",
    "that",
    "a",
    "an",
    "of",
    "only",
    "section",
    "chapter",
    "part",
    "topic",
    "about",
    "explain",
    "summarize",
    "summary",
    "translate",
    "document",
    "file",
    "قسم",
    "القسم",
    "الفصل",
    "جزء",
    "الجزء",
    "اشرح",
    "شرح",
    "لخص",
    "لخّص",
    "ترجم",
    "فقط",
    "عن",
    "حول",
    "الملف",
    "المستند",
}


def normalize_scope_query(
    value: str | None,
) -> str:
    value = normalize_text(
        value
    ).casefold()

    if not value:
        return ""

    value = re.sub(
        r"[^\w\u0600-\u06ff]+",
        " ",
        value,
        flags=re.UNICODE,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def scope_query_tokens(
    value: str | None,
) -> list[str]:
    normalized = normalize_scope_query(
        value
    )

    if not normalized:
        return []

    tokens = []

    for token in normalized.split():
        if (
            len(token) < 2
            or token in SCOPE_STOPWORDS
        ):
            continue

        if token not in tokens:
            tokens.append(
                token
            )

    return tokens


def build_page_search_text(
    page: dict,
) -> str:
    parts = [
        page.get(
            "text_context"
        )
        or "",
        page.get(
            "asset_context"
        )
        or "",
    ]

    return normalize_scope_query(
        "\n".join(parts)
    )


def score_page_for_scope(
    page: dict,
    scope_query: str,
) -> float:
    page_text = build_page_search_text(
        page
    )

    if not page_text:
        return 0.0

    normalized_query = (
        normalize_scope_query(
            scope_query
        )
    )

    tokens = scope_query_tokens(
        scope_query
    )

    score = 0.0

    if (
        normalized_query
        and normalized_query
        in page_text
    ):
        score += 8.0

    for token in tokens:
        occurrences = (
            page_text.count(
                token
            )
        )

        if occurrences:
            score += min(
                5.0,
                1.0
                + occurrences * 0.8,
            )

    return score


def choose_best_page_run(
    scored_pages: list[
        tuple[int, float]
    ],
) -> list[int]:
    if not scored_pages:
        return []

    scored_pages = sorted(
        scored_pages
    )

    runs: list[
        list[
            tuple[int, float]
        ]
    ] = []

    current = [
        scored_pages[0]
    ]

    for item in scored_pages[1:]:
        previous_page = (
            current[-1][0]
        )

        if (
            item[0]
            <= previous_page + 2
        ):
            current.append(
                item
            )

        else:
            runs.append(
                current
            )

            current = [
                item
            ]

    runs.append(
        current
    )

    def run_score(
        run: list[
            tuple[int, float]
        ],
    ) -> float:
        return (
            sum(
                score
                for _, score
                in run
            )
            + len(run) * 1.5
        )

    best_run = max(
        runs,
        key=run_score,
    )

    return [
        page
        for page, _
        in best_run
    ]


def find_scope_page_numbers(
    db: Session,
    document: Document,
    scope_query: str,
    max_pages: int = 80,
) -> list[int]:
    """
    Find the pages that best represent a requested section/topic.

    The method is intentionally deterministic and local:
    it uses extracted page text/assets, keeps contiguous page runs,
    and avoids sending the full document to another LLM just to
    determine scope.
    """
    normalized_query = (
        normalize_scope_query(
            scope_query
        )
    )

    if not normalized_query:
        return []

    pages = build_transcription_pages(
        db=db,
        document=document,
    )

    scored = []

    for page in pages:
        score = score_page_for_scope(
            page=page,
            scope_query=(
                normalized_query
            ),
        )

        if score <= 0:
            continue

        scored.append(
            (
                int(
                    page[
                        "page_number"
                    ]
                ),
                score,
            )
        )

    if not scored:
        return []

    best_run = choose_best_page_run(
        scored
    )

    # A repeated section heading usually creates a useful contiguous
    # run (for example "Engine" on every page of an engine chapter).
    if len(best_run) >= 2:
        selected = best_run

    else:
        # Topic-like requests may only hit one or a few pages.
        ranked = sorted(
            scored,
            key=lambda item:
                item[1],
            reverse=True,
        )

        selected_set = set()

        for page_number, _ in ranked[
            :min(
                8,
                len(ranked),
            )
        ]:
            selected_set.add(
                page_number
            )

            if page_number > 1:
                selected_set.add(
                    page_number - 1
                )

            if (
                not document.pages_count
                or page_number
                < document.pages_count
            ):
                selected_set.add(
                    page_number + 1
                )

        selected = sorted(
            selected_set
        )

    if len(selected) > max_pages:
        selected = selected[
            :max_pages
        ]

    return selected


def build_text_context(
    db: Session,
    document_id: int,
    max_chars: int,
    page_numbers:
        set[int] | None = None,
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
        if page_numbers is not None:
            page_number = (
                get_chunk_page_number(
                    chunk
                )
            )

            if (
                page_number
                not in page_numbers
            ):
                continue

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
    page_numbers:
        set[int] | None = None,
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
        if page_numbers is not None:
            page_number = (
                get_asset_page_number(
                    asset
                )
            )

            if (
                page_number
                not in page_numbers
            ):
                continue

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
    selected_page_numbers:
        set[int] | None = None,
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

    if selected_page_numbers is not None:
        page_numbers = [
            page_number
            for page_number
            in page_numbers
            if page_number
            in selected_page_numbers
        ]

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
    page_numbers:
        list[int] | None = None,
) -> dict:
    selected_pages = (
        set(page_numbers)
        if page_numbers
        else None
    )

    text_context = build_text_context(
        db=db,
        document_id=document.id,
        max_chars=max_text_chars,
        page_numbers=(
            selected_pages
        ),
    )

    asset_context = build_asset_context(
        db=db,
        document_id=document.id,
        max_chars=max_asset_chars,
        page_numbers=(
            selected_pages
        ),
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
        "selected_pages":
            (
                sorted(
                    selected_pages
                )
                if selected_pages
                else None
            ),
    }


def get_document_transcription_context(
    db: Session,
    document: Document,
    page_numbers:
        list[int] | None = None,
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
                selected_page_numbers=(
                    set(page_numbers)
                    if page_numbers
                    else None
                ),
            ),
    }