import os
import time

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

        return [
            item["embedding"]
            for item in data["data"]
        ]

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

    all_embeddings = []

    for start in range(
        0,
        len(cleaned_texts),
        VOYAGE_BATCH_SIZE,
    ):
        batch = cleaned_texts[
            start:
            start + VOYAGE_BATCH_SIZE
        ]

        batch_number = (
            start // VOYAGE_BATCH_SIZE
        ) + 1

        total_batches = (
            len(cleaned_texts)
            + VOYAGE_BATCH_SIZE
            - 1
        ) // VOYAGE_BATCH_SIZE

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