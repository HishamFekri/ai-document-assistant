"""Cached, bounded dependency probes. Never import application engines/providers."""

import logging
import os
from threading import Lock
from time import monotonic

from app.services.observability import log_exception


logger = logging.getLogger(__name__)
_lock = Lock()
_cached = None
_expires = 0.0
PROBE_TIMEOUT_SECONDS = 2
CACHE_SECONDS = 5


def check_database():
    import psycopg
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Unsupported readiness database")
    # One transient connection at a time per process, closed on every path.
    # Independent of the application pool's possibly long checkout queue.
    kwargs = {**url.translate_connect_args(username="user", database="dbname"), **url.query}
    kwargs.update(connect_timeout=PROBE_TIMEOUT_SECONDS,
                  options="-c statement_timeout=2000 -c default_transaction_read_only=on")
    with psycopg.connect(**kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise RuntimeError("Database readiness failed")


def check_redis(url):
    import redis

    if not url:
        raise ValueError("Redis configuration missing")
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    client = redis.Redis.from_url(
        url, socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
        socket_timeout=PROBE_TIMEOUT_SECONDS, retry=Retry(NoBackoff(), 0),
    )
    # redis-py's URL query arguments override keyword arguments. Reassert the
    # probe budget before acquiring a connection, including any retry overrides.
    client.connection_pool.connection_kwargs.update(
        socket_connect_timeout=PROBE_TIMEOUT_SECONDS, socket_timeout=PROBE_TIMEOUT_SECONDS,
        retry=Retry(NoBackoff(), 0), retry_on_timeout=False, retry_on_error=[],
    )
    try:
        if client.ping() is not True:
            raise RuntimeError("Redis readiness failed")
    finally:
        client.close()
        client.connection_pool.disconnect()


def readiness_status():
    global _cached, _expires
    if _cached is not None and monotonic() < _expires:
        return _cached
    if not _lock.acquire(blocking=False):
        # Do not claim readiness based on an expired success while another
        # caller is probing. Avoid unbounded queues of dependency connections.
        return {"status": "not_ready", "checks": {"probe": "in_progress"}}, 503
    try:
        if _cached is not None and monotonic() < _expires:
            return _cached
        from app.services.resource_limits import resource_limits

        checks = {}
        def probe(name, callback):
            try:
                callback()
                checks[name] = "ok"
            except Exception as error:
                checks[name] = "unavailable"
                log_exception(logger, "readiness", error, dependency=name)

        probe("database", check_database)
        redis_targets = {"resource_redis": resource_limits().redis_url}
        if os.getenv("TASK_QUEUE", "background").lower() == "celery":
            redis_targets.update(
                celery_broker=os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"),
                celery_backend=os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1"),
            )
        seen = {}
        for name, url in redis_targets.items():
            if url in seen:
                checks[name] = seen[url]
            else:
                probe(name, lambda: check_redis(url))
                seen[url] = checks[name]
        ready = all(value == "ok" for value in checks.values())
        _cached = ({"status": "ready" if ready else "not_ready", "checks": checks}, 200 if ready else 503)
        _expires = monotonic() + CACHE_SECONDS
        return _cached
    finally:
        _lock.release()
