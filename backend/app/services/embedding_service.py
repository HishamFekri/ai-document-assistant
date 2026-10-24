import logging
from app.services.observability import log_event
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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

# Total attempts per request, including the first.
VOYAGE_MAX_RETRIES = int(
    os.getenv(
        "VOYAGE_MAX_RETRIES",
        "5",
    )
)

VOYAGE_TIMEOUT_SECONDS = float(
    os.getenv(
        "VOYAGE_TIMEOUT_SECONDS",
        "60",
    )
)

VOYAGE_CONNECT_TIMEOUT_SECONDS = 10.0
VOYAGE_MAX_RETRY_DELAY_SECONDS = 30.0
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

# Document processing tolerates longer provider waits than an interactive query.
PASSAGE_DEADLINE_SECONDS = 180.0
QUERY_DEADLINE_SECONDS = 30.0
QUERY_MAX_ATTEMPTS = 3


if not VOYAGE_API_KEY:
    raise RuntimeError(
        "VOYAGE_API_KEY is not set"
    )

if VOYAGE_MAX_RETRIES < 1 or not 0 < VOYAGE_TIMEOUT_SECONDS <= 600:
    raise RuntimeError(
        "Invalid Voyage retry or timeout setting"
    )


def retry_delay_seconds(
    attempt: int,
    retry_after: str | None,
) -> float:
    delay = None

    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                delay = None

    if delay is None or delay != delay:
        delay = 2 ** (attempt + 1)

    return min(max(delay, 0.0), VOYAGE_MAX_RETRY_DELAY_SECONDS)


def _create_embeddings(
    texts: list[str],
    input_type: str,
    *,
    max_attempts: int = VOYAGE_MAX_RETRIES,
    deadline_seconds: float = PASSAGE_DEADLINE_SECONDS,
) -> list[list[float]]:
    logger = logging.getLogger(__name__)
    started = time.monotonic()

    for attempt in range(max_attempts):
        remaining = deadline_seconds - (time.monotonic() - started)
        # Never start an attempt that could run past the overall deadline.
        read_timeout = max(1.0, min(VOYAGE_TIMEOUT_SECONDS, remaining))

        try:
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
                timeout=(
                    min(VOYAGE_CONNECT_TIMEOUT_SECONDS, read_timeout),
                    read_timeout,
                ),
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
            failure, response = error, None
            retry_after = None
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                return validate_provider_response(response.json(), len(texts))
            failure = None
            retry_after = response.headers.get("Retry-After")

        delay = retry_delay_seconds(attempt, retry_after)
        elapsed = time.monotonic() - started

        if attempt == max_attempts - 1 or elapsed + delay + 1.0 >= deadline_seconds:
            # Re-raise the original type: callers classify Timeout, ConnectionError
            # and HTTPError (408/429/5xx) as retryable at the processing level.
            if response is not None:
                response.raise_for_status()
            raise failure

        fields = {"retry": attempt + 1, "delay_seconds": delay}
        if response is not None:
            fields["status_code"] = response.status_code
        else:
            fields["exception_type"] = f"{type(failure).__module__}.{type(failure).__name__}"
        log_event(logger, logging.INFO, "embedding_retry", **fields)

        time.sleep(delay)

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
        max_attempts=min(QUERY_MAX_ATTEMPTS, VOYAGE_MAX_RETRIES),
        deadline_seconds=QUERY_DEADLINE_SECONDS,
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

        log_event(logging.getLogger(__name__), logging.INFO, "embedding_batch", count=len(batch))

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
