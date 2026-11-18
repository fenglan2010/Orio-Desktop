"""Shared job functions. Callable synchronously in tests; no broker required."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.jobs.ingest import run_ingest
from app.jobs.ledger import dump_job_run, finish_job_run, start_job_run
from app.jobs.runtime import JOB_INGEST, JOB_WRITE_RECONCILE, JOB_WRITE_RETRY, SYSTEM_ACTOR
from app.models import Hotel, WriteJob
from app.services.writes import is_retryable, reconcile_job, retry_write_job

log = logging.getLogger(__name__)

UNSETTLED_STATUSES = frozenset({"accepted", "failed", "timeout", "partial"})


def _hotel(db: Session, hotel_id: int) -> Hotel:
    hotel = db.get(Hotel, hotel_id)
    if hotel is None:
        raise ValueError(f"酒店不存在：{hotel_id}")
    return hotel


def _with_session(fn, *args, **kwargs):
    db = SessionLocal()
    try:
        return fn(db, *args, **kwargs)
    finally:
        db.close()


def ingest_hotel(db: Session, hotel_id: int, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    hotel = _hotel(db, hotel_id)
    run = start_job_run(db, hotel_id=hotel.id, job_type=JOB_INGEST, actor=actor)
    try:
        result = dict(run_ingest(db, hotel) or {})
        try:
            from app.services.impact import measure_hotel_episodes

            result["impact_measurement"] = measure_hotel_episodes(db, hotel.id)
        except Exception:
            log.exception("impact measurement failed hotel_id=%s", hotel_id)
            result["impact_measurement"] = {"hotel_id": hotel.id, "status": "failed", "updated": 0}
        status = result.get("status") or "succeeded"
        if status == "failed":
            finish_job_run(
                db,
                run,
                status="failed",
                result=result,
                error=result.get("error") or result.get("message") or "官方采集失败",
                audit_action="job_ingest",
                audit_reason=result.get("error") or result.get("message") or "官方采集失败",
            )
        else:
            finish_job_run(
                db,
                run,
                status="succeeded",
                result=result,
                audit_action="job_ingest",
                audit_reason=result.get("message")
                or (
                    "官方采集完成"
                    if result.get("source") == "official"
                    else "本地模拟数据刷新（未改写指标）"
                ),
            )
    except Exception as exc:
        log.exception("ingest failed hotel_id=%s", hotel_id)
        finish_job_run(
            db,
            run,
            status="failed",
            result={"source": "skipped", "official_http": False},
            error=str(exc),
            audit_action="job_ingest",
            audit_reason=f"采集失败：{exc}",
        )
        raise
    return dump_job_run(run)


def ingest_all_hotels(db: Session, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    hotels = db.query(Hotel).order_by(Hotel.id).all()
    runs: list[dict[str, Any]] = []
    for hotel in hotels:
        try:
            runs.append(ingest_hotel(db, hotel.id, actor=actor))
        except Exception as exc:
            runs.append({"hotel_id": hotel.id, "status": "failed", "error": str(exc), "job_type": JOB_INGEST})
    return {"job_type": "ingest_sweep", "hotels": len(hotels), "runs": runs}


def retry_hotel_writes(db: Session, hotel_id: int, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    hotel = _hotel(db, hotel_id)
    run = start_job_run(db, hotel_id=hotel.id, job_type=JOB_WRITE_RETRY, actor=actor)
    rows = (
        db.query(WriteJob)
        .filter(WriteJob.hotel_id == hotel.id)
        .order_by(WriteJob.id)
        .all()
    )
    retried: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for job in rows:
        if not is_retryable(job):
            skipped.append({"id": job.id, "status": job.status, "refuse_reason": job.refuse_reason or ""})
            continue
        try:
            updated = retry_write_job(db, job, actor=actor)
            retried.append({"id": updated.id, "status": updated.status, "attempt_count": updated.attempt_count})
        except ValueError as exc:
            skipped.append({"id": job.id, "status": job.status, "error": str(exc)})
        except Exception as exc:
            errors.append({"id": job.id, "error": str(exc)})
    result = {
        "retried": retried,
        "skipped": skipped,
        "errors": errors,
        "checked": len(rows),
        "retried_count": len(retried),
        "error_count": len(errors),
    }
    status = "failed" if errors and not retried else "succeeded"
    finish_job_run(
        db,
        run,
        status=status,
        result=result,
        error="" if status == "succeeded" else f"{len(errors)} 条写入重试失败",
        audit_action="job_write_retry",
        audit_reason=f"调度重试：成功 {len(retried)}，跳过 {len(skipped)}，失败 {len(errors)}",
    )
    return dump_job_run(run)


def retry_all_hotels(db: Session, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    hotels = db.query(Hotel).order_by(Hotel.id).all()
    runs: list[dict[str, Any]] = []
    for hotel in hotels:
        try:
            runs.append(retry_hotel_writes(db, hotel.id, actor=actor))
        except Exception as exc:
            runs.append({"hotel_id": hotel.id, "status": "failed", "error": str(exc), "job_type": JOB_WRITE_RETRY})
    return {"job_type": "write_retry_sweep", "hotels": len(hotels), "runs": runs}


def reconcile_hotel_writes(db: Session, hotel_id: int, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    hotel = _hotel(db, hotel_id)
    run = start_job_run(db, hotel_id=hotel.id, job_type=JOB_WRITE_RECONCILE, actor=actor)
    rows = (
        db.query(WriteJob)
        .filter(WriteJob.hotel_id == hotel.id, WriteJob.status.in_(tuple(UNSETTLED_STATUSES)))
        .order_by(WriteJob.id)
        .all()
    )
    checked: list[dict[str, Any]] = []
    mismatches = 0
    for job in rows:
        item = reconcile_job(db, job, actor=actor)
        checked.append({"id": job.id, "status": job.status, "ok": item["ok"], "issues": item.get("issues") or []})
        if not item["ok"]:
            mismatches += 1
    result = {
        "checked": len(checked),
        "mismatches": mismatches,
        "jobs": checked,
    }
    finish_job_run(
        db,
        run,
        status="succeeded",
        result=result,
        audit_action="job_write_reconcile",
        audit_reason=f"调度对账：检查 {len(checked)} 条，不一致 {mismatches} 条",
    )
    return dump_job_run(run)


def reconcile_all_hotels(db: Session, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    hotels = db.query(Hotel).order_by(Hotel.id).all()
    runs: list[dict[str, Any]] = []
    for hotel in hotels:
        try:
            runs.append(reconcile_hotel_writes(db, hotel.id, actor=actor))
        except Exception as exc:
            runs.append({"hotel_id": hotel.id, "status": "failed", "error": str(exc), "job_type": JOB_WRITE_RECONCILE})
    return {"job_type": "write_reconcile_sweep", "hotels": len(hotels), "runs": runs}


def run_ingest_hotel(hotel_id: int, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    return _with_session(ingest_hotel, hotel_id, actor=actor)


def run_ingest_all_hotels(*, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    return _with_session(ingest_all_hotels, actor=actor)


def run_retry_hotel_writes(hotel_id: int, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    return _with_session(retry_hotel_writes, hotel_id, actor=actor)


def run_retry_all_hotels(*, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    return _with_session(retry_all_hotels, actor=actor)


def run_reconcile_hotel_writes(hotel_id: int, *, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    return _with_session(reconcile_hotel_writes, hotel_id, actor=actor)


def run_reconcile_all_hotels(*, actor: str = SYSTEM_ACTOR) -> dict[str, Any]:
    return _with_session(reconcile_all_hotels, actor=actor)
