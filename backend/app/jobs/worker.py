"""Standalone local worker. Same task functions as Celery, no Redis required.

    python -m app.jobs.worker
    bash scripts/worker.sh

Intervals come from environment variables. This process does not listen on a
network port.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from app.config import validate_runtime_settings
from app.database import init_db
from app.jobs.runtime import (
    SYSTEM_ACTOR,
    ingest_interval_seconds,
    job_queue_mode,
    reconcile_interval_seconds,
    retry_interval_seconds,
    startup_delay_seconds,
)
from app.jobs.tasks import run_ingest_all_hotels, run_reconcile_all_hotels, run_retry_all_hotels
from app.seed import ensure_seeded

log = logging.getLogger("oiro.jobs.worker")

_STOP = False


def _request_stop(signum, _frame) -> None:
    global _STOP
    _STOP = True
    log.info("received signal %s, stopping after the current cycle", signum)


def _run_safe(label: str, fn) -> None:
    try:
        result = fn(actor=SYSTEM_ACTOR)
        summary = ""
        if isinstance(result, dict):
            summary = f" hotels={result.get('hotels', '')}"
        log.info("%s completed%s", label, summary)
    except Exception:
        log.exception("%s failed", label)


def run_loop() -> None:
    jobs = [
        ("ingest", ingest_interval_seconds(), run_ingest_all_hotels),
        ("write_retry", retry_interval_seconds(), run_retry_all_hotels),
        ("write_reconcile", reconcile_interval_seconds(), run_reconcile_all_hotels),
    ]
    log.info(
        "local job worker schedule ingest=%ss retry=%ss reconcile=%ss",
        jobs[0][1],
        jobs[1][1],
        jobs[2][1],
    )
    delay = startup_delay_seconds()
    if delay:
        log.info("startup delay %ss", delay)
        deadline = time.monotonic() + delay
        while not _STOP and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))
    # First pass shortly after startup so last-run status is not empty for days.
    now = time.monotonic()
    next_due = {name: now for name, _interval, _fn in jobs}
    while not _STOP:
        now = time.monotonic()
        for name, interval, fn in jobs:
            if now >= next_due[name]:
                _run_safe(name, fn)
                next_due[name] = time.monotonic() + interval
        sleep_for = min((due - time.monotonic() for due in next_due.values()), default=1.0)
        if sleep_for > 0 and not _STOP:
            time.sleep(min(sleep_for, 1.0))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    once = "--once" in args
    mode = job_queue_mode()
    validate_runtime_settings()
    log.info("OIRO job worker starting mode=%s (connection strings are not logged)", mode)
    if mode == "idle":
        log.info("job queue is disabled (OIRO_JOB_QUEUE=off); exiting")
        return 0
    if mode == "celery" and not once:
        log.info(
            "A broker is configured. Start Celery with: "
            "celery -A app.jobs.celery_app worker --loglevel=info   and   "
            "celery -A app.jobs.celery_app beat --loglevel=info. "
            "To force this standalone loop, set OIRO_JOB_QUEUE=local."
        )
        return 0

    init_db()
    ensure_seeded()
    if once:
        _run_safe("ingest", run_ingest_all_hotels)
        _run_safe("write_retry", run_retry_all_hotels)
        _run_safe("write_reconcile", run_reconcile_all_hotels)
        return 0

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    run_loop()
    log.info("OIRO job worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
