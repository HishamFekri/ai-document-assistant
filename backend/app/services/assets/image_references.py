"""Browser-facing image references; never mutate stored asset/chunk/message data."""

import re
from urllib.parse import quote, urlsplit


STORAGE_FIELDS = {"asset_path", "asset_url", "file_path", "image_url", "secure_url"}
CLOUDINARY_URL = re.compile(r"https?://res\.cloudinary\.com/[^\s\"'<>]+", re.IGNORECASE)


def safe_asset_filename(value) -> bool:
    return (
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and not any(char in value for char in "/\\:")
        and not any(ord(char) < 32 for char in value)
    )


def positive_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def asset_file_url(document_id: int, asset_id: int) -> str:
    return f"/documents/{document_id}/assets/{asset_id}/file"


def chunk_image_url(document_id: int, chunk_id: int) -> str:
    return f"/documents/{document_id}/image-chunks/{chunk_id}/file"


def normalize_image_metadata(value, image_path: str | None):
    """Copy metadata, replacing internal paths (including nested legacy values)."""
    if isinstance(value, dict):
        return {
            key: image_path if key in STORAGE_FIELDS else normalize_image_metadata(item, image_path)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_image_metadata(item, image_path) for item in value]
    if isinstance(value, str):
        return CLOUDINARY_URL.sub(lambda _: image_path or "[private image]", value)
    return value


def normalize_image_source(source):
    if not isinstance(source, dict):
        return source
    if source.get("content_type") != "image" and not source.get("asset_url"):
        return source

    document_id = source.get("document_id")
    image_path = None
    if positive_id(document_id):
        if positive_id(source.get("asset_id")):
            image_path = asset_file_url(document_id, source["asset_id"])
        elif positive_id(source.get("chunk_id")):
            # Chunk IDs also work for records predating DocumentAsset, and avoid
            # collisions between repeated extraction filenames on different pages.
            image_path = chunk_image_url(document_id, source["chunk_id"])
        else:
            filename = source.get("asset_filename")
            if not safe_asset_filename(filename):
                try:
                    origin = source.get("asset_url")
                    filename = urlsplit(origin).path.rsplit("/", 1)[-1] if isinstance(origin, str) else None
                except ValueError:
                    filename = None
            if safe_asset_filename(filename):
                image_path = f"/documents/{document_id}/assets/{quote(filename, safe='')}"

    normalized = normalize_image_metadata(source, image_path)
    normalized["asset_url"] = image_path
    return normalized


def normalize_sources(sources):
    if sources is None:
        return None
    return [normalize_image_source(source) for source in sources]
