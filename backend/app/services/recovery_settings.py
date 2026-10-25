"""Settings for automatic processing recovery. No database imports (validated at startup)."""

import os
from dataclasses import dataclass

from app.services.environment import environment_name


@dataclass(frozen=True)
class RecoverySettings:
    enabled: bool
    stale_seconds: int
    interval_seconds: int
    max_age_seconds: int


def _setting(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"Invalid {name}") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def recovery_settings():
    queue = os.getenv("TASK_QUEUE", "background").lower()
    configured = os.getenv("DOCUMENT_RECOVERY_ENABLED")
    if configured is not None and configured.lower() not in {"true", "false"}:
        raise ValueError("DOCUMENT_RECOVERY_ENABLED must be true or false")
    # On by default except in tests; never for Celery, whose broker-queued tasks do
    # not refresh the row while they wait.
    enabled = (configured.lower() == "true") if configured is not None else environment_name() != "test"
    # Must stay well above the 60s queued-refresh and heartbeat intervals.
    stale = _setting("DOCUMENT_RECOVERY_STALE_SECONDS", 300, 180, 3600)
    return RecoverySettings(
        enabled=enabled and queue == "background",
        stale_seconds=stale,
        interval_seconds=_setting("DOCUMENT_RECOVERY_INTERVAL_SECONDS", 60, 10, 3600),
        max_age_seconds=_setting("DOCUMENT_RECOVERY_MAX_AGE_SECONDS", 86400, stale, 7 * 86400),
    )
