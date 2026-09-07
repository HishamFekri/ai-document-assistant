"""Typed processing failures; only known transient causes qualify for retry."""

import httpx
import requests
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import DBAPIError


class RetryableDocumentProcessingError(RuntimeError):
    pass


class ProcessingClaimLost(RetryableDocumentProcessingError):
    pass


class DocumentDeletedDuringProcessing(Exception):
    pass


def is_retryable_processing_error(error: Exception) -> bool:
    seen = set()
    current = error
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        if isinstance(current, (
            RetryableDocumentProcessingError, TimeoutError, SoftTimeLimitExceeded,
            requests.exceptions.Timeout, requests.exceptions.ConnectionError,
            httpx.TimeoutException, httpx.NetworkError,
        )):
            return True
        if isinstance(current, requests.exceptions.HTTPError):
            response = current.response
            if response is not None and response.status_code in (408, 429, 500, 502, 503, 504):
                return True
        if isinstance(current, DBAPIError):
            if current.connection_invalidated or getattr(current.orig, "sqlstate", None) in {
                "40001", "40P01", "53300", "57P01", "57P02", "57P03",
            }:
                return True
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return False
