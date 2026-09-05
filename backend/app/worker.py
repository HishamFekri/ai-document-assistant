import os

from celery import Celery


celery_app = Celery(
    "ai_document_assistant",

    broker=os.getenv(
        "CELERY_BROKER_URL",
        "redis://127.0.0.1:6379/0",
    ),

    backend=os.getenv(
        "CELERY_RESULT_BACKEND",
        "redis://127.0.0.1:6379/1",
    ),
)


celery_app.conf.update(

    task_acks_late=True,



    task_reject_on_worker_lost=True,


    task_track_started=True,


    worker_prefetch_multiplier=1,



    broker_connection_retry_on_startup=True,


    result_expires=3600,


    broker_transport_options={
        "visibility_timeout": 900,
    },
)


@celery_app.task(
    bind=True,

    autoretry_for=(
        Exception,
    ),

    retry_backoff=True,

    retry_backoff_max=60,

    retry_jitter=True,

    retry_kwargs={
        "max_retries": 3,
    },


    soft_time_limit=480,


    time_limit=600,
)
def process_document_task(
    self,
    document_id: int,
    file_path: str,
) -> None:
    from app.services.document_processing_service import (
        process_document,
    )

    process_document(
        document_id,
        file_path,
    )