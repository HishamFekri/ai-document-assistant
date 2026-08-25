def chunk_text(
    text: str,
    chunk_size: int = 300,
    overlap: int = 50,
):
    words = text.split()

    if not words:
        return []

    chunks = []
    start = 0

    while start < len(words):
        end = start + chunk_size

        chunk_words = words[
            start:end
        ]

        chunks.append(
            " ".join(
                chunk_words
            )
        )

        start += (
            chunk_size
            - overlap
        )

    return chunks


def chunk_table(
    content: str,
):
    if not content.strip():
        return []

    return [
        content.strip()
    ]


def chunk_formula(
    content: str,
):
    if not content.strip():
        return []

    return [
        content.strip()
    ]


def chunk_code(
    content: str,
):
    if not content.strip():
        return []

    return [
        content.strip()
    ]


def chunk_image(
    content: str,
    metadata: dict,
):
    cleaned_content = (
        content.strip()
        if content
        else ""
    )

    if cleaned_content:
        return [
            cleaned_content
        ]

    asset_filename = (
        metadata.get(
            "asset_filename"
        )
    )

    page = metadata.get(
        "page"
    )

    if asset_filename:
        fallback_description = (
            "Image extracted from the document"
        )

        if page:
            fallback_description += (
                f" on page {page}"
            )

        fallback_description += (
            f". Asset: {asset_filename}"
        )

        return [
            fallback_description
        ]

    return []


def create_chunks_from_content(
    blocks: list[dict],
    chunk_size: int = 300,
    overlap: int = 50,
):
    result = []

    for block in blocks:
        block_type = (
            block.get(
                "type",
                "text",
            )
        )

        content = (
            block.get(
                "content",
                "",
            )
            or ""
        )

        metadata = (
            block.get(
                "metadata",
                {},
            )
            or {}
        )

        if block_type == "text":
            chunks = chunk_text(
                content,
                chunk_size,
                overlap,
            )

        elif block_type == "table":
            chunks = chunk_table(
                content
            )

        elif block_type == "formula":
            chunks = chunk_formula(
                content
            )

        elif block_type == "code":
            chunks = chunk_code(
                content
            )

        elif block_type == "image":
            chunks = chunk_image(
                content=content,
                metadata=metadata,
            )

        else:
            chunks = chunk_text(
                content,
                chunk_size,
                overlap,
            )

        for chunk in chunks:
            result.append(
                {
                    "content": chunk,
                    "content_type": block_type,
                    "location": block.get(
                        "location"
                    ),
                    "metadata": metadata,
                }
            )

    return result