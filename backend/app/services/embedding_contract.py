"""Embedding format and provenance; importing this module needs no credentials."""

import math
import os
import struct


EMBEDDING_DIMENSION = 512
VOYAGE_MODEL = os.getenv("VOYAGE_MODEL", "voyage-4-lite")
GENERATION_KEY = "embedding_generation"


def embedding_generation() -> dict:
    # This describes our request format, not an immutable provider model revision.
    return {
        "provider": "voyage",
        "model": VOYAGE_MODEL,
        "dimension": EMBEDDING_DIMENSION,
        "input_type": "document",
        "output_dtype": "float",
        "format_version": 1,
    }


def with_embedding_generation(metadata: dict | None) -> dict:
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Chunk metadata must be an object")
    return {**(metadata or {}), GENERATION_KEY: embedding_generation()}


def validate_embeddings(vectors, expected_count: int) -> list[list[float]]:
    """Reject malformed results before writing any part of a provider batch."""
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise ValueError("Embedding count does not match input count")
    validated = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSION:
            raise ValueError("Embedding dimension is invalid")
        converted = []
        for value in vector:
            if type(value) not in (int, float):
                raise ValueError("Embedding contains a nonnumeric value")
            try:
                # pgvector stores float32; reject overflow and zero after rounding.
                value = struct.unpack("f", struct.pack("f", value))[0]
            except (OverflowError, struct.error):
                raise ValueError("Embedding value is outside float32 range") from None
            if not math.isfinite(value):
                raise ValueError("Embedding contains a nonfinite value")
            converted.append(value)
        if not any(converted):
            raise ValueError("Zero embeddings cannot support cosine retrieval")
        validated.append(converted)
    return validated


def validate_provider_response(payload, expected_count: int) -> list[list[float]]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError("Embedding response count does not match input count")
    ordered = [None] * expected_count
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Embedding response item is invalid")
        index = row.get("index")
        if type(index) is not int or not 0 <= index < expected_count or index in seen:
            raise ValueError("Embedding response indices do not match inputs")
        seen.add(index)
        ordered[index] = row.get("embedding")
    return validate_embeddings(ordered, expected_count)
