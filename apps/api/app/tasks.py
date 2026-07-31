from celery import Celery

from .config import get_settings
from .services.document_processing import process_document_sync

settings = get_settings()
celery_app = Celery("citemind", broker=settings.celery_broker_url, backend=settings.celery_result_backend)
celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_store_eager_result=False,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=900,
    task_soft_time_limit=840,
    worker_prefetch_multiplier=1,
)


@celery_app.task(
    bind=True,
    autoretry_for=(OSError, ConnectionError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="documents.process",
)
def process_document_task(self, document_id: int) -> None:  # type: ignore[no-untyped-def]
    process_document_sync(document_id)
