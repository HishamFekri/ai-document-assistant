import os

from fastapi import BackgroundTasks


TASK_QUEUE = os.getenv("TASK_QUEUE", "background").lower()


def enqueue_document_processing(
    background_tasks: BackgroundTasks,
    document_id: int,
    file_path: str,
) -> None:
    if TASK_QUEUE == "celery":
        from app.worker import process_document_task

        process_document_task.delay(
            document_id,
            file_path,
        )
        return

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

    process_document(
        document_id,
        file_path,
    )
