"""In-hotel evolution engine (Phase IV increment 4).

Default OFF. When ON it uses this hotel's knowledge graph, offline learner
and memories to propose candidates and to evaluate them in an L5 sandbox
mode. It does not train a cross-hotel identifiable model, skip human
review, auto-execute, or change the hotel's write_mode to live.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import EvolutionCandidate, EvolutionEngineState, RoomType, Strategy, WorkflowRun
from app.services.risk_policy import load_execution_policy
from app.services.rl import ACTION_LABELS, fit_hotel_policy, serialize_policy
from app.services.workflow import HUMAN_REVIEW

ENGINE_OFF_NOTE = "进化引擎默认关闭。关闭时仍可查看本店经验、知识图谱与离线学习结果，但不能发明新策略或进入沙盒评估。"
ENGINE_ON_NOTE = (
    "进化引擎已接通，但只在本店经验上做离线学习与建议，不会训练跨店可识别模型，"
    "不会跳过人审，不会自动改价，也不会改为线上写入。"
)
SANDBOX_NOTE = (
    "沙盒模式只用于评估本店候选策略，不会把酒店的写入模式改为线上，也不会打开自动执行，更不会调用线上官方接口。"
)
MIGRATE_DISABLED_NOTE = "一键迁移不开放，以免带出其他酒店身份。跨店学习只提供匿名模式引用，不会复制其他酒店的在用策略。"


def _default_state(hotel_id: int) -> dict[str, Any]:
    return {
        "hotel_id": int(hotel_id),
        "enabled": False,
        "sandbox_mode": False,
        "updated_by": "",
        "updated_at": None,
    }


def load_engine(db: Session, hotel_id: int) -> EvolutionEngineState | None:
    return (
        db.query(EvolutionEngineState)
        .filter(EvolutionEngineState.hotel_id == int(hotel_id))
        .one_or_none()
    )


def serialize_engine(db: Session, hotel_id: int) -> dict[str, Any]:
    row = load_engine(db, hotel_id)
    policy = load_execution_policy(db, hotel_id=hotel_id)
    payload = _default_state(hotel_id)
    if row is not None:
        payload.update(
            {
                "enabled": bool(row.enabled),
                "sandbox_mode": bool(row.sandbox_mode),
                "updated_by": row.updated_by or "",
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    payload["note"] = ENGINE_ON_NOTE if payload["enabled"] else ENGINE_OFF_NOTE
    payload["write_mode"] = policy.write_mode
    payload["auto_execute_enabled"] = bool(policy.auto_execute_enabled)
    payload["one_click_migrate_enabled"] = False
    payload["one_click_migrate_note"] = MIGRATE_DISABLED_NOTE
    payload["sandbox_note"] = SANDBOX_NOTE
    return payload


def _ensure_row(db: Session, hotel_id: int) -> EvolutionEngineState:
    row = load_engine(db, hotel_id)
    if row is None:
        row = EvolutionEngineState(
            hotel_id=int(hotel_id),
            enabled=False,
            sandbox_mode=False,
        )
        db.add(row)
        db.flush()
    return row


def set_engine_enabled(db: Session, hotel_id: int, enabled: bool, *, actor: str) -> dict[str, Any]:
    row = _ensure_row(db, hotel_id)
    row.enabled = bool(enabled)
    if not row.enabled:
        row.sandbox_mode = False
    row.updated_by = actor or ""
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return serialize_engine(db, hotel_id)


def set_sandbox_mode(db: Session, hotel_id: int, active: bool, *, actor: str) -> dict[str, Any]:
    row = _ensure_row(db, hotel_id)
    if not row.enabled and active:
        raise ValueError("进化引擎关闭时不能进入沙盒模式。")
    row.sandbox_mode = bool(active)
    row.updated_by = actor or ""
    row.updated_at = datetime.utcnow()
    db.commit()
    # Sandbox mode is an L5 evaluation flag only — never touch write_mode or auto_execute.
    if row.sandbox_mode:
        evaluate_candidates(db, hotel_id)
    return serialize_engine(db, hotel_id)


def serialize_candidate(row: EvolutionCandidate) -> dict[str, Any]:
    return {
        "id": row.id,
        "hotel_id": row.hotel_id,
        "action_bucket": row.action_bucket,
        "action_label": row.action_label or ACTION_LABELS.get(row.action_bucket, row.action_bucket),
        "context_key": row.context_key,
        "context": row.context or {},
        "title": row.title,
        "rationale": row.rationale,
        "episode_count": row.episode_count,
        "mean_observed_reward": row.mean_reward,
        "reward_unit": row.reward_unit,
        "status": row.status,
        "strategy_id": row.strategy_id,
        "sandbox_evaluation": row.sandbox_evaluation,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "auto_executed": False,
    }


def list_candidates(db: Session, hotel_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(EvolutionCandidate)
        .filter(EvolutionCandidate.hotel_id == int(hotel_id))
        .order_by(EvolutionCandidate.id.desc())
        .all()
    )
    return [serialize_candidate(row) for row in rows]


def _room_for_hotel(db: Session, hotel_id: int, room_type_id: int | None = None) -> RoomType | None:
    if room_type_id:
        room = db.get(RoomType, int(room_type_id))
        if room is not None and room.hotel_id == int(hotel_id):
            return room
    return (
        db.query(RoomType)
        .filter(RoomType.hotel_id == int(hotel_id))
        .order_by(RoomType.id.asc())
        .first()
    )


def _clip(value: float, lo: float | None, hi: float | None) -> float:
    out = float(value)
    if lo is not None:
        out = max(out, float(lo))
    if hi is not None:
        out = min(out, float(hi))
    return round(out, 1)


def _recommended_price(room: RoomType, action: str) -> tuple[float, float]:
    current = float(room.base_price or 0)
    lo = float(room.min_price) if room.min_price is not None else None
    hi = float(room.max_price) if room.max_price is not None else None
    if action == "raise":
        rec = _clip(current * 1.05, lo, hi)
    elif action == "cut":
        rec = _clip(current * 0.95, lo, hi)
    else:
        rec = _clip(current, lo, hi)
    return current, rec


def _park_strategy(
    db: Session,
    hotel_id: int,
    actor: str,
    *,
    room: RoomType,
    action: str,
    rationale: str,
    context: dict[str, Any],
    candidate_id: int,
) -> Strategy:
    """Create a pending_review pricing strategy. Never auto-executes, never sets live writes."""
    today = date.today()
    date_from = today
    date_to = today + timedelta(days=13)
    current, recommended = _recommended_price(room, action)
    run = WorkflowRun(
        hotel_id=int(hotel_id),
        kind="pricing_strategy",
        status="pending_review",
        current_node=HUMAN_REVIEW,
        payload={},
        created_by=actor,
    )
    db.add(run)
    db.flush()
    constraints = {
        "source": "evolution_engine",
        "candidate_id": candidate_id,
        "action_bucket": action,
        "auto_execute_suppressed": True,
    }
    row = Strategy(
        hotel_id=int(hotel_id),
        workflow_run_id=run.id,
        kind="pricing",
        room_type_id=room.id,
        date_from=date_from,
        date_to=date_to,
        current_price=current,
        min_price=room.min_price,
        max_price=room.max_price,
        recommended_price=recommended,
        confidence=0.0,
        rationale=rationale,
        expected_impact={
            "note": "来自本店已复盘记录的观察奖励对照，不是预测，也不是承诺兑现。",
        },
        constraints=constraints,
        status="pending_review",
        risk_level="medium",
        previous_state={"list_snapshot": current},
    )
    db.add(row)
    db.flush()
    state = {
        "hotel_id": int(hotel_id),
        "run_id": run.id,
        "strategy_id": row.id,
        "kind": "pricing",
        "status": "pending_review",
        "approval_status": "pending",
        "current_node": HUMAN_REVIEW,
        "path": [HUMAN_REVIEW],
        "actor": actor,
        "risk_level": "medium",
        "source": "evolution_engine",
        "auto_executed": False,
        "auto_execute_suppressed": True,
        "candidate_id": candidate_id,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "room_type_id": room.id,
        "strategy": {
            "kind": "pricing",
            "room_type_id": room.id,
            "room_name": room.name,
            "current_price": current,
            "recommended_price": recommended,
            "rationale": rationale,
        },
        "rl_context": context,
    }
    run.related_strategy_id = row.id
    run.payload = state
    # Intentionally do not call maybe_auto_execute.
    db.commit()
    db.refresh(row)
    return row


def invent_strategies(db: Session, hotel_id: int, *, actor: str) -> dict[str, Any]:
    engine = serialize_engine(db, hotel_id)
    if not engine["enabled"]:
        raise ValueError("进化引擎关闭时不能发明新策略。")
    policy = serialize_policy(db, hotel_id)
    if policy.get("insufficient") or not policy.get("suggestions"):
        return {
            "created": 0,
            "insufficient": True,
            "insufficient_reason": policy.get("insufficient_reason")
            or "本店观察样本不足，不会编造策略。",
            "candidates": list_candidates(db, hotel_id),
            "auto_executed": False,
            "write_mode": load_execution_policy(db, hotel_id=hotel_id).write_mode,
        }

    created: list[EvolutionCandidate] = []
    room = _room_for_hotel(db, hotel_id)
    for item in policy["suggestions"]:
        action = item["action_bucket"]
        context_key = item["context_key"]
        existing = (
            db.query(EvolutionCandidate)
            .filter(
                EvolutionCandidate.hotel_id == int(hotel_id),
                EvolutionCandidate.context_key == context_key,
                EvolutionCandidate.action_bucket == action,
                EvolutionCandidate.status.in_(("proposed", "submitted_review")),
            )
            .first()
        )
        if existing is not None:
            continue
        title = f"建议：{item['action_label']}"
        rationale = (
            f"根据本店 {item['episode_count']} 条已复盘记录，在类似信号下「{item['action_label']}」"
            f"的观察奖励均值为 {item['mean_observed_reward']} {_unit_short(item.get('reward_unit'))}。"
            "该数字是相对决策时基线的观察变化，不是因果收入。此建议进入人审队列，不会自动执行，也不会改为线上写入。"
        )
        candidate = EvolutionCandidate(
            hotel_id=int(hotel_id),
            action_bucket=action,
            action_label=item["action_label"],
            context_key=context_key,
            context=item.get("context") or {},
            title=title,
            rationale=rationale,
            episode_count=item["episode_count"],
            mean_reward=item["mean_observed_reward"],
            reward_unit=item.get("reward_unit") or "",
            status="proposed",
        )
        db.add(candidate)
        db.flush()
        if action in {"raise", "cut", "hold"} and room is not None:
            strategy = _park_strategy(
                db,
                hotel_id,
                actor,
                room=room,
                action=action,
                rationale=rationale,
                context=item.get("context") or {},
                candidate_id=candidate.id,
            )
            candidate.strategy_id = strategy.id
            candidate.status = "submitted_review"
        created.append(candidate)
    db.commit()
    return {
        "created": len(created),
        "insufficient": False,
        "insufficient_reason": "",
        "candidates": [serialize_candidate(row) for row in created] or list_candidates(db, hotel_id),
        "auto_executed": False,
        "write_mode": load_execution_policy(db, hotel_id=hotel_id).write_mode,
        "note": ENGINE_ON_NOTE,
    }


def _unit_short(unit: str | None) -> str:
    if unit == "revenue_yuan":
        return "元"
    if unit == "sold_rooms":
        return "间夜"
    return unit or ""


def evaluate_candidates(db: Session, hotel_id: int) -> list[dict[str, Any]]:
    """Pure counterfactual preview against stored observed rewards. No live writes."""
    policy = serialize_policy(db, hotel_id)
    values = policy.get("action_values") or []
    rows = (
        db.query(EvolutionCandidate)
        .filter(EvolutionCandidate.hotel_id == int(hotel_id))
        .order_by(EvolutionCandidate.id.asc())
        .all()
    )
    for row in rows:
        matching = [
            item
            for item in values
            if item.get("context_key") == row.context_key and item.get("action_bucket") == row.action_bucket
        ]
        peers = [item for item in values if item.get("context_key") == row.context_key]
        evaluation = {
            "mode": "sandbox_counterfactual",
            "live_write": False,
            "write_mode_unchanged": True,
            "auto_execute_unchanged": True,
            "observed": True,
            "causal": False,
            "label": "用本店已复盘记录做对照预览，不是线上写入，也不是因果收入。",
            "matching_episodes": matching[0]["episode_count"] if matching else 0,
            "mean_observed_reward": matching[0]["mean_observed_reward"] if matching else row.mean_reward,
            "reward_unit": (matching[0]["reward_unit"] if matching else row.reward_unit),
            "peer_actions": peers,
        }
        row.sandbox_evaluation = evaluation
        if row.status == "proposed":
            row.status = "sandbox_evaluated"
    db.commit()
    return list_candidates(db, hotel_id)


def overview(db: Session, hotel_id: int) -> dict[str, Any]:
    from app.services.knowledge_graph import serialize_graph

    engine = serialize_engine(db, hotel_id)
    graph = serialize_graph(db, hotel_id)
    # Avoid leaking a raw node dump twice in the overview; keep counts + summary.
    graph_brief = {
        "summary": graph.get("summary"),
        "note": graph.get("note"),
        "node_count": len(graph.get("nodes") or []),
        "edge_count": len(graph.get("edges") or []),
    }
    return {
        "engine": engine,
        "graph": graph_brief,
        "rl": serialize_policy(db, hotel_id),
        "candidates": list_candidates(db, hotel_id),
        "note": engine["note"],
    }


def assert_defaults_intact(db: Session, hotel_id: int) -> dict[str, Any]:
    """Helper for tests and API responses: engine actions never flip hotel write policy."""
    policy = load_execution_policy(db, hotel_id=hotel_id)
    return {
        "auto_execute_enabled": bool(policy.auto_execute_enabled),
        "write_mode": policy.write_mode,
    }
