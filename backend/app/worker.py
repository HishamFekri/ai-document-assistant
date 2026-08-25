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
    # لا تعتبر الـ task ناجحة قبل انتهائها فعليًا
    task_acks_late=True,

    # إذا مات الـ worker أثناء التنفيذ،
    # رجّع الـ task للـ queue
    task_reject_on_worker_lost=True,

    # يسمح بحالة STARTED
    task_track_started=True,

    # كل worker يأخذ task واحدة مسبقًا فقط
    worker_prefetch_multiplier=1,

    # حاول الاتصال بالـ broker مرة أخرى
    # إذا Redis لم يكن جاهزًا لحظة تشغيل worker
    broker_connection_retry_on_startup=True,

    # مدة الاحتفاظ بنتائج Celery
    result_expires=3600,

    # Redis ينتظر مدة كافية قبل اعتبار
    # الـ task غير acknowledged وإعادة إرسالها
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

    # تحذير للـ task بعد 8 دقائق
    soft_time_limit=480,

    # قتل الـ task نهائيًا بعد 10 دقائق
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