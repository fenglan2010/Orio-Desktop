from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.services.permissions import require
from app.database import get_db
from app.models import AuditLog, Hotel, ModelCallLog, WorkflowRun

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require("view_audit"))])


@router.get("")
def list_audit(
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    limit: int = Query(200, le=500),
):
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.hotel_id == hotel.id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "logs": [
            {
                "id": r.id,
                "actor": r.actor,
                "action": r.action,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "before": r.before,
                "after": r.after,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.get("/model-calls")
def model_calls(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel), limit: int = Query(200, le=500)):
    rows = (
        db.query(ModelCallLog)
        .filter(ModelCallLog.hotel_id == hotel.id)
        .order_by(ModelCallLog.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "logs": [
            {
                "id": r.id,
                "vendor": r.vendor,
                "model": r.model,
                "task_type": r.task_type,
                "hotel_id": r.hotel_id,
                "outbound": r.outbound,
                "degraded": r.degraded,
                "status": r.status,
                "latency_ms": r.latency_ms,
                "error_code": r.error_code,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.get("/workflows/{run_id}")
def workflow_detail(run_id: int, db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    run = db.get(WorkflowRun, run_id)
    if run is None or run.hotel_id != hotel.id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="工作流不存在")
    from app.api.strategies import _checkpoints, _path

    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "current_node": run.current_node,
        "payload": run.payload,
        "related_strategy_id": run.related_strategy_id,
        "related_review_id": run.related_review_id,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "path": _path(db, run.id),
        "checkpoints": _checkpoints(db, run.id),
    }
