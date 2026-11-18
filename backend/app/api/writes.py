from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.models import Hotel, User, WriteJob
from app.services.permissions import require
from app.services.writes import dump_job, is_retryable, reconcile_hotel_jobs, reconcile_job, retry_write_job

router = APIRouter(prefix="/api/writes", tags=["writes"], dependencies=[Depends(get_current_user)])


def _get_job(db: Session, hotel: Hotel, write_id: int) -> WriteJob:
    job = db.get(WriteJob, write_id)
    if job is None or job.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="写入任务不存在")
    return job


@router.get("")
def list_writes(
    status: str | None = Query(default=None),
    strategy_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("view_writes")),
):
    q = db.query(WriteJob).filter(WriteJob.hotel_id == hotel.id)
    if status:
        q = q.filter(WriteJob.status == status)
    if strategy_id:
        q = q.filter(WriteJob.strategy_id == strategy_id)
    rows = q.order_by(WriteJob.id.desc()).all()
    return {
        "writes": [dump_job(j) for j in rows],
        "retryable": sum(1 for j in rows if is_retryable(j)),
        "open_failures": sum(1 for j in rows if j.status in {"failed", "timeout", "partial"}),
    }


@router.get("/{write_id}")
def get_write(write_id: int, db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel), user: User = Depends(require("view_writes"))):
    return dump_job(_get_job(db, hotel, write_id))


@router.post("/{write_id}/retry")
def retry_write(
    write_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("retry_writes")),
):
    job = _get_job(db, hotel, write_id)
    try:
        job = retry_write_job(db, job, actor=user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dump_job(job)


@router.post("/{write_id}/reconcile")
def reconcile_one(
    write_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("reconcile_writes")),
):
    job = _get_job(db, hotel, write_id)
    result = reconcile_job(db, job, actor=user.username)
    payload = dump_job(job)
    payload["reconcile"] = result
    return payload


@router.post("/reconcile")
def reconcile_batch(
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("reconcile_writes")),
):
    return reconcile_hotel_jobs(db, hotel.id, actor=user.username)
