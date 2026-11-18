from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.services.trace import assemble_trace
from app.auth import get_current_user
from app.database import get_db
from app.models import Hotel, Review, User, WorkflowRun
from app.schemas import ReviewDecision
from app.services.permissions import has_permission, require, require_permission
from app.services.workflow import apply_human_decision, execute_approved_run, start_review_reply_run

router = APIRouter(prefix="/api/reviews", tags=["reviews"], dependencies=[Depends(get_current_user)])


def _dump(db: Session, r: Review) -> dict:
    run = db.get(WorkflowRun, r.workflow_run_id) if r.workflow_run_id else None
    tr = assemble_trace(db, r.workflow_run_id)
    return {
        "id": r.id,
        "external_id": r.external_id,
        "platform": r.platform,
        "guest_name": r.guest_name,
        "rating": r.rating,
        "title": r.title,
        "content": r.content,
        "review_date": r.review_date.isoformat() if r.review_date else None,
        "sentiment": r.sentiment,
        "reply_draft": r.reply_draft,
        "reply_status": r.reply_status,
        "executed_reply": r.executed_reply,
        "review_comment": r.review_comment,
        "workflow_run_id": r.workflow_run_id,
        "current_node": tr["current_node"] or (run.current_node if run else None),
        "risk_level": tr["risk_level"] or ((run.payload or {}).get("risk_level") if run else None),
        "checkpoint_id": tr["checkpoint_id"] or ((run.payload or {}).get("checkpoint_id") if run else None),
        "path": tr["path"],
        "checkpoints": tr["checkpoints"],
        "approvals": tr["approvals"],
    }


@router.get("")
def list_reviews(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    rows = db.query(Review).filter(Review.hotel_id == hotel.id).order_by(Review.review_date.desc()).all()
    return {"reviews": [_dump(db, r) for r in rows]}


@router.post("/{review_id}/draft")
def draft(
    review_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("generate")),
):
    review = db.get(Review, review_id)
    if review is None or review.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="评价不存在")
    run, _state = start_review_reply_run(db, hotel.id, user.username, review.id)
    db.refresh(review)
    return _dump(db, review)


@router.post("/{review_id}/review")
def review_decision(
    review_id: int,
    body: ReviewDecision,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    require_permission(user, "approve")
    review = db.get(Review, review_id)
    if review is None or review.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="评价不存在")
    if review.reply_status != "pending_review" or not review.workflow_run_id:
        raise HTTPException(status_code=400, detail="请先生成回复草稿并处于待人审状态")
    run = db.get(WorkflowRun, review.workflow_run_id)
    execute = body.decision.lower().strip() in {"approve", "approved"} and has_permission(user, "execute")
    try:
        apply_human_decision(db, run=run, actor=user.username, decision=body.decision, comment=body.comment, execute=execute)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.refresh(review)
    return _dump(db, review)


@router.post("/{review_id}/execute")
def execute_reply(
    review_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("execute")),
):
    review = db.get(Review, review_id)
    if review is None or review.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="评价不存在")
    if review.reply_status != "approved" or not review.workflow_run_id:
        raise HTTPException(status_code=400, detail="仅已通过、尚未执行的回复可执行")
    run = db.get(WorkflowRun, review.workflow_run_id)
    try:
        execute_approved_run(db, run, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.refresh(review)
    return _dump(db, review)


@router.post("/{review_id}/rollback")
def rollback_reply(
    review_id: int,
    body: ReviewDecision,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("rollback")),
):
    from app.schemas import RollbackRequest
    from app.services.workflow import rollback_run

    review = db.get(Review, review_id)
    if review is None or review.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="评价不存在")
    if review.reply_status not in {"executed", "execution_failed", "execution_partial"} or not review.workflow_run_id:
        raise HTTPException(status_code=400, detail="仅已执行、写入失败或部分成功的回复可回滚")
    run = db.get(WorkflowRun, review.workflow_run_id)
    try:
        rollback_run(db, run, user.username, body.comment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.refresh(review)
    return _dump(db, review)
