"""Shared environment interpretation; no dotenv loading, caching or I/O."""

import os


def environment_name():
    value = os.getenv("ENVIRONMENT", "development").strip().lower()
    if value not in {"development", "test", "staging", "production"}:
        raise RuntimeError("ENVIRONMENT must be development, test, staging or production")
    return value


def is_production_environment():
    return environment_name() in {"production", "staging"}
