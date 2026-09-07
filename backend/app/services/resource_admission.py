"""Redis sliding windows and PostgreSQL session permits shared by all processes."""

from contextlib import contextmanager
from functools import lru_cache
from hashlib import sha256
import ipaddress
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import text

from app.database import database
from app.services.resource_limits import resource_limits


RATE_SCRIPT = """
local time = redis.call('TIME')
local now = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
local window = tonumber(ARGV[1]) * 1000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    return {0, math.max(1, math.ceil((tonumber(oldest[2]) + window - now) / 1000))}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0}
"""

# 64 reserved slots per class; distinct from document and summary context locks.
PERMIT_NAMESPACES = {
    "chat": 0x52410000, "search": 0x52410100,
    "summary": 0x52410200, "processing": 0x52410300,
    "upload_quota": 0x52410400,
}
TRY_PERMIT = text("SELECT pg_try_advisory_lock(:namespace, :user_id)")
RELEASE_PERMIT = text("SELECT pg_advisory_unlock(:namespace, :user_id)")


class ResourceRejected(HTTPException):
    def __init__(self, code, detail, retry_after=1, status_code=429):
        self.code = code
        self.retry_after = max(1, int(retry_after))
        super().__init__(status_code, detail, headers={
            "Retry-After": str(self.retry_after), "X-Resource-Error": code,
        })


class AdmissionUnavailable(ResourceRejected):
    def __init__(self):
        super().__init__("admission_unavailable", "Resource protection is temporarily unavailable. Please try again shortly.", 5, 503)


async def resource_error_response(request, error):
    return JSONResponse(status_code=error.status_code, headers=error.headers, content={
        "detail": error.detail, "code": error.code, "retry_after": error.retry_after,
    })


def stream_resource_error(error):
    return {"type": "error", "message": error.detail,
            "code": error.code, "retry_after": error.retry_after}


def checked_user_id(user_id):
    if type(user_id) is not int or not 0 < user_id < 2**31:
        raise AdmissionUnavailable()
    return user_id


@lru_cache(maxsize=1)
def rate_backend():
    url = resource_limits().redis_url
    if not url or not url.startswith(("redis://", "rediss://")):
        raise AdmissionUnavailable()
    return Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1,
                          retry=Retry(NoBackoff(), 0), max_connections=32)


def consume_rate(subject, category):
    try:
        policy = resource_limits().rates[category]
        digest = sha256(str(subject).encode("utf-8")).hexdigest()
        key = f"resource:v1:rate:{category}:{digest}"
        allowed, retry = rate_backend().eval(
            RATE_SCRIPT, 1, key, policy.seconds, policy.limit, uuid4().hex,
        )
        if int(allowed) != 1:
            raise ResourceRejected("rate_limit", "Too many requests. Please wait and try again.", retry)
    except ResourceRejected:
        raise
    except Exception:
        raise AdmissionUnavailable() from None


def consume_user_rate(user_id, category):
    user_id = checked_user_id(user_id)
    try:
        consume_rate(f"user:{user_id}", category)
    except AdmissionUnavailable:
        # Preserve the ability to cancel/delete during a limiter outage.
        # All new work and ordinary reads remain fail-closed.
        if category != "recovery":
            raise


def authentication_subject(request):
    # Use the ASGI peer only; never parse X-Forwarded-For or Forwarded here.
    peer = request.client.host if request.client else "unknown"
    try:
        peer = str(ipaddress.ip_address(peer))
    except ValueError:
        peer = "unknown"
    return f"ip:{peer}"


class Permit:
    def __init__(self, connection, user_id, category, namespace, owns_connection):
        self.connection = connection
        self.user_id = user_id
        self.category = category
        self.namespace = namespace
        self.owns_connection = owns_connection
        # Only lifetime bookkeeping is local. Admission itself is PostgreSQL.
        self._mutex = Lock()
        self._references = 1

    def check(self):
        if self._references == 0 or self.connection.closed or self.connection.invalidated:
            raise AdmissionUnavailable()

    def retain(self):
        with self._mutex:
            if self._references == 0:
                raise AdmissionUnavailable()
            self.check()
            self._references += 1
        return self

    def release(self):
        with self._mutex:
            if self._references == 0:
                return
            self._references -= 1
            if self._references:
                return
        try:
            if not self.connection.closed and not self.connection.invalidated:
                self.connection.rollback()
                released = self.connection.scalar(RELEASE_PERMIT, {
                    "namespace": self.namespace, "user_id": self.user_id,
                })
                self.connection.commit()
                if not released:
                    self.connection.invalidate()
        except Exception:
            self.connection.invalidate()
        except BaseException:
            self.connection.invalidate()
            raise
        finally:
            if self.owns_connection:
                self.connection.close()


def acquire_permit(user_id, category, connection=None):
    user_id = checked_user_id(user_id)
    owned = connection is None
    try:
        count = 1 if category == "upload_quota" else resource_limits().concurrency[category]
        if owned:
            connection = database.engine.connect()
        if connection.closed or connection.invalidated or connection.in_transaction():
            raise AdmissionUnavailable()
        for slot in range(count):
            namespace = PERMIT_NAMESPACES[category] + slot
            acquired = connection.scalar(TRY_PERMIT, {"namespace": namespace, "user_id": user_id})
            connection.commit()
            if acquired:
                return Permit(connection, user_id, category, namespace, owned)
        raise ResourceRejected("concurrency_limit", "You already have the maximum number of these operations running. Please wait and try again.", 5)
    except ResourceRejected:
        if owned and connection is not None:
            connection.close()
        raise
    except BaseException as error:
        if connection is not None:
            # Acquisition may have succeeded before its reply/commit failed.
            connection.invalidate()
            if owned:
                connection.close()
        if not isinstance(error, Exception):
            raise
        raise AdmissionUnavailable() from None


@contextmanager
def user_operation(user_id, category, *, rate=True, existing=None, connection=None):
    if existing is not None:
        if existing.user_id != user_id or existing.category != category:
            raise AdmissionUnavailable()
        existing.check()
        yield existing
        return
    if rate:
        consume_user_rate(user_id, category)
    permit = acquire_permit(user_id, category, connection)
    try:
        yield permit
    finally:
        permit.release()


class OwnedIterator:
    """A disconnect cannot release capacity while a synchronous next() runs."""
    def __init__(self, iterator, permit):
        self.iterator = iter(iterator)
        self.permit = permit
        self.mutex = Lock()
        self.closed = False
        self.running = False

    def __iter__(self):
        return self

    def __next__(self):
        with self.mutex:
            if self.closed:
                raise StopIteration
            self.permit.retain()
            self.running = True
        try:
            self.permit.check()
            return next(self.iterator)
        finally:
            with self.mutex:
                self.running = False
                close = self.closed
            try:
                if close:
                    self.iterator.close()
            finally:
                self.permit.release()

    def close(self):
        with self.mutex:
            self.closed = True
            close = not self.running
        if close:
            self.iterator.close()


class AdmittedStreamingResponse(StreamingResponse):
    def __init__(self, content, permit, **kwargs):
        self.owned_iterator = OwnedIterator(content, permit)
        super().__init__(self.owned_iterator, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.owned_iterator.close()
