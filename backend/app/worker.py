import logging
import os

from celery import Celery
from celery.exceptions import Retry
from celery import signals
from dotenv import load_dotenv

from app.services.document_processing_errors import RetryableDocumentProcessingError
from app.services.document_processing_retry import (
    MAX_PROCESSING_RETRIES,
    retry_delay,
)
from app.services.error_service import log_generation_failure
from app.services.observability import configure_logging, current_context, log_context, valid_id, log_event, log_exception
from app.services.runtime_config import validate_runtime


load_dotenv()
logger = logging.getLogger(__name__)


@signals.setup_logging.connect
def safe_worker_logging(**kwargs):
    configure_logging()


@signals.worker_init.connect
def validate_worker_runtime(**kwargs):
    try:
        validate_runtime()
    except Exception as error:
        log_exception(logger, "runtime_configuration", error)
        # Celery's Signal.send catches Exception. SystemExit must escape so an
        # invalid deployment cannot continue starting the worker.
        raise SystemExit(1) from None


@signals.before_task_publish.connect
def correlate_document_task(sender=None, headers=None, **kwargs):
    if sender != "app.worker.process_document_task" or headers is None:
        return
    for name in ("request_id", "correlation_id"):
        if value := valid_id(current_context().get(name)):
            headers[name] = value

celery_app = Celery(
    "ai_document_assistant",
    broker=os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1"),
)
celery_app.conf.update(
    task_acks_late=True,
    # Killed workers must not cause unbounded poison-message redelivery.
    # The persisted document/checkpoint is recovered through explicit retry.
    task_reject_on_worker_lost=False,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=5,
    result_expires=3600,
    broker_transport_options={
        "visibility_timeout": 900,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    },
)


@celery_app.task(
    # Capacity deferrals are queueing, not processing failures, and may outlive
    # the bounded retry budget used for actual transient processing errors.
    bind=True, max_retries=None,
    soft_time_limit=480, time_limit=600,
    reject_on_worker_lost=False,
)
def process_document_task(
    self,
    document_id: int,
    file_path: str | None = None,
    processing_failures: int = 0,
):
    headers = self.request.headers or {}
    with log_context(operation="document_processing", job_id=self.request.id, document_id=document_id,
                     request_id=headers.get("request_id"), correlation_id=headers.get("correlation_id")):
        return _process_document_task(self, document_id, file_path, processing_failures)


def schedule_document_retry(self, document_id, processing_failures, event):
    delay = retry_delay(self.request.retries)
    log_event(logger, logging.INFO, event, document_id=document_id,
              retry=self.request.retries + 1, delay_seconds=delay)
    try:
        raise self.retry(
            exc=RetryableDocumentProcessingError("Document processing temporarily unavailable"),
            countdown=delay,
            kwargs={"processing_failures": processing_failures},
        )
    except Retry:
        raise
    except Exception as error:
        log_generation_failure(error, "document", document_id=document_id)
        raise RuntimeError("Could not schedule document processing retry") from None


def _process_document_task(self, document_id, file_path, processing_failures):
    from app.services.document_processing_service import (
        mark_processing_retries_exhausted, process_document,
    )

    try:
        outcome = process_document(document_id, file_path)
        if outcome == "deferred":
            return schedule_document_retry(
                self, document_id, processing_failures, "document_processing_deferred",
            )
        return outcome
    except RetryableDocumentProcessingError:
        if processing_failures >= MAX_PROCESSING_RETRIES:
            try:
                mark_processing_retries_exhausted(document_id)
            except Exception as error:
                log_generation_failure(error, "document", document_id=document_id)
            log_event(logger, logging.WARNING, "document_retries_exhausted", document_id=document_id)
            return "retry_exhausted"
        return schedule_document_retry(
            self, document_id, processing_failures + 1, "document_retry_scheduled",
        )
