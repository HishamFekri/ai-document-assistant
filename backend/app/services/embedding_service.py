import os
import time

import requests

from app.services.embedding_contract import (
    EMBEDDING_DIMENSION,
    VOYAGE_MODEL,
    validate_provider_response,
)

VOYAGE_API_KEY = os.getenv(
    "VOYAGE_API_KEY"
)

VOYAGE_API_URL = (
    "https://api.voyageai.com/v1/embeddings"
)

VOYAGE_BATCH_SIZE = int(
    os.getenv(
        "VOYAGE_BATCH_SIZE",
        "128",
    )
)

VOYAGE_MAX_RETRIES = int(
    os.getenv(
        "VOYAGE_MAX_RETRIES",
        "5",
    )
)


if not VOYAGE_API_KEY:
    raise RuntimeError(
        "VOYAGE_API_KEY is not set"
    )


def _create_embeddings(
    texts: list[str],
    input_type: str,
) -> list[list[float]]:
    for attempt in range(
        VOYAGE_MAX_RETRIES
    ):
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
            timeout=120,
        )

        if response.status_code == 429:
            if attempt == VOYAGE_MAX_RETRIES - 1:
                response.raise_for_status()

            retry_after = response.headers.get(
                "Retry-After"
            )

            if retry_after:
                wait_seconds = float(
                    retry_after
                )
            else:
                wait_seconds = 2 ** (
                    attempt + 1
                )

            print(
                f"[VOYAGE] Rate limited. "
                f"Retrying in {wait_seconds}s..."
            )

            time.sleep(
                wait_seconds
            )

            continue

        response.raise_for_status()

        data = response.json()

        return validate_provider_response(data, len(texts))

    raise RuntimeError(
        "Voyage embedding request failed"
    )


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
    *,
    batch_size: int | None = None,
) -> list[list[float]]:
    if not texts:
        return []

    batch_size = VOYAGE_BATCH_SIZE if batch_size is None else batch_size
    if not 1 <= batch_size <= 1000:
        raise ValueError("Embedding batch size must be between 1 and 1000")

    cleaned_texts = []

    for text in texts:
        if not text or not text.strip():
            raise ValueError(
                "Passage text cannot be empty"
            )

        cleaned_texts.append(
            text.strip()
        )

    all_embeddings = []

    for start in range(
        0,
        len(cleaned_texts),
        batch_size,
    ):
        batch = cleaned_texts[
            start:
            start + batch_size
        ]

        batch_number = (
            start // batch_size
        ) + 1

        total_batches = (
            len(cleaned_texts)
            + batch_size
            - 1
        ) // batch_size

        print(
            f"[VOYAGE] Embedding batch "
            f"{batch_number}/{total_batches} "
            f"({len(batch)} chunks)"
        )

        batch_embeddings = (
            _create_embeddings(
                batch,
                input_type="document",
            )
        )

        all_embeddings.extend(
            batch_embeddings
        )

    return all_embeddings
