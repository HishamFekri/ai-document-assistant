import os
import logging
from threading import BoundedSemaphore
from time import sleep

from fastapi import BackgroundTasks
from app.services.document_processing_errors import RetryableDocumentProcessingError
from app.services.document_processing_retry import (
    MAX_PROCESSING_RETRIES,
    retry_delay,
)
from app.services.error_service import log_generation_failure
from app.services.observability import log_event
from app.services.resource_limits import resource_limits


TASK_QUEUE = os.getenv("TASK_QUEUE", "background").lower()
logger = logging.getLogger(__name__)
_BACKGROUND_PROCESSING_SLOTS = BoundedSemaphore(
    resource_limits().concurrency["processing"]
)


def enqueue_document_processing(
    background_tasks: BackgroundTasks,
    document_id: int,
    file_path: str | None,
) -> None:
    if TASK_QUEUE == "celery":
        from app.worker import process_document_task

        # The worker reloads authoritative source metadata from PostgreSQL. Keep
        # file_path in this call signature for callers, but never serialize it.
        process_document_task.apply_async(args=(document_id,), retry=False)
        return

    if TASK_QUEUE != "background":
        raise ValueError("Unsupported task queue configuration")
    log_event(logger, logging.INFO, "document_background_dispatch", document_id=document_id)
    background_tasks.add_task(
        _run_document_processing,
        document_id,
    )


def _run_document_processing(
    document_id: int,
) -> str:
    from app.services.document_processing_service import (
        mark_processing_retries_exhausted,
        process_document,
    )

    retries = 0
    processing_failures = 0
    while True:
        try:
            outcome = process_document(
                document_id,
                processing_capacity=_BACKGROUND_PROCESSING_SLOTS,
            )
        except RetryableDocumentProcessingError:
            if processing_failures >= MAX_PROCESSING_RETRIES:
                try:
                    mark_processing_retries_exhausted(document_id)
                except Exception as error:
                    log_generation_failure(error, "document", document_id=document_id)
                log_event(
                    logger,
                    logging.WARNING,
                    "document_retries_exhausted",
                    document_id=document_id,
                )
                return "retry_exhausted"
            processing_failures += 1
            event = "document_retry_scheduled"
        except Exception as error:
            log_generation_failure(error, "document", document_id=document_id)
            return "failed"
        else:
            if outcome != "deferred":
                return outcome
            event = "document_processing_deferred"

        delay = retry_delay(retries)
        retries += 1
        log_event(
            logger,
            logging.INFO,
            event,
            document_id=document_id,
            retry=retries,
            delay_seconds=delay,
        )
        sleep(delay)
