from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.jobs.ledger import dump_job_run, job_status_for_hotel, list_job_runs
from app.models import Hotel, User
from app.services.permissions import require

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_jobs(
    job_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("view_writes")),
):
    status = job_status_for_hotel(db, hotel.id, recent_limit=limit)
    runs = [dump_job_run(row) for row in list_job_runs(db, hotel.id, job_type=job_type, limit=limit)]
    status["runs"] = runs
    return status
