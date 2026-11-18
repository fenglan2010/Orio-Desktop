"""Job-queue mode and schedule settings. Never log broker or database passwords."""

from __future__ import annotations

from app.config import get_settings
from app.database import redact_url

SYSTEM_ACTOR = "system:scheduler"

QUEUE_LOCAL = "local"
QUEUE_CELERY = "celery"
QUEUE_IDLE = "idle"

DEFAULT_INGEST_INTERVAL = 1800
DEFAULT_RETRY_INTERVAL = 120
DEFAULT_RECONCILE_INTERVAL = 300
DEFAULT_STARTUP_DELAY = 5

JOB_INGEST = "ingest"
JOB_WRITE_RETRY = "write_retry"
JOB_WRITE_RECONCILE = "write_reconcile"


def broker_url() -> str:
    settings = get_settings()
    return (settings.celery_broker_url or settings.redis_url or "").strip()


def job_queue_mode() -> str:
    """Return local, celery, or idle. Does not include connection strings."""
    settings = get_settings()
    raw = (settings.oiro_job_queue or "auto").strip().lower()
    if raw in {"off", "none", "idle", "disabled"}:
        return QUEUE_IDLE
    if raw in {"celery", "redis"}:
        return QUEUE_CELERY
    if raw == "local":
        return QUEUE_LOCAL
    return QUEUE_CELERY if broker_url() else QUEUE_LOCAL


def safe_broker_label() -> str:
    """Redacted broker URL for logs, or empty when unset. Prefer not to print this."""
    url = broker_url()
    return redact_url(url) if url else ""


def _as_int(value: object, default: int, minimum: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(minimum, number)


def ingest_interval_seconds() -> int:
    return _as_int(get_settings().oiro_ingest_interval_seconds, DEFAULT_INGEST_INTERVAL, 60)


def retry_interval_seconds() -> int:
    return _as_int(get_settings().oiro_write_retry_interval_seconds, DEFAULT_RETRY_INTERVAL, 30)


def reconcile_interval_seconds() -> int:
    return _as_int(get_settings().oiro_reconcile_interval_seconds, DEFAULT_RECONCILE_INTERVAL, 30)


def startup_delay_seconds() -> int:
    return _as_int(get_settings().oiro_job_startup_delay_seconds, DEFAULT_STARTUP_DELAY, 0)
