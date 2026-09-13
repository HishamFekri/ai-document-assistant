"""Validate deployment settings without displaying values or contacting services."""

import os
from urllib.parse import urlsplit


def validate_runtime():
    from app.services.auth_config import auth_settings
    from app.database.pool_config import pool_options
    from app.services.resource_limits import resource_limits, upload_limits

    auth_settings()
    pool_options()
    limits = resource_limits()
    upload_limits()
    queue = os.getenv("TASK_QUEUE", "background").lower()
    if queue not in {"background", "celery"}:
        raise ValueError("Invalid TASK_QUEUE")
    if os.getenv("ENVIRONMENT", "development").lower() not in {"production", "staging"}:
        return
    if queue != "celery":
        raise ValueError("Production/staging requires TASK_QUEUE=celery")
    for name, value in {
        "RESOURCE_REDIS_URL": limits.redis_url,
        "CELERY_BROKER_URL": os.getenv("CELERY_BROKER_URL"),
        "CELERY_RESULT_BACKEND": os.getenv("CELERY_RESULT_BACKEND"),
    }.items():
        try:
            parsed = urlsplit(value or "")
            valid = parsed.scheme in {"redis", "rediss"} and bool(parsed.hostname)
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"Invalid or missing {name}") from None
    from sqlalchemy.engine import make_url
    try:
        url = make_url(os.getenv("DATABASE_URL", ""))
        valid = url.drivername in {"postgresql", "postgresql+psycopg"} and bool(url.host and url.database)
        url.port
    except Exception:
        valid = False
    if not valid:
        raise ValueError("Invalid or missing DATABASE_URL") from None
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret) < 32 or secret in {"generate-a-long-random-secret", "replace-me"}:
        raise ValueError("JWT_SECRET_KEY must be a strong deployment secret of at least 32 characters")


def server_arguments():
    try:
        port = int(os.getenv("PORT", "8000"))
    except ValueError:
        raise ValueError("Invalid PORT") from None
    if not 1 <= port <= 65535:
        raise ValueError("Invalid PORT")
    # Ignore forwarded headers by default, including ambient Uvicorn proxy env.
    return ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(port),
            "--no-proxy-headers", "--no-access-log"]
