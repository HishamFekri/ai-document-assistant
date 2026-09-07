import logging
import os

from celery import Celery
from celery.exceptions import Retry
from dotenv import load_dotenv

from app.services.document_processing_errors import RetryableDocumentProcessingError
from app.services.error_service import log_generation_failure


load_dotenv()
logger = logging.getLogger(__name__)
MAX_PROCESSING_RETRIES = 3
MAX_RETRY_DELAY_SECONDS = 60

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


def retry_delay(retries):
    return min(MAX_RETRY_DELAY_SECONDS, 2 ** min(max(retries, 0) + 1, 6))


@celery_app.task(
    bind=True, max_retries=MAX_PROCESSING_RETRIES,
    soft_time_limit=480, time_limit=600,
    reject_on_worker_lost=False,
)
def process_document_task(self, document_id: int, file_path: str | None = None):
    from app.services.document_processing_service import (
        mark_processing_retries_exhausted, process_document,
    )

    try:
        return process_document(document_id, file_path)
    except RetryableDocumentProcessingError:
        if self.request.retries >= MAX_PROCESSING_RETRIES:
            try:
                mark_processing_retries_exhausted(document_id)
            except Exception as error:
                log_generation_failure(error, "document", document_id=document_id)
            logger.warning("Processing retries exhausted document=%s", document_id)
            return "retry_exhausted"
        delay = retry_delay(self.request.retries)
        logger.info(
            "Processing retry scheduled document=%s retry=%s delay_seconds=%s",
            document_id, self.request.retries + 1, delay,
        )
        try:
            raise self.retry(
                exc=RetryableDocumentProcessingError("Document processing temporarily unavailable"),
                countdown=delay, max_retries=MAX_PROCESSING_RETRIES,
            )
        except Retry:
            raise
        except Exception as error:
            log_generation_failure(error, "document", document_id=document_id)
            raise RuntimeError("Could not schedule document processing retry") from None
