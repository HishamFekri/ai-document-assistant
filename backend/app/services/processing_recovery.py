"""Automatic recovery of background processing lost with its web process.

TASK_QUEUE=background keeps jobs in memory, so a restart, crash or redeploy loses
them. A document is recoverable only when nothing can still be working on it:

* no process holds its advisory lock (claim_document_processing holds it for the
  whole job, Datalab and image uploads included, and PostgreSQL releases it when
  the owning connection or process dies), and
* processing_updated_at is older than stale_seconds. Running jobs refresh it with
  processing_heartbeat; jobs waiting for a slot refresh it on every attempt (at
  most 60s apart), so a stale, unlocked row has no live job.

Recovery re-dispatches through the normal background path, so the global slot,
per-user permits, ownership and saved checkpoints (no repeated extraction once
chunks exist) all apply. Each recovery takes the document's advisory lock in a
short transaction, re-checks the row and bumps processing_updated_at before
dispatching, so overlapping instances (e.g. during a deploy) cannot both dispatch
it. After MAX_AUTOMATIC_RECOVERIES the document fails with a retryable message.
"""

import logging
import signal
import threading
from contextlib import contextmanager
from datetime import timedelta
from time import perf_counter
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text, update

from app.database.models import Document
from app.services.document_processing_claim import LOCK_NAMESPACE
from app.services.observability import log_context, log_event, log_exception
from app.services.recovery_settings import recovery_settings


logger = logging.getLogger(__name__)

MAX_AUTOMATIC_RECOVERIES = 2
RECOVERY_BATCH_SIZE = 20
HEARTBEAT_SECONDS = 60
# Render's maximum shutdown delay; the platform kills the process earlier anyway.
RECOVERED_JOBS_SHUTDOWN_WAIT_SECONDS = 300
RECOVERY_EXHAUSTED_ERROR = (
    "Document processing was interrupted and could not be resumed automatically. Please retry."
)
TRY_RECOVERY_LOCK = text("SELECT pg_try_advisory_xact_lock(:namespace, :document_id)")


def _recoverable(settings):
    now = func.now()
    return and_(
        Document.processing_updated_at <= now - timedelta(seconds=settings.stale_seconds),
        # Older rows stay with the existing manual flow instead of being reprocessed
        # (and billed) in bulk the first time this runs.
        Document.processing_updated_at >= now - timedelta(seconds=settings.max_age_seconds),
        or_(
            Document.processing_status == "processing",
            # A failed attempt whose in-memory retry loop died before retrying.
            and_(Document.processing_status == "failed", Document.processing_stage == "retryable_failure"),
        ),
    )


def recovery_candidates(db, settings):
    return db.scalars(
        select(Document.id).where(_recoverable(settings))
        .order_by(Document.processing_updated_at, Document.id)
        .limit(RECOVERY_BATCH_SIZE)
    ).all()


def recover_document(session_factory, document_id, settings):
    """Return "dispatch", "exhausted", "active" (locked) or "skipped" (no longer eligible)."""
    with session_factory() as db, db.begin():
        locked = db.scalar(TRY_RECOVERY_LOCK, {"namespace": LOCK_NAMESPACE, "document_id": document_id})
        if not locked:
            return "active"
        document = db.scalars(
            select(Document).where(Document.id == document_id, _recoverable(settings)).with_for_update()
        ).one_or_none()
        if document is None:
            return "skipped"
        if document.processing_recovery_attempts >= MAX_AUTOMATIC_RECOVERIES:
            document.processing_status = "failed"
            # retry_exhausted: in-memory or redelivered tasks skip it; an explicit
            # retry resets it.
            document.processing_stage = "retry_exhausted"
            document.processing_error = RECOVERY_EXHAUSTED_ERROR
            document.processing_updated_at = func.now()
            return "exhausted"
        document.processing_recovery_attempts += 1
        document.processing_status = "processing"
        document.processing_stage = "queued"
        document.processing_progress = 5
        document.processing_error = None
        # Fresh again: concurrent scanners re-check this after the lock and skip.
        document.processing_updated_at = func.now()
        return "dispatch"
    # The transaction-scoped lock is released by this commit, before dispatch.


_recovered_jobs = set()
_recovered_jobs_lock = threading.Lock()
# Set by a second shutdown signal: stop waiting for recovered jobs (forced exit).
_abandon_shutdown_wait = threading.Event()


def _run_recovered(document_id):
    from app.services.task_queue import _run_document_processing
    try:
        with log_context(operation="document_recovery", job_id=str(uuid4())):
            try:
                _run_document_processing(document_id, queued_at=perf_counter())
            except Exception as error:
                log_exception(logger, "document_recovery_job", error, document_id=document_id)
    finally:
        with _recovered_jobs_lock:
            _recovered_jobs.discard(threading.current_thread())


def dispatch_recovered(document_id):
    # One thread per job (like a BackgroundTask), not a bounded pool: a job queued
    # behind a pool would stop refreshing its row and look abandoned. The
    # processing semaphore and per-user permits bound real work. Tracked so that
    # shutdown waits for them as Uvicorn waits for BackgroundTasks.
    thread = threading.Thread(target=_run_recovered, args=(document_id,), daemon=True,
                              name=f"document-recovery-{document_id}")
    with _recovered_jobs_lock:
        _recovered_jobs.add(thread)
    thread.start()


