import os

import requests


VOYAGE_API_KEY = os.getenv(
    "VOYAGE_API_KEY"
)

VOYAGE_MODEL = os.getenv(
    "VOYAGE_MODEL",
    "voyage-4-lite",
)

VOYAGE_API_URL = (
    "https://api.voyageai.com/v1/embeddings"
)

EMBEDDING_DIMENSION = 512


if not VOYAGE_API_KEY:
    raise RuntimeError(
        "VOYAGE_API_KEY is not set"
    )


def _create_embeddings(
    texts: list[str],
    input_type: str,
) -> list[list[float]]:
    response = requests.post(
        VOYAGE_API_URL,
        headers={
            "Authorization": (
                f"Bearer {VOYAGE_API_KEY}"
            ),
            "Content-Type": "application/json",
        },
        json={
            "input": texts,
            "model": VOYAGE_MODEL,
            "input_type": input_type,
            "output_dimension": (
                EMBEDDING_DIMENSION
            ),
            "output_dtype": "float",
        },
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    return [
        item["embedding"]
        for item in data["data"]
    ]


def create_passage_embedding(
    text: str,
) -> list[float]:
    if not text or not text.strip():
        raise ValueError(
            "Text cannot be empty"
        )

    embeddings = _create_embeddings(
        [text.strip()],
        input_type="document",
    )

    return embeddings[0]


def create_query_embedding(
    text: str,
) -> list[float]:
    if not text or not text.strip():
        raise ValueError(
            "Query cannot be empty"
        )

    embeddings = _create_embeddings(
        [text.strip()],
        input_type="query",
    )

    return embeddings[0]


def create_passage_embeddings(
    texts: list[str],
) -> list[list[float]]:
    if not texts:
        return []

    cleaned_texts = []

    for text in texts:
        if not text or not text.strip():
            raise ValueError(
                "Passage text cannot be empty"
            )

        cleaned_texts.append(
            text.strip()
        )

    return _create_embeddings(
        cleaned_texts,
        input_type="document",
    )