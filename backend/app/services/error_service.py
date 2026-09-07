import logging
from collections import deque
from typing import Literal

import httpx
import requests
from openai import APITimeoutError


logger = logging.getLogger(__name__)


def log_and_get_public_error(
    error: Exception,
    message: str,
) -> str:
    logger.exception("%s: %s", message, error)
    return message


GenerationOperation = Literal["summary", "message", "document"]
GENERATION_FAILED = {
    "document": "Document processing failed. Please try again.",
    "summary": "Summary generation failed. Please try again.",
    "message": "Message generation failed. Please try again.",
}
GENERATION_TIMED_OUT = {
    "document": "Document processing timed out. Please try again.",
    "summary": "Summary generation timed out. Please try again.",
    "message": "Message generation timed out. Please try again.",
}
SAFE_GENERATION_ERRORS = {
    "document": {GENERATION_FAILED["document"], GENERATION_TIMED_OUT["document"]},
    "summary": {GENERATION_FAILED["summary"], GENERATION_TIMED_OUT["summary"]},
    "message": {
        GENERATION_FAILED["message"], GENERATION_TIMED_OUT["message"],
        "Answer generation failed",
        "One or more documents failed to process.",
    },
}
TIMEOUT_TYPES = (TimeoutError, httpx.TimeoutException, requests.exceptions.Timeout, APITimeoutError)


def public_generation_error(value: str | None, operation: GenerationOperation) -> str | None:
    """Allow only known public messages at serialization time; no historical writes."""
    if value is None or value in SAFE_GENERATION_ERRORS[operation]:
        return value
    return GENERATION_FAILED[operation]


def log_generation_failure(
    error: Exception,
    operation: GenerationOperation,
    *,
    document_id: int | None = None,
    chat_id: int | None = None,
    summary_id: int | None = None,
    message_id: int | None = None,
) -> str:
    """Log bounded diagnostic locations, never exception text, source lines or locals.

    Provider and database exceptions can embed credentials or document contents
    in their messages, causes and attributes. logger.exception/str(error) would
    copy that data into logs. Preserve types and call locations instead, using
    the existing Python logger without changing global logging configuration.
    """
    diagnostics = []
    seen = set()
    current = error
    timed_out = False
    while current is not None and id(current) not in seen and len(diagnostics) < 5:
        seen.add(id(current))
        timed_out = timed_out or isinstance(current, TIMEOUT_TYPES)
        frames = deque(maxlen=12)
        tb = current.__traceback__
        while tb is not None:
            frames.append({
                "module": tb.tb_frame.f_globals.get("__name__", "unknown"),
                "function": tb.tb_frame.f_code.co_name,
                "line": tb.tb_lineno,
            })
            tb = tb.tb_next
        diagnostics.append({
            "exception_type": f"{type(current).__module__}.{type(current).__name__}",
            "frames": list(frames),
        })
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)

    context = {
        "operation": operation,
        "document_id": document_id,
        "chat_id": chat_id,
        "summary_id": summary_id,
        "message_id": message_id,
        "failure_category": "timeout" if timed_out else "generation_failed",
        "diagnostics": diagnostics,
    }
    # Include context in the message for the existing default formatter as well
    # as record fields for any configured structured formatter. No exc_info.
    logger.error("Generation failure: %s", context, extra=context)
    return GENERATION_TIMED_OUT[operation] if timed_out else GENERATION_FAILED[operation]
