"""Compatible chunk types and human-visible, one-based page references."""

import re

GENERIC_CONTENT_TYPES = ["text", "table", "equation", "formula", "code"]
PAGE_LOCATION = re.compile(r"(?:\bpage|صفحة)\s*[:#]?\s*(-?\d+(?:\.\d+)?)\b", re.I)


def canonical_content_type(value: str) -> str:
    return "equation" if value in {"formula", "equation"} else value


def compatible_content_types(values):
    result = list(dict.fromkeys(values))
    if {"formula", "equation"}.intersection(result):
        for value in ("equation", "formula"):
            if value not in result:
                result.append(value)
    return result


def integer_page(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def original_page(location, metadata=None):
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get("page_mapping_status") == "unknown":
        return None
    # An explicit but invalid page must not fall through to a contradictory label.
    values = [integer_page(metadata[key]) for key in ("page", "page_number", "page_num")
              if metadata.get(key) is not None]
    if values:
        page = values[0]
        return page if page is not None and page > 0 and all(v == page for v in values) else None
    match = PAGE_LOCATION.search(location or "")
    page = integer_page(match.group(1)) if match else None
    return page if page is not None and page > 0 else None


def normalized_location(location, metadata=None):
    page = original_page(location, metadata)
    if page is not None:
        return f"Page {page}"
    metadata = metadata if isinstance(metadata, dict) else {}
    if PAGE_LOCATION.search(location or "") or metadata.get("page_mapping_status") == "unknown" or any(
        metadata.get(key) is not None for key in ("page", "page_number", "page_num")
    ):
        return "Unknown location"
    return location


def source_location(chunk):
    return normalized_location(chunk.location, chunk.chunk_metadata)