def wait_for_recovered_jobs(timeout):
    """Wait (bounded) for recovered jobs at shutdown; return how many are still running.

    Runs after Uvicorn has drained requests and their BackgroundTasks, so recovered
    jobs get the same remaining grace before the platform kills the process.
    """
    deadline = perf_counter() + timeout
    logged = False
    while not _abandon_shutdown_wait.is_set():
        with _recovered_jobs_lock:
            running = [thread for thread in _recovered_jobs if thread.is_alive()]
        remaining = deadline - perf_counter()
        if not running or remaining <= 0:
            break
        if not logged:
            log_event(logger, logging.INFO, "document_recovery_shutdown_wait", count=len(running))
            logged = True
        running[0].join(min(0.5, remaining))           # short slices: a forced exit is noticed
    with _recovered_jobs_lock:
        return sum(thread.is_alive() for thread in _recovered_jobs)


def scan_once(session_factory=None, settings=None, dispatch=dispatch_recovered, stopping=None):
    settings = settings or recovery_settings()
    if session_factory is None:
        from app.database.database import SessionLocal
        session_factory = SessionLocal
    outcomes = {"dispatch": 0, "exhausted": 0, "active": 0, "skipped": 0}
    with session_factory() as db:
        candidates = recovery_candidates(db, settings)
    for document_id in candidates:
        # Shutdown started: claim nothing more, so no attempt is counted for a job
        # this dying process could not finish. (A claimed one is still dispatched.)
        if stopping is not None and stopping():
            break
        try:
            outcome = recover_document(session_factory, document_id, settings)
        except Exception as error:
            log_exception(logger, "document_recovery", error, document_id=document_id)
            continue
        outcomes[outcome] += 1
        if outcome == "dispatch":
            log_event(logger, logging.WARNING, "document_processing_recovered", document_id=document_id)
            dispatch(document_id)
        elif outcome == "exhausted":
            log_event(logger, logging.WARNING, "document_recovery_exhausted", document_id=document_id)
    if candidates:
        log_event(logger, logging.INFO, "document_recovery_scan", count=len(candidates),
                  recovered=outcomes["dispatch"], exhausted=outcomes["exhausted"])
    return outcomes


class RecoveryScanner:
    """Daemon thread: one scan shortly after startup, then every interval.

    Never blocks readiness; failures (e.g. the database is briefly unavailable)
    are logged and retried on the next interval.
    """

    def __init__(self, settings, initial_delay=5.0, scan=scan_once):
        self.settings, self.initial_delay, self._scan = settings, initial_delay, scan
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="document-recovery-scanner")

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        delay = self.initial_delay
        while not self._stop.wait(delay):
            try:
                self._scan(settings=self.settings, stopping=self._stop.is_set)
            except Exception as error:
                log_exception(logger, "document_recovery_scan", error)
            delay = self.settings.interval_seconds

    def request_stop(self):
        """Non-blocking (safe in a signal handler): no further recoveries are claimed."""
        self._stop.set()

    @property
    def stopping(self):
        return self._stop.is_set()

    def stop(self, timeout=5.0):
        self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()


def start_recovery_scanner():
    settings = recovery_settings()
    if not settings.enabled:
        return None
    return RecoveryScanner(settings).start()


SHUTDOWN_SIGNALS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGINT") if hasattr(signal, name))


@contextmanager
def stop_recovery_on_shutdown_signal(scanner):
    """Stop claiming recoveries as soon as shutdown begins.

    Uvicorn runs the lifespan shutdown only after draining requests, which can
    take until the platform's SIGKILL; without this hook the scanner would keep
    starting jobs on the dying process. Chains to the existing handler (Uvicorn
    installs its own before the lifespan starts). A second signal also abandons
    the wait for recovered jobs, matching Uvicorn's forced exit.
    """
    if threading.current_thread() is not threading.main_thread():
        yield                                   # signals are only settable here
        return
    previous = {}

    def handler(signum, frame):
        if scanner.stopping:
            _abandon_shutdown_wait.set()
        scanner.request_stop()
        prior = previous.get(signum)
        if callable(prior):
            prior(signum, frame)
        elif prior == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)   # keep default termination
            signal.raise_signal(signum)

    for sig in SHUTDOWN_SIGNALS:
        previous[sig] = signal.signal(sig, handler)
    try:
        yield
    finally:
        for sig, prior in previous.items():
            if signal.getsignal(sig) is handler:
                signal.signal(sig, prior)


@contextmanager
def processing_heartbeat(document_id, interval=HEARTBEAT_SECONDS):
    """Refresh processing_updated_at while a job runs (e.g. long Datalab work).

    Uses its own short transactions, so a live job still looks alive even if its
    lock connection was dropped. Failures are logged; the job is unaffected.
    """
    stop = threading.Event()

    def beat():
        from app.database.database import SessionLocal
        while not stop.wait(interval):
            try:
                with SessionLocal() as db, db.begin():
                    db.execute(update(Document).where(
                        Document.id == document_id, Document.processing_status == "processing",
                    ).values(processing_updated_at=func.now()))
            except Exception as error:
                log_exception(logger, "document_processing_heartbeat", error)

    thread = threading.Thread(target=beat, daemon=True, name=f"document-heartbeat-{document_id}")
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(5)
