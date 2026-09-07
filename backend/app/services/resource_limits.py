"""Central resource policy. No network access or secrets printed at import."""

from dataclasses import dataclass, field
from functools import lru_cache
import os


@dataclass(frozen=True)
class RatePolicy:
    limit: int
    seconds: int


RATE_DEFAULTS = {
    "api": (120, 60), "auth": (30, 60), "recovery": (30, 60),
    "search": (20, 60), "chat": (10, 60),
    "upload": (5, 3600), "summary": (3, 3600),
}
CONCURRENCY_DEFAULTS = {"chat": 2, "search": 2, "summary": 1, "processing": 1}
ROUTE_POLICIES = {
    "search_chat_documents": "search",
    "ask_chat": "chat", "ask_chat_stream": "chat",
    "create_summary_assistant_message": "chat",
    "upload_document": "upload", "retry_document_processing": "upload",
    "create_document_summary": "summary", "stream_document_summary": "summary",
    "cancel_document_summary": "recovery",
}


def positive_setting(name, default, maximum=1_000_000):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"Invalid resource setting: {name}") from None
    if not 0 < value <= maximum:
        raise ValueError(f"Invalid resource setting: {name}")
    return value


@dataclass(frozen=True)
class ResourceLimits:
    rates: dict
    concurrency: dict
    max_documents: int
    max_original_bytes: int
    redis_url: str | None = field(repr=False)


@lru_cache(maxsize=1)
def resource_limits():
    production = os.getenv("ENVIRONMENT", "development").lower() == "production"
    return ResourceLimits(
        rates={name: RatePolicy(
            positive_setting(f"RESOURCE_{name.upper()}_LIMIT", limit),
            positive_setting(f"RESOURCE_{name.upper()}_WINDOW_SECONDS", seconds, 86400),
        ) for name, (limit, seconds) in RATE_DEFAULTS.items()},
        concurrency={name: positive_setting(
            f"RESOURCE_{name.upper()}_CONCURRENCY", count, 64,
        ) for name, count in CONCURRENCY_DEFAULTS.items()},
        max_documents=positive_setting("RESOURCE_MAX_DOCUMENTS", 100),
        max_original_bytes=positive_setting("RESOURCE_MAX_ORIGINAL_BYTES", 1024**3, 1024**5),
        redis_url=(os.getenv("RESOURCE_REDIS_URL") or os.getenv("CELERY_BROKER_URL")
                   or (None if production else "redis://127.0.0.1:6379/2")),
    )


def request_policy(request):
    if request.method == "DELETE":
        return "recovery"
    route = request.scope.get("route")
    return ROUTE_POLICIES.get(getattr(route, "name", ""), "api")
