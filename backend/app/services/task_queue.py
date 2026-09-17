import os
import logging

from fastapi import BackgroundTasks
from app.services.error_service import log_generation_failure
from app.services.observability import log_event


TASK_QUEUE = os.getenv("TASK_QUEUE", "background").lower()
logger = logging.getLogger(__name__)


def enqueue_document_processing(
    background_tasks: BackgroundTasks,
    document_id: int,
    file_path: str,
) -> None:
    if TASK_QUEUE == "celery":
        from app.worker import process_document_task

        process_document_task.apply_async(args=(document_id, file_path), retry=False)
        return

    if TASK_QUEUE != "background":
        raise ValueError("Unsupported task queue configuration")
    log_event(logger, logging.INFO, "document_development_dispatch", document_id=document_id)
    background_tasks.add_task(
        _run_document_processing,
        document_id,
        file_path,
    )


def _run_document_processing(
    document_id: int,
    file_path: str,
) -> None:
    from app.services.document_processing_service import process_document

    try:
        process_document(document_id, file_path)
    except Exception as error:
        # Failed/interrupted background work requires the explicit retry route.
        log_generation_failure(error, "document", document_id=document_id)
