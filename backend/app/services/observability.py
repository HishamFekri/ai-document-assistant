"""Small, allowlisted logging/adapter boundary. No SDK, payload capture or I/O."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import math
import re
import sys
from time import monotonic
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from functools import wraps


_context = ContextVar("observability_context", default=None)
_error_reporter = None
_request_observer = None
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,159}\Z")
_NUMBERS = {
    "document_id", "summary_id", "chat_id", "message_id", "status_code",
    "duration_ms", "response_bytes", "retry", "delay_seconds", "chunks",
    "count", "pages", "images", "tables", "equations",
}
_NAMES = {"event", "operation", "exception_type", "failure_category", "dependency", "outcome"}
_IDS = {"request_id", "correlation_id", "job_id"}
logger = logging.getLogger(__name__)


def valid_id(value):
    if not isinstance(value, str) or len(value) not in (32, 36):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def safe_fields(fields):
    """Drop unknown fields and invalid values; never stringify arbitrary objects."""
    result = {}
    for key, value in fields.items():
        if key in _IDS:
            if identifier := valid_id(value):
                result[key] = identifier
        elif key in _NUMBERS and (
            (type(value) is int and -(2**63) <= value < 2**63)
            or (type(value) is float and math.isfinite(value))
        ):
            result[key] = value
        elif key in _NAMES and isinstance(value, str) and _NAME.fullmatch(value):
            result[key] = value
        elif key == "method" and isinstance(value, str) and value in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "OTHER"}:
            result[key] = value
    return result


def current_context():
    return dict(_context.get() or {})


def summary_job_id(summary_id):
    return str(uuid5(NAMESPACE_URL, f"ai-document-assistant:summary:{summary_id}"))


def submit_observed(executor, function, *args, **kwargs):
    # ThreadPoolExecutor does not inherit ContextVars. Capture only our safe IDs,
    # without copying ORM sessions, request objects or any other application state.
    fields = current_context()
    def run():
        with log_context(**fields):
            return function(*args, **kwargs)
    return executor.submit(run)


def document_job(function):
    @wraps(function)
    def run(document_id, *args, **kwargs):
        with log_context(operation="document_processing", document_id=document_id,
                         job_id=current_context().get("job_id") or str(uuid4())):
            log_event(logger, logging.INFO, "document_job_started")
            try:
                result = function(document_id, *args, **kwargs)
            except BaseException:
                log_event(logger, logging.INFO, "document_job_finished", outcome="interrupted")
                raise
            log_event(logger, logging.INFO, "document_job_finished", outcome=result)
            return result
    return run


@contextmanager
def log_context(**fields):
    token = _context.set({**current_context(), **safe_fields(fields)})
    try:
        yield
    finally:
        _context.reset(token)


def install_observers(*, error_reporter=None, request_observer=None):
    """Trusted boot-time adapters only; receive safe dicts, never exceptions/requests.

    Adapters must be fast/nonblocking (enqueue into their own bounded queue).
    No SDK auto-instrumentation, breadcrumbs, locals, or request capture here.
    """
    global _error_reporter, _request_observer
    _error_reporter, _request_observer = error_reporter, request_observer


def _notify(callback, fields):
    if callback is not None:
        try:
            callback(dict(fields))
        except Exception:
            # Adapter failures cannot affect requests or recursively notify.
            logger.warning("observer_failed", extra={"safe_event": {"event": "observer_failed"}})


def notify_error(fields):
    _notify(_error_reporter, safe_fields(fields))


def log_event(target, level, event, *, _stacklevel=2, **fields):
    payload = safe_fields({**current_context(), **fields, "event": event})
    target.log(level, event, extra={"safe_event": payload, **payload}, stacklevel=_stacklevel)
    if level >= logging.ERROR:
        _notify(_error_reporter, payload)


def log_exception(target, operation, error=None, **fields):
    error = error if error is not None else sys.exception()
    log_event(target, logging.ERROR, "operation_failed", _stacklevel=3, operation=operation,
              exception_type=f"{type(error).__module__}.{type(error).__name__}", **fields)


class SafeJSONFormatter(logging.Formatter):
    def format(self, record):
        try:
            return self._format_safe(record)
        except Exception:
            # logging.handleError can otherwise dump the raw record to stderr.
            return '{"event":"logging_record_rejected","level":"ERROR"}'

    def _format_safe(self, record):
        # Do not call getMessage()/formatException(): third-party records can
        # contain full HTTP URLs, SQL parameters, task args, secrets and bodies.
        fields = safe_fields({**current_context(), **getattr(record, "safe_event", {})})
        fields.setdefault("event", "library_log")
        fields.update(timestamp=datetime.now(timezone.utc).isoformat(), level=record.levelname)
        fields["logger"] = record.name if _NAME.fullmatch(record.name) else "unknown"
        fields["source"] = record.funcName if _NAME.fullmatch(record.funcName) else "unknown"
        fields["line"] = record.lineno
        if record.exc_info and record.exc_info[0]:
            kind = record.exc_info[0]
            fields.update(safe_fields({"exception_type": f"{kind.__module__}.{kind.__name__}"}))
        # Batch 4 diagnostics already exclude exception messages/source/locals.
        # Re-allowlist each item rather than copying arbitrary extras.
        if record.name == "app.services.error_service":
            fields.update(safe_fields(record.__dict__))
            diagnostics = []
            for item in getattr(record, "diagnostics", [])[:5]:
                diagnostic = safe_fields({"exception_type": item.get("exception_type")})
                diagnostic["frames"] = [{
                    k: v for k, v in frame.items()
                    if (k in {"module", "function"} and isinstance(v, str) and _NAME.fullmatch(v))
                    or (k == "line" and type(v) is int)
                } for frame in item.get("frames", [])[-12:]]
                diagnostics.append(diagnostic)
            if diagnostics:
                fields["diagnostics"] = diagnostics
        return json.dumps(fields, ensure_ascii=True, allow_nan=False)


def configure_logging():
    """Used by ASGI startup and Celery's setup_logging signal (including children)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeJSONFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # A broken output stream must never trigger stdlib's raw msg/args dump.
    logging.raiseExceptions = False
    # Libraries must propagate through this boundary, not their own raw formatters.
    for item in list(logging.root.manager.loggerDict.values()):
        if isinstance(item, logging.Logger):
            item.handlers.clear()
            item.propagate = True
    logging.captureWarnings(True)


