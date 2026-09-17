"""Per-process application pool limits; Alembic uses its separate NullPool."""

import os


def pool_options():
    def integer(name, default, minimum, maximum):
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError:
            raise ValueError(f"Invalid pool setting: {name}") from None
        if not minimum <= value <= maximum:
            raise ValueError(f"Invalid pool setting: {name}")
        return value

    ping = os.getenv("DB_POOL_PRE_PING", "true").lower()
    if ping not in {"true", "false"}:
        raise ValueError("Invalid pool setting: DB_POOL_PRE_PING")
    recycle = integer("DB_POOL_RECYCLE", -1, -1, 86400)
    if recycle == 0:
        raise ValueError("Invalid pool setting: DB_POOL_RECYCLE")
    return dict(pool_size=integer("DB_POOL_SIZE", 5, 1, 5),
                max_overflow=integer("DB_MAX_OVERFLOW", 10, 0, 10),
                pool_timeout=integer("DB_POOL_TIMEOUT", 30, 1, 300),
                pool_recycle=recycle, pool_pre_ping=ping == "true")
