"""In-hotel decision memory (Phase IV increment 1).

Strictly scoped by hotel_id. Records facts already present on the run / strategy / write ledger,
plus a decision-time metric baseline for later observation. Does not invent
YoY, accuracy scores, or causal post-decision revenue. Cross-hotel identifiable
reads are not allowed; anonymized industry patterns are recorded separately.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Hotel, MemoryRecord, RoomType, Strategy, WriteJob

MEMORY_KIND = "decision_episode"
OUTCOME_NOT_MEASURED = "not_measured"

TERMINAL_STATUSES = frozenset(
    {
        "executed",
        "execution_partial",
        "execution_failed",
        "rejected",
        "rolled_back",
    }
)

ACTION_KIND_LABELS = {
    "pricing": "调价",
    "campaign": "活动",
    "review_reply": "评价回复",
}

STATUS_LABELS = {
    "executed": "已执行",
    "execution_partial": "部分执行",
    "execution_failed": "执行失败",
    "rejected": "已否决",
    "rolled_back": "已回滚",
}


def action_kind_of(state: dict) -> str:
    kind = (state.get("kind") or "").lower().strip()
    if kind in {"campaign", "campaign_advice"}:
        return "campaign"
    if kind in {"review_reply"}:
        return "review_reply"
    strat = state.get("strategy") or {}
    if isinstance(strat, dict):
        sk = (strat.get("kind") or "").lower().strip()
        if sk in {"campaign", "campaign_advice"}:
            return "campaign"
        if sk == "review_reply":
            return "review_reply"
    return "pricing"


def terminal_status_of(state: dict) -> str | None:
    status = (state.get("status") or "").lower().strip()
    if status in TERMINAL_STATUSES:
        return status
    approval = (state.get("approval_status") or "").lower().strip()
    if approval in TERMINAL_STATUSES:
        return approval
    return None


def should_persist_episode(state: dict) -> bool:
    """True only after a final disposition — never while parked for review."""
    if terminal_status_of(state) is None:
        return False
    status = (state.get("status") or "").lower().strip()
    approval = (state.get("approval_status") or "").lower().strip()
    if status in {"pending", "pending_review", "running"} or approval in {"pending"}:
        return False
    if status == "approved" and approval == "approved":
        # Approved but not yet executed is not a final episode.
        return False
    return True


def episode_key(hotel_id: int, run_id: int, strategy_id: int | None, review_id: int | None, terminal: str) -> str:
    return f"h{int(hotel_id)}:r{int(run_id)}:s{int(strategy_id or 0)}:v{int(review_id or 0)}:{terminal}"


def _proposed_action(state: dict, kind: str) -> dict[str, Any]:
    strat = state.get("strategy") or {}
    if not isinstance(strat, dict):
        strat = {}
    if kind == "campaign":
        advice = strat.get("campaign_advice") or {}
        return {
            "kind": "campaign",
            "campaign_name": advice.get("campaign_name") or "",
            "recommendation": advice.get("recommendation") or advice.get("recommendation_label") or "",
            "scope": advice.get("scope") or "",
        }
    if kind == "review_reply":
        return {
            "kind": "review_reply",
            "review_id": strat.get("review_id") or state.get("review_id"),
            "rating": strat.get("rating"),
            "has_draft": bool(strat.get("draft")),
        }
    return {
        "kind": "pricing",
        "room_type_id": strat.get("room_type_id") or state.get("room_type_id"),
        "room_name": strat.get("room_name") or "",
        "current_price": strat.get("current_price"),
        "recommended_price": strat.get("recommended_price"),
        "min_price": strat.get("min_price"),
        "max_price": strat.get("max_price"),
    }


def _room_name(db: Session, state: dict, proposed: dict) -> str:
    name = proposed.get("room_name") or (state.get("strategy") or {}).get("room_name") or ""
    if name:
        return str(name)
    room_id = proposed.get("room_type_id") or state.get("room_type_id")
    if room_id:
        room = db.get(RoomType, int(room_id))
        if room:
            return room.name
    return ""


def _execution_fact(state: dict) -> dict[str, Any]:
    result = state.get("execution_result") or {}
    if not isinstance(result, dict):
        result = {}
    fact = {
        "accepted": result.get("accepted"),
        "outcome": result.get("outcome"),
        "mode": result.get("mode"),
        "message": result.get("message") or "",
        "rolled_back": bool(result.get("rolled_back")),
        "restored_checkpoint_id": result.get("restored_checkpoint_id") or "",
    }
    return {k: v for k, v in fact.items() if v is not None and v != ""}


def _write_job_fact(db: Session, state: dict) -> dict[str, Any] | None:
    job_id = state.get("write_job_id")
    if not job_id:
        result = state.get("execution_result") or {}
        if isinstance(result, dict):
            job_id = result.get("write_job_id")
    if not job_id:
        return None
    job = db.get(WriteJob, int(job_id))
    if job is None:
        return {"id": int(job_id)}
    return {
        "id": job.id,
        "status": job.status,
        "write_mode": job.write_mode,
        "platform": job.platform,
        "refuse_reason": job.refuse_reason or "",
    }


def build_payload(db: Session, state: dict) -> dict[str, Any]:
    kind = action_kind_of(state)
    proposed = _proposed_action(state, kind)
    room_name = _room_name(db, state, proposed)
    if room_name and kind == "pricing":
        proposed["room_name"] = room_name
    terminal = terminal_status_of(state) or ""
    payload: dict[str, Any] = {
        "proposed_action": proposed,
        "risk_level": state.get("risk_level") or "",
        "actor": state.get("actor") or "",
        "approval_status": state.get("approval_status") or terminal,
        "status": terminal,
        "execution_result": _execution_fact(state) if kind or terminal != "rejected" else {},
        "write_job": _write_job_fact(db, state),
        "checkpoint_id": state.get("checkpoint_id") or "",
        "date_from": state.get("date_from"),
        "date_to": state.get("date_to"),
        "room_type_id": proposed.get("room_type_id") or state.get("room_type_id"),
        "room_name": room_name,
        "action_kind": kind,
        "review_comment": state.get("review_comment") or "",
        # Outcome stays not_measured until a later comparison against this baseline.
        "outcome": OUTCOME_NOT_MEASURED,
        "impact": None,
    }
    from app.services.impact import attach_decision_baseline

    attach_decision_baseline(db, state, payload)
    payload["outcome"] = OUTCOME_NOT_MEASURED
    if payload.get("impact") in (None, {}):
        payload["impact"] = None
    from app.services.industry_patterns import signal_types_of

    flags = signal_types_of(state, payload)
    if flags:
        payload["signal_types"] = flags
    if terminal == "rejected":
        # Refusals may have no execution result; keep an empty object rather than inventing one.
        payload["execution_result"] = _execution_fact(state)
    return payload


def episode_summary(payload: dict | None, *, action_kind: str = "", status: str = "") -> str:
    data = payload or {}
    kind = action_kind or data.get("action_kind") or ""
    terminal = status or data.get("status") or data.get("approval_status") or ""
    label = ACTION_KIND_LABELS.get(kind, "决策")
    status_label = STATUS_LABELS.get(terminal, terminal or "已处理")
    room = data.get("room_name") or ""
    proposed = data.get("proposed_action") or {}
    parts = [f"本店{status_label}"]
    if kind == "pricing":
        subject = f"「{room}」" if room else ""
        price = proposed.get("recommended_price")
        if price is not None:
            parts.append(f"{subject}调价建议（推荐约¥{round(float(price))}）".strip())
        else:
            parts.append(f"{subject}调价建议".strip() if subject else "调价建议")
    elif kind == "campaign":
        name = proposed.get("campaign_name") or "渠道活动"
        parts.append(f"活动建议「{name}」")
    elif kind == "review_reply":
        parts.append("评价回复草稿")
    else:
        parts.append(f"{label}建议")
    execution = data.get("execution_result") or {}
    mode = execution.get("mode") or ""
    if terminal == "executed" and mode:
        if "sandbox" in str(mode):
            parts.append("（沙盒回执，未向真实 OTA 发送请求）")
        elif str(mode) in {"refused", "refused_live", "live_refused"}:
            parts.append("（线上写入被拒绝）")
    elif terminal == "rejected":
        comment = data.get("review_comment") or ""
        if comment:
            parts.append(f"：{comment}")
    # Honest wording: observation versus baseline, never claimed causal revenue.
    outcome = data.get("outcome") or OUTCOME_NOT_MEASURED
    from app.services.impact import observation_title

    title = observation_title(terminal, kind)
    if terminal in {"executed", "execution_partial", "rejected", "rolled_back", "execution_failed"} and kind in {"pricing", "campaign"}:
        if outcome == "measured":
            parts.append(f"。{title}：入住窗口已实现，已对照决策时基线记录观察变化")
        elif outcome == "partially_measured":
            parts.append(f"。{title}：部分入住日已实现或已记录在手订单变化，对照决策时基线")
        else:
            parts.append(f"。{title}：尚未复盘，等待入住日实现或后续在手订单变化")
    text = "".join(parts)
    if not text.endswith("。"):
        text += "。"
    return text


def serialize_record(row: MemoryRecord) -> dict[str, Any]:
    payload = row.payload or {}
    status = row.approval_status or payload.get("status") or ""
    kind = row.action_kind or payload.get("action_kind") or ""
    return {
        "id": row.id,
        "hotel_id": row.hotel_id,
        "kind": row.kind,
        "run_id": row.run_id,
        "strategy_id": row.strategy_id,
        "checkpoint_id": row.checkpoint_id or payload.get("checkpoint_id") or "",
        "approval_status": status,
        "action_kind": kind,
        "action_kind_label": ACTION_KIND_LABELS.get(kind, kind or "决策"),
        "status_label": STATUS_LABELS.get(status, status or ""),
        "risk_level": row.risk_level or payload.get("risk_level") or "",
        "payload": payload,
        "summary": episode_summary(payload, action_kind=kind, status=status),
        "outcome": (payload or {}).get("outcome") or OUTCOME_NOT_MEASURED,
        "impact": (payload or {}).get("impact"),
        "room_name": payload.get("room_name") or "",
        "date_from": payload.get("date_from"),
        "date_to": payload.get("date_to"),
        "actor": payload.get("actor") or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def memory_to_strategy_context(record: dict) -> dict[str, Any]:
    """Honest context for the next run. No invented impact or YoY."""
    payload = record.get("payload") or {}
    return {
        "memory_id": record.get("id"),
        "strategy_id": record.get("strategy_id"),
        "run_id": record.get("run_id"),
        "action_kind": record.get("action_kind"),
        "approval_status": record.get("approval_status"),
        "risk_level": record.get("risk_level"),
        "room_name": record.get("room_name") or payload.get("room_name") or "",
        "date_from": record.get("date_from") or payload.get("date_from"),
        "date_to": record.get("date_to") or payload.get("date_to"),
        "summary": record.get("summary") or "",
        "outcome": record.get("outcome") or OUTCOME_NOT_MEASURED,
    }


def load_hotel_memories(db: Session, hotel_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """Load recent in-hotel episodes only. Never crosses hotel_id."""
    if not hotel_id:
        return []
    rows = (
        db.query(MemoryRecord)
        .filter(
            MemoryRecord.hotel_id == int(hotel_id),
            MemoryRecord.kind == MEMORY_KIND,
        )
        .order_by(MemoryRecord.id.desc())
        .limit(limit)
        .all()
    )
    return [serialize_record(row) for row in rows]


def attach_hotel_memories(state: dict, db: Session, *, limit: int = 20) -> dict:
    hotel_id = int(state.get("hotel_id") or 0)
    records = load_hotel_memories(db, hotel_id, limit=limit)
    state["historical_memories"] = records
    state["historical_strategies"] = [memory_to_strategy_context(r) for r in records]
    return state


def in_hotel_memory_facts(state: dict) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for row in state.get("historical_memories") or []:
        facts.append(
            {
                "action_kind": row.get("action_kind"),
                "approval_status": row.get("approval_status"),
                "risk_level": row.get("risk_level"),
                "room_name": row.get("room_name") or "",
                "date_from": row.get("date_from"),
                "date_to": row.get("date_to"),
                "summary": row.get("summary") or "",
                "outcome": row.get("outcome") or OUTCOME_NOT_MEASURED,
            }
        )
    return facts



def _record_anonymous_pattern(db: Session, row: MemoryRecord | None, state: dict) -> MemoryRecord | None:
    if row is None:
        return None
    from app.services.industry_patterns import record_episode_pattern

    record_episode_pattern(db, row, state)
    try:
        from app.services.knowledge_graph import upsert_episode_graph

        upsert_episode_graph(db, row)
    except Exception:
        pass
    return row


def persist_decision_episode(db: Session, state: dict) -> MemoryRecord | None:
    """Insert one episode for this run+strategy+terminal status. Idempotent."""
    if not should_persist_episode(state):
        return None
    hotel_id = int(state.get("hotel_id") or 0)
    run_id = int(state.get("run_id") or 0)
    if not hotel_id or not run_id:
        return None
    hotel = db.get(Hotel, hotel_id)
    if hotel is None:
        return None
    kind = action_kind_of(state)
    terminal = terminal_status_of(state) or ""
    strategy_id = state.get("strategy_id")
    if strategy_id is None:
        strategy_id = (state.get("strategy") or {}).get("id") if isinstance(state.get("strategy"), dict) else None
    review_id = state.get("review_id")
    if review_id is None and isinstance(state.get("strategy"), dict):
        review_id = (state.get("strategy") or {}).get("review_id")
    key = episode_key(hotel_id, run_id, strategy_id, review_id, terminal)

    existing = (
        db.query(MemoryRecord)
        .filter(MemoryRecord.hotel_id == hotel_id, MemoryRecord.episode_key == key)
        .one_or_none()
    )
    if existing is None and strategy_id is not None:
        existing = (
            db.query(MemoryRecord)
            .filter(
                MemoryRecord.hotel_id == hotel_id,
                MemoryRecord.kind == MEMORY_KIND,
                MemoryRecord.run_id == run_id,
                MemoryRecord.strategy_id == int(strategy_id),
                MemoryRecord.approval_status == terminal,
            )
            .one_or_none()
        )
    if existing is None and strategy_id is None:
        existing = (
            db.query(MemoryRecord)
            .filter(
                MemoryRecord.hotel_id == hotel_id,
                MemoryRecord.kind == MEMORY_KIND,
                MemoryRecord.run_id == run_id,
                MemoryRecord.approval_status == terminal,
            )
            .one_or_none()
        )
    if existing is not None:
        state["memory_id"] = existing.id
        state["memory_episode_key"] = existing.episode_key
        return _record_anonymous_pattern(db, existing, state)

    payload = build_payload(db, state)
    row = MemoryRecord(
        hotel_id=hotel_id,
        kind=MEMORY_KIND,
        run_id=run_id,
        strategy_id=int(strategy_id) if strategy_id is not None else None,
        checkpoint_id=str(state.get("checkpoint_id") or payload.get("checkpoint_id") or ""),
        approval_status=terminal,
        action_kind=kind,
        risk_level=str(state.get("risk_level") or ""),
        episode_key=key,
        payload=payload,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(MemoryRecord)
            .filter(MemoryRecord.hotel_id == hotel_id, MemoryRecord.episode_key == key)
            .one_or_none()
        )
        if existing is None:
            raise
        state["memory_id"] = existing.id
        state["memory_episode_key"] = existing.episode_key
        return _record_anonymous_pattern(db, existing, state)
    db.refresh(row)
    state["memory_id"] = row.id
    state["memory_episode_key"] = row.episode_key
    # Touch Strategy only if we already have the row — never invent fields on it.
    if row.strategy_id:
        strategy = db.get(Strategy, row.strategy_id)
        if strategy is not None and strategy.hotel_id != hotel_id:
            # Defensive: a memory row must never attach to another hotel's strategy.
            db.delete(row)
            db.commit()
            return None
    return _record_anonymous_pattern(db, row, state)
