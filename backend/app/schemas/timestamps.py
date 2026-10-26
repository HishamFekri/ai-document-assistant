"""API timestamps.

Stored values are UTC without a time zone (every database session runs in UTC,
see app.database.database.enforce_utc_session). Responses mark them as UTC, so
clients never have to guess: 2026-10-26T08:59:49.123456Z. Stored values are
never changed; pagination cursors keep using the stored values.
"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, TypeAdapter


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UtcDateTime = Annotated[datetime, AfterValidator(as_utc)]

_UTC_JSON = TypeAdapter(UtcDateTime)


def utc_isoformat(value: datetime) -> str:
    """The exact string the API uses for timestamps, for hand-built payloads."""
    # Serialization alone skips validators; validate first so as_utc applies.
    return _UTC_JSON.dump_python(_UTC_JSON.validate_python(value), mode="json")
