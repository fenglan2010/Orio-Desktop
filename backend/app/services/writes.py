"""Durable official-write ledger, reconciliation, retries and write alerts.

This layer records intended writes and official-shaped receipts. Live official
HTTP is sent only through OfficialWriteAdapter when the channel is authorized,
credentials exist, the write flag is on, and an official contract is configured.
Missing any of those refuses without opening a socket. Reconciliation rules are
unchanged.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.ota.official_write import (
    OfficialWriteAdapter,
    OUTCOME_ACCEPTED,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_REFUSED,
    OUTCOME_TIMEOUT,
    build_intended_items,
)
from app.models import Alert, Review, Strategy, WorkflowRun, WriteJob
from app.services.risk_policy import load_execution_policy

MAX_ATTEMPTS = 3
ALERT_WRITE_FAILED = "write_failed"
ALERT_RECONCILE = "reconcile_mismatch"

RETRYABLE_STATUSES = {"failed", "timeout", "partial"}
NON_RETRYABLE_REASONS = {
    "missing_credentials",
    "live_api_not_connected",
    "missing_contract",
    "channel_unauthorized",
    "write_disabled",
    "http_4xx",
    "review_reply_not_auto_posted",
    "action_not_in_contract",
}
TERMINAL_OK = {"accepted", "reconciled", "rolled_back"}

STATUS_FROM_OUTCOME = {
    OUTCOME_ACCEPTED: "accepted",
    OUTCOME_PARTIAL: "partial",
    OUTCOME_TIMEOUT: "timeout",
    OUTCOME_FAILED: "failed",
    OUTCOME_REFUSED: "failed",
}


def _now() -> datetime:
    return datetime.utcnow()


def _norm_kind(kind: str | None) -> str:
    value = (kind or "pricing").lower().strip()
    if value in {"pricing", "pricing_strategy"}:
        return "pricing"
    if value in {"campaign", "campaign_advice"}:
        return "campaign"
    if value in {"review_reply", "review"}:
        return "review_reply"
    return value


def _action_for(kind: str) -> str:
    return {
        "pricing": "apply_price",
        "campaign": "apply_campaign",
        "review_reply": "post_review_reply",
    }.get(kind, "apply_price")


def is_retryable(job: WriteJob) -> bool:
    if job.status in TERMINAL_OK:
        return False
    if job.refuse_reason in NON_RETRYABLE_REASONS:
        return False
    if (job.receipt or {}).get("refuse_reason") in NON_RETRYABLE_REASONS:
        return False
    if job.attempt_count >= (job.max_attempts or MAX_ATTEMPTS):
        return False
    if job.kind == "review_reply" and job.actor == "system:auto_execute":
        return False
    return job.status in RETRYABLE_STATUSES or job.status in {"pending", "writing"}


def build_state_payload(state: dict, strategy: Strategy | None = None, review: Review | None = None) -> dict[str, Any]:
    kind = _norm_kind(state.get("kind") or (strategy.kind if strategy else None) or ("review_reply" if review else "pricing"))
    strat = state.get("strategy") or {}
    checkpoint_id = state.get("checkpoint_id") or (strategy.checkpoint_id if strategy else "") or ""
    payload: dict[str, Any] = {
        "strategy_id": state.get("strategy_id") or (strategy.id if strategy else None),
        "review_id": state.get("review_id") or (review.id if review else None),
        "kind": kind,
        "checkpoint_id": checkpoint_id,
        "room_type_id": strat.get("room_type_id") or state.get("room_type_id") or (strategy.room_type_id if strategy else None),
        "room_name": strat.get("room_name"),
        "date_from": strat.get("date_from") or state.get("date_from") or (strategy.date_from.isoformat() if strategy and strategy.date_from else None),
        "date_to": strat.get("date_to") or state.get("date_to") or (strategy.date_to.isoformat() if strategy and strategy.date_to else None),
        "recommended_price": strat.get("recommended_price") if strat.get("recommended_price") is not None else (strategy.recommended_price if strategy else None),
        "current_price": strat.get("current_price") if strat.get("current_price") is not None else (strategy.current_price if strategy else None),
        "daily_breakdown": strat.get("daily_breakdown") or (strategy.daily_breakdown if strategy else None),
        "campaign": strat.get("campaign_advice") or (strategy.campaign_advice if strategy else None),
    }
    if review is not None:
        payload["reply"] = review.reply_draft
        payload["review_id"] = review.id
    if state.get("_sandbox_simulate") or strat.get("_sandbox_simulate"):
        payload["_sandbox_simulate"] = state.get("_sandbox_simulate") or strat.get("_sandbox_simulate")
    payload["items"] = build_intended_items(payload, _action_for(kind))
    return payload


def dump_job(job: WriteJob) -> dict[str, Any]:
    receipt = job.receipt or {}
    result = job.reconcile_result or {}
    return {
        "id": job.id,
        "hotel_id": job.hotel_id,
        "strategy_id": job.strategy_id,
        "review_id": job.review_id,
        "workflow_run_id": job.workflow_run_id,
        "kind": job.kind,
        "action": job.action,
        "platform": job.platform,
        "write_mode": job.write_mode,
        "status": job.status,
        "checkpoint_id": job.checkpoint_id,
        "intended_payload": job.intended_payload,
        "receipt": receipt,
        "request_id": job.request_id or receipt.get("request_id") or "",
        "receipt_id": job.receipt_id or receipt.get("receipt_id") or "",
        "receipt_mode": receipt.get("mode") or ("sandbox_official" if job.write_mode == "sandbox" else job.write_mode),
        "outcome": receipt.get("outcome") or job.status,
        "items": job.line_results or receipt.get("items") or [],
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts or MAX_ATTEMPTS,
        "retryable": is_retryable(job),
        "last_error": job.last_error or "",
        "refuse_reason": job.refuse_reason or receipt.get("refuse_reason") or "",
        "reconciled_at": job.reconciled_at.isoformat() if job.reconciled_at else None,
        "reconcile_result": result,
        "reconcile_ok": None if not result else not bool(result.get("issues")),
        "actor": job.actor,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _upsert_alert(
    db: Session,
    *,
    hotel_id: int,
    stable_key: str,
    alert_type: str,
    title: str,
    body: str,
    payload: dict | None,
    severity: str = "high",
) -> Alert:
    row = (
        db.query(Alert)
        .filter(Alert.hotel_id == hotel_id, Alert.stable_key == stable_key)
        .first()
    )
    if row is None:
        row = Alert(
            hotel_id=hotel_id,
            stable_key=stable_key,
            alert_type=alert_type,
            severity=severity,
            title=title,
            body=body,
            metric_date=date.today(),
            payload=payload,
            status="open",
        )
        db.add(row)
    else:
        row.alert_type = alert_type
        row.severity = severity
        row.title = title
        row.body = body
        row.payload = payload
        row.status = "open"
        row.metric_date = date.today()
    db.commit()
    db.refresh(row)
    return row


def _ack_alert(db: Session, hotel_id: int, stable_key: str) -> None:
    row = (
        db.query(Alert)
        .filter(Alert.hotel_id == hotel_id, Alert.stable_key == stable_key, Alert.status == "open")
        .first()
    )
    if row is not None:
        row.status = "acknowledged"
        db.commit()


def _write_alert_for_job(db: Session, job: WriteJob) -> Alert | None:
    if job.status in {"accepted", "reconciled", "rolled_back", "pending", "writing"}:
        if job.status in {"accepted", "reconciled", "rolled_back"}:
            _ack_alert(db, job.hotel_id, f"write_failed:{job.id}")
        return None
    mode = (job.receipt or {}).get("mode") or job.write_mode
    live = mode == "live"
    if job.status == "partial":
        title = f"写入部分成功（任务 #{job.id}）"
        body = job.last_error or ("官方回执显示部分行项目未写入，请对账后决定重试或回滚。" if live else "官方沙盒回执显示部分行项目未写入，请对账后决定重试或回滚。")
        severity = "medium"
    elif job.status == "timeout":
        title = f"写入超时（任务 #{job.id}）"
        body = job.last_error or ("未在时限内收到官方回执。" if live else "未在时限内收到官方回执。本记录仍是沙盒回执，未向真实 OTA 发送请求。")
        severity = "high"
    else:
        title = f"写入失败（任务 #{job.id}）"
        body = job.last_error or ("官方写入失败。" if live else "官方沙盒写入失败。未向真实 OTA 发送请求。")
        severity = "high"
    return _upsert_alert(
        db,
        hotel_id=job.hotel_id,
        stable_key=f"write_failed:{job.id}",
        alert_type=ALERT_WRITE_FAILED,
        title=title,
        body=body,
        payload={"write_job_id": job.id, "status": job.status, "strategy_id": job.strategy_id, "review_id": job.review_id},
        severity=severity,
    )


def _apply_receipt(job: WriteJob, receipt: dict) -> None:
    outcome = (receipt or {}).get("outcome") or (OUTCOME_ACCEPTED if receipt.get("accepted") else OUTCOME_FAILED)
    job.receipt = receipt
    job.request_id = receipt.get("request_id") or job.request_id or ""
    job.receipt_id = receipt.get("receipt_id") or ""
    job.line_results = receipt.get("items") or []
    job.last_error = "" if receipt.get("accepted") else (receipt.get("message") or job.last_error or "")
    job.refuse_reason = receipt.get("refuse_reason") or ""
    job.status = STATUS_FROM_OUTCOME.get(outcome, "failed")
    job.updated_at = _now()


def create_write_job(
    db: Session,
    *,
    hotel_id: int,
    kind: str,
    payload: dict,
    checkpoint_id: str = "",
    platform: str = "ctrip",
    write_mode: str = "sandbox",
    strategy_id: int | None = None,
    review_id: int | None = None,
    workflow_run_id: int | None = None,
    actor: str = "",
    max_attempts: int = MAX_ATTEMPTS,
) -> WriteJob:
    kind_n = _norm_kind(kind)
    job = WriteJob(
        hotel_id=hotel_id,
        strategy_id=strategy_id,
        review_id=review_id,
        workflow_run_id=workflow_run_id,
        kind=kind_n,
        action=_action_for(kind_n),
        platform=platform,
        write_mode=write_mode if write_mode in {"sandbox", "live"} else "sandbox",
        status="pending",
        checkpoint_id=checkpoint_id or (payload or {}).get("checkpoint_id") or "",
        intended_payload=payload,
        attempt_count=0,
        max_attempts=max_attempts,
        actor=actor,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def attempt_write(
    db: Session,
    job: WriteJob,
    *,
    actor: str = "",
    simulate: str | None = None,
) -> WriteJob:
    """Run one official-write attempt. Live HTTP only when authorization + contract + flag allow it."""
    from app.services.workflow import write_audit

    if job.refuse_reason in NON_RETRYABLE_REASONS and job.attempt_count > 0:
        raise ValueError("该写入因缺少凭证、官方契约或授权被拒绝，不能当作瞬时网络错误重试")
    if job.attempt_count >= (job.max_attempts or MAX_ATTEMPTS) and job.status not in {"pending"}:
        raise ValueError(f"已达到最大重试次数 {job.max_attempts or MAX_ATTEMPTS}，不能继续重试")
    if job.status in {"rolled_back"}:
        raise ValueError("已回滚的写入任务不能重试")
    if job.status in {"accepted", "reconciled"}:
        raise ValueError("该写入已成功，无需重试")
    if job.kind == "review_reply" and (actor or job.actor) == "system:auto_execute":
        raise ValueError("评价回复不会自动发布，也不能按自动执行路径重试")

    job.status = "writing"
    if actor:
        job.actor = actor
    job.updated_at = _now()
    db.commit()

    payload = dict(job.intended_payload or {})
    payload["checkpoint_id"] = job.checkpoint_id or payload.get("checkpoint_id") or ""
    if simulate:
        payload["_sandbox_simulate"] = simulate
    writer = OfficialWriteAdapter(platform=job.platform or "ctrip")
    live_context = None
    if (job.write_mode or "").lower() in {"live", "production", "official_live"}:
        from app.services.channels import resolve_live_write_context

        live_context = resolve_live_write_context(db, job.hotel_id, job.platform or "ctrip")
    if job.action == "post_review_reply":
        receipt = writer.post_review_reply(payload, write_mode=job.write_mode, simulate=simulate, live_context=live_context)
    elif job.action == "apply_campaign":
        receipt = writer.apply_campaign(payload, write_mode=job.write_mode, simulate=simulate, live_context=live_context)
    else:
        receipt = writer.apply_price(payload, write_mode=job.write_mode, simulate=simulate, live_context=live_context)

    job.attempt_count = int(job.attempt_count or 0) + 1
    _apply_receipt(job, receipt)
    db.commit()
    db.refresh(job)
    if (job.receipt or {}).get("mode") == "live" and job.status in {"accepted", "partial", "reconciled"}:
        from app.services.channels import mark_channel_verified

        mark_channel_verified(db, job.hotel_id, job.platform or "ctrip")

    write_audit(
        db,
        actor=job.actor or actor or "system",
        action="write_attempt",
        entity_type="write_job",
        entity_id=str(job.id),
        after={
            "status": job.status,
            "attempt_count": job.attempt_count,
            "receipt_id": job.receipt_id,
            "request_id": job.request_id,
            "mode": (job.receipt or {}).get("mode"),
            "outcome": (job.receipt or {}).get("outcome"),
            "checkpoint_id": job.checkpoint_id,
        },
        reason=job.last_error or f"写入任务 #{job.id} 第 {job.attempt_count} 次尝试，结果 {job.status}",
    )
    _write_alert_for_job(db, job)
    if job.status in {"accepted"}:
        _sync_entities_after_success(db, job)
        reconcile_job(db, job, actor=job.actor or actor or "system", raise_alert=True)
    elif job.status == "partial":
        _sync_entities_after_partial(db, job)
        reconcile_job(db, job, actor=job.actor or actor or "system", raise_alert=True)
    else:
        _sync_entities_after_failure(db, job)
        _maybe_auto_rollback(db, job)
    db.refresh(job)
    return job


def _strategy(db: Session, job: WriteJob) -> Strategy | None:
    return db.get(Strategy, job.strategy_id) if job.strategy_id else None


def _review(db: Session, job: WriteJob) -> Review | None:
    return db.get(Review, job.review_id) if job.review_id else None


def _run(db: Session, job: WriteJob) -> WorkflowRun | None:
    return db.get(WorkflowRun, job.workflow_run_id) if job.workflow_run_id else None


def _sync_entities_after_success(db: Session, job: WriteJob) -> None:
    receipt = job.receipt or {}
    strategy = _strategy(db, job)
    if strategy and strategy.status != "rolled_back":
        strategy.status = "executed"
        strategy.executed_at = _now()
        strategy.execution_payload = receipt
        if job.checkpoint_id:
            strategy.checkpoint_id = job.checkpoint_id
    review = _review(db, job)
    if review is not None and review.reply_status != "rolled_back":
        review.executed_reply = (job.intended_payload or {}).get("reply") or review.reply_draft
        review.reply_status = "executed"
    run = _run(db, job)
    if run is not None and run.status != "rolled_back":
        run.status = "executed"
        payload = dict(run.payload or {})
        payload["execution_result"] = receipt
        payload["write_job_id"] = job.id
        payload["status"] = "executed"
        run.payload = payload
        run.error = None
    db.commit()


def _sync_entities_after_partial(db: Session, job: WriteJob) -> None:
    receipt = job.receipt or {}
    strategy = _strategy(db, job)
    if strategy and strategy.status not in {"rolled_back"}:
        strategy.status = "execution_partial"
        strategy.execution_payload = receipt
        if job.checkpoint_id:
            strategy.checkpoint_id = job.checkpoint_id
    review = _review(db, job)
    if review is not None and review.reply_status not in {"rolled_back", "executed"}:
        review.reply_status = "execution_partial"
    run = _run(db, job)
    if run is not None and run.status not in {"rolled_back"}:
        run.status = "execution_partial"
        payload = dict(run.payload or {})
        payload["execution_result"] = receipt
        payload["write_job_id"] = job.id
        payload["status"] = "execution_partial"
        run.payload = payload
        run.error = job.last_error
    db.commit()


def _sync_entities_after_failure(db: Session, job: WriteJob) -> None:
    receipt = job.receipt or {}
    strategy = _strategy(db, job)
    if strategy and strategy.status not in {"rolled_back", "executed"}:
        strategy.status = "execution_failed"
        strategy.execution_payload = receipt
        if job.checkpoint_id:
            strategy.checkpoint_id = job.checkpoint_id
    review = _review(db, job)
    if review is not None and review.reply_status not in {"rolled_back", "executed"}:
        review.reply_status = "execution_failed"
    run = _run(db, job)
    if run is not None and run.status not in {"rolled_back", "executed"}:
        run.status = "execution_failed"
        payload = dict(run.payload or {})
        payload["execution_result"] = receipt
        payload["write_job_id"] = job.id
        payload["status"] = "execution_failed"
        payload["error"] = job.last_error
        run.payload = payload
        run.error = job.last_error
    db.commit()


def _maybe_auto_rollback(db: Session, job: WriteJob) -> None:
    """Roll back via the checkpoint path when the hotel asked for auto-on-write-failure.

    Retryable failures wait until attempts are exhausted. Refused live writes
    (missing credentials / API not connected) are not retried and roll back immediately.
    Partial leftovers are left for the operator to reconcile; they are not auto-rolled back.
    """
    if job.status == "partial":
        return
    policy = load_execution_policy(db, hotel_id=job.hotel_id)
    if policy.rollback_policy != "auto_on_write_failure":
        return
    retryable = is_retryable(job)
    exhausted = job.attempt_count >= (job.max_attempts or MAX_ATTEMPTS)
    if retryable and not exhausted:
        return
    run = _run(db, job)
    if run is None:
        return
    from app.services.workflow import rollback_run

    try:
        rollback_run(
            db,
            run,
            job.actor or "system:write_failure",
            reason=f"写入失败后按回滚策略自动回退（任务 #{job.id}，状态 {job.status}）",
        )
    except ValueError:
        # No checkpoint or already rolled back — still mark the job.
        mark_jobs_rolled_back(db, workflow_run_id=job.workflow_run_id, actor=job.actor)


def mark_jobs_rolled_back(db: Session, *, workflow_run_id: int | None, actor: str = "") -> None:
    if not workflow_run_id:
        return
    rows = db.query(WriteJob).filter(WriteJob.workflow_run_id == workflow_run_id).all()
    for job in rows:
        if job.status == "rolled_back":
            continue
        job.status = "rolled_back"
        job.updated_at = _now()
        receipt = dict(job.receipt or {})
        receipt["rolled_back"] = True
        job.receipt = receipt
        _ack_alert(db, job.hotel_id, f"write_failed:{job.id}")
        _ack_alert(db, job.hotel_id, f"reconcile_mismatch:{job.id}")
    db.commit()


def retry_write_job(db: Session, job: WriteJob, *, actor: str = "", simulate: str | None = None) -> WriteJob:
    if not is_retryable(job) and not simulate:
        if job.refuse_reason in NON_RETRYABLE_REASONS or (job.receipt or {}).get("refuse_reason") in NON_RETRYABLE_REASONS:
            raise ValueError("该写入因缺少凭证、官方契约或授权被拒绝，不能当作瞬时网络错误重试")
        if job.kind == "review_reply" and job.actor == "system:auto_execute":
            raise ValueError("评价回复不会自动发布，也不能按自动执行路径重试")
        if job.attempt_count >= (job.max_attempts or MAX_ATTEMPTS):
            raise ValueError(f"已达到最大重试次数 {job.max_attempts or MAX_ATTEMPTS}")
        raise ValueError("当前写入任务不可重试")
    return attempt_write(db, job, actor=actor or job.actor, simulate=simulate)


def execute_state_write(
    db: Session,
    state: dict,
    *,
    policy=None,
    platform: str = "ctrip",
    simulate: str | None = None,
) -> tuple[WriteJob, dict]:
    """Create a ledger row and perform the first official-write attempt for a workflow state."""
    kind = _norm_kind(state.get("kind"))
    strategy = db.get(Strategy, state["strategy_id"]) if state.get("strategy_id") else None
    review = db.get(Review, int(state["review_id"])) if state.get("review_id") else None
    policy = policy or load_execution_policy(
        db,
        hotel_id=state.get("hotel_id") or (strategy.hotel_id if strategy else None) or (review.hotel_id if review else None),
    )
    payload = build_state_payload(state, strategy, review)
    if simulate:
        payload["_sandbox_simulate"] = simulate
        state["_sandbox_simulate"] = simulate
    job = create_write_job(
        db,
        hotel_id=int(state.get("hotel_id") or (strategy.hotel_id if strategy else 0) or (review.hotel_id if review else 0)),
        kind=kind,
        payload=payload,
        checkpoint_id=state.get("checkpoint_id") or "",
        platform=platform,
        write_mode=policy.write_mode,
        strategy_id=strategy.id if strategy else None,
        review_id=review.id if review else None,
        workflow_run_id=int(state["run_id"]) if state.get("run_id") else None,
        actor=str(state.get("actor") or ""),
    )
    job = attempt_write(db, job, actor=job.actor, simulate=simulate)
    receipt = job.receipt or {}
    state["write_job_id"] = job.id
    state["execution_result"] = receipt
    return job, receipt


def _item_key(item: dict) -> str:
    if item.get("type") == "price" or item.get("date") or item.get("intended_price") is not None:
        return f"price:{item.get('room_type_id')}:{item.get('date') or item.get('date_from') or ''}"
    if item.get("type") == "campaign_signup" or item.get("campaign_name"):
        return f"campaign:{item.get('campaign_name')}"
    if item.get("type") == "review_reply" or item.get("review_id"):
        return f"review:{item.get('review_id')}"
    return repr(sorted((k, str(v)) for k, v in item.items() if k not in {"accepted", "status", "error", "actual_price"}))


def reconcile_job(db: Session, job: WriteJob, *, actor: str = "", raise_alert: bool = True) -> dict[str, Any]:
    """Compare intended write vs receipt vs persisted strategy/review state."""
    from app.services.workflow import write_audit

    issues: list[dict[str, str]] = []
    receipt = job.receipt or {}
    intended = job.intended_payload or {}
    intended_items = intended.get("items") or build_intended_items(intended, job.action)
    receipt_items = job.line_results or receipt.get("items") or []

    if job.status in {"pending", "writing"}:
        issues.append({"code": "missing_receipt", "message": "写入尚未完成，没有可用的官方回执。"})
    elif job.status == "timeout" and not job.receipt_id:
        issues.append({"code": "missing_receipt", "message": "写入超时，回执缺少 receipt_id。"})
    elif not receipt:
        issues.append({"code": "missing_receipt", "message": "账本中没有官方回执。"})

    intended_map = {_item_key(i): i for i in intended_items}
    receipt_map = {_item_key(i): i for i in receipt_items}
    for key, item in intended_map.items():
        got = receipt_map.get(key)
        if got is None:
            issues.append({"code": "missing_line", "message": f"回执缺少行项目 {key}"})
            continue
        intended_price = item.get("intended_price")
        actual = got.get("actual_price")
        if intended_price is not None and got.get("accepted") and actual is not None and float(actual) != float(intended_price):
            issues.append(
                {
                    "code": "price_mismatch",
                    "message": f"{key} 拟写入 {intended_price}，回执为 {actual}",
                }
            )
        if got.get("status") in {"failed", "timeout"} or got.get("accepted") is False:
            issues.append({"code": "partial_leftover", "message": f"{key} 未成功写入（{got.get('status') or 'failed'}）"})

    strategy = _strategy(db, job)
    review = _review(db, job)
    outcome = receipt.get("outcome")
    if outcome == OUTCOME_ACCEPTED or job.status in {"accepted", "reconciled"}:
        if strategy is not None and strategy.status not in {"executed", "rolled_back"}:
            issues.append(
                {
                    "code": "accepted_but_state_not_executed",
                    "message": f"回执为已接受，但策略状态仍是 {strategy.status}",
                }
            )
        if review is not None and review.reply_status not in {"executed", "rolled_back"}:
            issues.append(
                {
                    "code": "accepted_but_state_not_executed",
                    "message": f"回执为已接受，但评价回复状态仍是 {review.reply_status}",
                }
            )
    if strategy is not None and strategy.status == "executed" and job.status in {"failed", "timeout"}:
        issues.append({"code": "state_mismatch", "message": "策略已标记为已执行，但写入账本不是成功状态。"})
    if strategy is not None and strategy.status == "executed" and job.status == "partial":
        issues.append({"code": "state_mismatch", "message": "策略已全部标记为已执行，但回执仍有未写入的行项目。"})

    leftover = [i for i in receipt_items if i.get("accepted") is False or i.get("status") in {"failed", "timeout"}]
    if job.status == "partial" or leftover:
        if not any(i["code"] == "partial_leftover" for i in issues):
            issues.append({"code": "partial_leftover", "message": f"仍有 {len(leftover)} 条行项目未成功写入。"})

    ok = not issues
    unexpected_codes = {"accepted_but_state_not_executed", "price_mismatch", "state_mismatch"}
    if job.status in {"accepted", "reconciled"}:
        unexpected_codes |= {"missing_receipt", "missing_line"}
    unexpected = [i for i in issues if i["code"] in unexpected_codes]
    result = {
        "ok": ok,
        "issues": issues,
        "unexpected_issues": unexpected,
        "checked_at": _now().isoformat() + "Z",
        "intended_lines": len(intended_items),
        "receipt_lines": len(receipt_items),
        "job_status": job.status,
        "strategy_status": strategy.status if strategy is not None else None,
        "review_status": review.reply_status if review is not None else None,
    }
    job.reconcile_result = result
    job.reconciled_at = _now()
    if ok and job.status == "accepted":
        job.status = "reconciled"
        _ack_alert(db, job.hotel_id, f"reconcile_mismatch:{job.id}")
        _ack_alert(db, job.hotel_id, f"write_failed:{job.id}")
    elif unexpected and raise_alert and job.status != "rolled_back":
        codes = "、".join(sorted({i["code"] for i in issues}))
        _upsert_alert(
            db,
            hotel_id=job.hotel_id,
            stable_key=f"reconcile_mismatch:{job.id}",
            alert_type=ALERT_RECONCILE,
            title=f"写入对账不一致（任务 #{job.id}）",
            body="对账发现：" + "；".join(i["message"] for i in issues[:4]),
            payload={"write_job_id": job.id, "codes": codes, "issues": issues},
            severity="high",
        )
    elif ok:
        _ack_alert(db, job.hotel_id, f"reconcile_mismatch:{job.id}")
    job.updated_at = _now()
    db.commit()
    write_audit(
        db,
        actor=actor or job.actor or "system:reconcile",
        action="write_reconcile",
        entity_type="write_job",
        entity_id=str(job.id),
        after={"ok": ok, "issues": issues, "status": job.status},
        reason="对账通过" if ok else "对账发现不一致：" + "；".join(i["message"] for i in issues[:3]),
    )
    db.refresh(job)
    return result


def reconcile_hotel_jobs(db: Session, hotel_id: int, *, actor: str = "") -> dict[str, Any]:
    rows = (
        db.query(WriteJob)
        .filter(WriteJob.hotel_id == hotel_id)
        .order_by(WriteJob.id.desc())
        .all()
    )
    results = []
    issues = 0
    for job in rows:
        item = reconcile_job(db, job, actor=actor or "system:reconcile")
        results.append({"id": job.id, "status": job.status, "ok": item["ok"], "issues": item["issues"]})
        if not item["ok"]:
            issues += 1
    return {"checked": len(results), "mismatches": issues, "jobs": results}


def honest_write_alert_diagnosis(alert: Alert) -> dict[str, Any]:
    """Diagnosis for write/reconcile alerts: no market-story wording."""
    payload = alert.payload or {}
    if alert.alert_type == ALERT_RECONCILE:
        issues = payload.get("issues") or []
        narrative = "这是一条写入对账预警，不是经营指标异常。请核对该写入任务的拟写入内容、官方沙盒回执和策略状态，不要把它理解成转化或排名问题。"
        if issues:
            narrative += " 发现的问题：" + "；".join(
                (i.get("message") if isinstance(i, dict) else str(i)) for i in issues[:4]
            )
        return {
            "classification": "operational",
            "classification_label": "写入对账问题",
            "narrative": narrative,
            "primary_causes": [{"factor": "回执与账本或策略状态不一致"}],
            "not_causes": ["不是转化走弱", "不是搜索排名下跌"],
        }
    narrative = "这是一条官方写入失败/超时/部分成功预警，来自写入账本，不是经营诊断。本增量未向真实 OTA 发送请求。"
    return {
        "classification": "operational",
        "classification_label": "写入执行问题",
        "narrative": narrative,
        "primary_causes": [{"factor": "官方沙盒回执未全部成功"}],
        "not_causes": ["不是转化走弱", "不是搜索排名下跌"],
    }
