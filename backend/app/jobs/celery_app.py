"""Celery application for production workers.

Import is safe without Redis. When no broker is configured, tasks run eagerly
in-process (for tests and local imports). Scheduled production use is:

    celery -A app.jobs.celery_app worker --loglevel=info
    celery -A app.jobs.celery_app beat --loglevel=info

Do not log the broker URL; it may contain a password.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import beat_init, worker_init

from app.jobs.runtime import (
    SYSTEM_ACTOR,
    broker_url,
    ingest_interval_seconds,
    job_queue_mode,
    reconcile_interval_seconds,
    retry_interval_seconds,
)
from app.jobs.tasks import (
    run_ingest_all_hotels,
    run_ingest_hotel,
    run_reconcile_all_hotels,
    run_retry_all_hotels,
)

_BROKER = broker_url()
celery_app = Celery("oiro", broker=_BROKER or "memory://")
celery_app.conf.update(
    task_always_eager=not _BROKER,
    task_eager_propagates=True,
    task_ignore_result=True,
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=False,
    worker_hijack_root_logger=False,
    beat_schedule={
        "ingest-all-hotels": {
            "task": "oiro.ingest_all_hotels",
            "schedule": float(ingest_interval_seconds()),
        },
        "retry-write-jobs": {
            "task": "oiro.retry_all_write_jobs",
            "schedule": float(retry_interval_seconds()),
        },
        "reconcile-write-jobs": {
            "task": "oiro.reconcile_all_write_jobs",
            "schedule": float(reconcile_interval_seconds()),
        },
    },
)


@celery_app.task(name="oiro.ingest_hotel")
def ingest_hotel_task(hotel_id: int, actor: str = SYSTEM_ACTOR):
    return run_ingest_hotel(int(hotel_id), actor=actor or SYSTEM_ACTOR)


@celery_app.task(name="oiro.ingest_all_hotels")
def ingest_all_hotels_task(actor: str = SYSTEM_ACTOR):
    return run_ingest_all_hotels(actor=actor or SYSTEM_ACTOR)


@celery_app.task(name="oiro.retry_all_write_jobs")
def retry_all_write_jobs_task(actor: str = SYSTEM_ACTOR):
    return run_retry_all_hotels(actor=actor or SYSTEM_ACTOR)


@celery_app.task(name="oiro.reconcile_all_write_jobs")
def reconcile_all_write_jobs_task(actor: str = SYSTEM_ACTOR):
    return run_reconcile_all_hotels(actor=actor or SYSTEM_ACTOR)


def celery_ready() -> bool:
    return job_queue_mode() == "celery" and bool(broker_url())


def _validate_celery_process(**_kwargs) -> None:
    from app.config import validate_runtime_settings

    validate_runtime_settings()


worker_init.connect(_validate_celery_process)
beat_init.connect(_validate_celery_process)
