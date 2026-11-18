from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.models import Hotel, RoomType, Strategy, User, WorkflowRun
from app.schemas import GenerateStrategiesRequest, ReviewDecision, RollbackRequest
from app.services.trace import assemble_trace
from app.services.permissions import has_permission, require, require_permission
from app.services.workflow import execute_approved_run,  apply_human_decision, generate_all_strategies, rollback_run

router = APIRouter(prefix="/api/strategies", tags=["strategies"], dependencies=[Depends(get_current_user)])


def _trace(db: Session, run_id: int | None, strategy: Strategy | None = None) -> dict:
    return assemble_trace(db, run_id, strategy=strategy)


def _path(db: Session, run_id: int | None) -> list[dict]:
    return assemble_trace(db, run_id)["path"]


def _checkpoints(db: Session, run_id: int | None) -> list[dict]:
    return assemble_trace(db, run_id)["checkpoints"]


def _dump(db: Session, s: Strategy) -> dict:
    room = db.get(RoomType, s.room_type_id) if s.room_type_id else None
    run = db.get(WorkflowRun, s.workflow_run_id) if s.workflow_run_id else None
    tr = assemble_trace(db, s.workflow_run_id, strategy=s)
    return {
        "id": s.id,
        "kind": s.kind,
        "room_type_id": s.room_type_id,
        "room_name": room.name if room else None,
        "date_from": s.date_from.isoformat() if s.date_from else None,
        "date_to": s.date_to.isoformat() if s.date_to else None,
        "current_price": s.current_price,
        "min_price": s.min_price,
        "max_price": s.max_price,
        "recommended_price": s.recommended_price,
        "confidence": s.confidence,
        "rationale": s.rationale,
        "expected_impact": s.expected_impact,
        "constraints": s.constraints,
        "daily_breakdown": s.daily_breakdown,
        "campaign_advice": s.campaign_advice,
        "status": s.status,
        "risk_level": s.risk_level,
        "checkpoint_id": s.checkpoint_id,
        "reviewer": s.reviewer,
        "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        "review_comment": s.review_comment,
        "auto_executed": bool(
            (s.reviewer or "") == "system:auto_execute"
            or (s.execution_payload or {}).get("auto_executed")
            or ((run.payload or {}).get("auto_executed") if run else False)
        ),
        "execution_mode": (s.execution_payload or {}).get("mode"),
        "executed_at": s.executed_at.isoformat() if s.executed_at else None,
        "execution_payload": s.execution_payload,
        "rolled_back_at": s.rolled_back_at.isoformat() if s.rolled_back_at else None,
        "rollback_reason": s.rollback_reason,
        "workflow_run_id": s.workflow_run_id,
        "current_node": tr["current_node"] or (run.current_node if run else None),
        "approval_status": (run.payload or {}).get("approval_status") if run else s.status,
        "path": tr["path"],
        "checkpoints": tr["checkpoints"],
        "approvals": tr["approvals"],
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.get("")
def list_strategies(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    rows = db.query(Strategy).filter(Strategy.hotel_id == hotel.id).order_by(Strategy.id.desc()).all()
    return {"strategies": [_dump(db, s) for s in rows]}


@router.get("/{strategy_id}")
def get_strategy(strategy_id: int, db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    s = db.get(Strategy, strategy_id)
    if s is None or s.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="策略不存在")
    return _dump(db, s)


@router.post("/generate")
def generate(
    body: GenerateStrategiesRequest,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("generate")),
):
    created = generate_all_strategies(
        db,
        hotel.id,
        user.username,
        room_type_id=body.room_type_id,
        date_from=body.date_from,
        date_to=body.date_to,
        kind=body.kind,
    )
    ids = [c["strategy_id"] for c in created if c.get("strategy_id")]
    rows = db.query(Strategy).filter(Strategy.id.in_(ids)).all() if ids else []
    return {"created": len(rows), "strategies": [_dump(db, s) for s in rows]}


@router.post("/{strategy_id}/review")
def review(
    strategy_id: int,
    body: ReviewDecision,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    require_permission(user, "approve")
    s = db.get(Strategy, strategy_id)
    if s is None or s.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="策略不存在")
    if s.status != "pending_review":
        raise HTTPException(status_code=400, detail="仅待人审策略可审批")
    run = db.get(WorkflowRun, s.workflow_run_id)
    if run is None:
        raise HTTPException(status_code=400, detail="找不到对应工作流")
    execute = body.decision.lower().strip() in {"approve", "approved"} and has_permission(user, "execute")
    try:
        apply_human_decision(db, run=run, actor=user.username, decision=body.decision, comment=body.comment, execute=execute)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.refresh(s)
    return _dump(db, s)


@router.post("/{strategy_id}/execute")
def execute_strategy(
    strategy_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("execute")),
):
    s = db.get(Strategy, strategy_id)
    if s is None or s.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="策略不存在")
    if s.status != "approved":
        raise HTTPException(status_code=400, detail="仅已通过、尚未执行的策略可执行")
    run = db.get(WorkflowRun, s.workflow_run_id)
    if run is None:
        raise HTTPException(status_code=400, detail="找不到对应工作流")
    try:
        execute_approved_run(db, run, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.refresh(s)
    return _dump(db, s)


@router.post("/{strategy_id}/rollback")
def rollback(
    strategy_id: int,
    body: RollbackRequest,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("rollback")),
):
    s = db.get(Strategy, strategy_id)
    if s is None or s.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="策略不存在")
    if s.status not in {"executed", "execution_failed", "execution_partial"}:
        raise HTTPException(status_code=400, detail="仅已执行、写入失败或部分成功的策略可回滚")
    run = db.get(WorkflowRun, s.workflow_run_id)
    if run is None:
        raise HTTPException(status_code=400, detail="找不到对应工作流")
    try:
        rollback_run(db, run, user.username, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.refresh(s)
    return _dump(db, s)