class RequestObservabilityMiddleware:
    """Pure ASGI: does not read/buffer bodies or change stream cancellation."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        # Always own request IDs. Only canonical UUID correlation values survive.
        correlation_id = next((valid_id(v.decode("ascii", errors="ignore"))
                               for k, v in scope.get("headers", []) if k.lower() == b"x-correlation-id"), None)
        start, status, size, complete, disconnected = monotonic(), 500, 0, False, False
        method = scope.get("method", "OTHER")
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            method = "OTHER"

        async def tracked_receive():
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def tracked_send(message):
            nonlocal status, size, complete
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                message = {**message, "headers": [*headers, (b"x-request-id", request_id.encode("ascii"))]}
            await send(message)
            if message["type"] == "http.response.body":
                size += len(message.get("body", b""))
                complete = not message.get("more_body", False)

        with log_context(request_id=request_id, correlation_id=correlation_id or request_id):
            try:
                await self.app(scope, tracked_receive, tracked_send)
            except Exception as error:
                log_exception(logger, "http_request", error)
                raise
            finally:
                # Route NAME is code-owned, low cardinality; never raw path/query.
                operation = getattr(scope.get("route"), "name", "unmatched")
                fields = safe_fields(dict(
                    operation=operation, method=method, status_code=status,
                    duration_ms=round((monotonic() - start) * 1000, 3), response_bytes=size,
                    outcome="disconnected" if disconnected else ("complete" if complete else "incomplete"),
                ))
                log_event(logger, logging.INFO, "http_request_completed", **fields)
                # IDs intentionally excluded from metric labels.
                _notify(_request_observer, fields)
