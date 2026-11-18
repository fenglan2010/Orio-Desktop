"""Persistence helpers for the job_runs ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.jobs.runtime import SYSTEM_ACTOR
from app.models import JobRun
from app.services.workflow import write_audit

def _now() -> datetime:
    return datetime.utcnow()


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    text = value.isoformat()
    if not text.endswith("Z") and "+" not in text:
        text += "Z"
    return text


def dump_job_run(row: JobRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "hotel_id": row.hotel_id,
        "job_type": row.job_type,
        "status": row.status,
        "actor": row.actor,
        "started_at": isoformat(row.started_at),
        "finished_at": isoformat(row.finished_at),
        "result": row.result or {},
        "error": row.error or "",
    }


def start_job_run(
    db: Session,
    *,
    hotel_id: int | None,
    job_type: str,
    actor: str = SYSTEM_ACTOR,
) -> JobRun:
    row = JobRun(
        hotel_id=hotel_id,
        job_type=job_type,
        status="running",
        actor=actor or SYSTEM_ACTOR,
        started_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def finish_job_run(
    db: Session,
    row: JobRun,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
    audit_action: str | None = None,
    audit_reason: str = "",
) -> JobRun:
    row.status = status
    row.result = result or {}
    row.error = error or ""
    row.finished_at = _now()
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=row.actor or SYSTEM_ACTOR,
        action=audit_action or f"job_{row.job_type}",
        entity_type="job_run",
        entity_id=str(row.id),
        hotel_id=row.hotel_id,
        after={
            "job_type": row.job_type,
            "status": row.status,
            "hotel_id": row.hotel_id,
            "result": _public_result(result or {}),
            "error": (error or "")[:300],
        },
        reason=audit_reason or _default_reason(row),
    )
    db.refresh(row)
    return row


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that might look like a secret before auditing."""
    skip = {"official_response", "headers", "credentials", "authorization", "body", "request_headers"}
    return {key: value for key, value in result.items() if key not in skip}


def _default_reason(row: JobRun) -> str:
    if row.status == "succeeded":
        return f"{row.job_type} 已完成"
    if row.status == "skipped":
        return f"{row.job_type} 已跳过"
    if row.status == "failed":
        return f"{row.job_type} 失败：{(row.error or '')[:120]}"
    return f"{row.job_type} 状态 {row.status}"


def list_job_runs(
    db: Session,
    hotel_id: int,
    *,
    job_type: str | None = None,
    limit: int = 20,
) -> list[JobRun]:
    query = db.query(JobRun).filter(JobRun.hotel_id == hotel_id)
    if job_type:
        query = query.filter(JobRun.job_type == job_type)
    cap = max(1, min(int(limit or 20), 100))
    return query.order_by(JobRun.id.desc()).limit(cap).all()


def latest_run(db: Session, hotel_id: int, job_type: str) -> JobRun | None:
    return (
        db.query(JobRun)
        .filter(JobRun.hotel_id == hotel_id, JobRun.job_type == job_type)
        .order_by(JobRun.id.desc())
        .first()
    )


def job_status_for_hotel(db: Session, hotel_id: int, *, recent_limit: int = 8) -> dict[str, Any]:
    from app.jobs.runtime import JOB_INGEST, JOB_WRITE_RECONCILE, JOB_WRITE_RETRY

    def _latest(job_type: str) -> dict[str, Any] | None:
        row = latest_run(db, hotel_id, job_type)
        return dump_job_run(row) if row else None

    last_ingest = _latest(JOB_INGEST)
    ingested_at = None
    if last_ingest and last_ingest.get("status") in {"succeeded", "skipped"}:
        ingested_at = (last_ingest.get("result") or {}).get("last_ingested_at") or last_ingest.get("finished_at")
    return {
        "last_ingest": last_ingest,
        "last_ingested_at": ingested_at,
        "last_retry": _latest(JOB_WRITE_RETRY),
        "last_reconcile": _latest(JOB_WRITE_RECONCILE),
        "runs": [dump_job_run(row) for row in list_job_runs(db, hotel_id, limit=recent_limit)],
    }
