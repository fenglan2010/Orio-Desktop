"""Decision workflow: LangGraph when available, equivalent state machine otherwise.

Node path (fixed; the model cannot choose the next hop):
  data_node → signal_node → analysis_node → strategy_node → human_review → execution_node → memory_node

memory_node persists an in-hotel decision episode after a terminal disposition
(approved execution or a final rejection). It does not skip human_review,
does not invent impact metrics, and never reads another hotel's identifiable rows.
Anonymized peer patterns may be attached as a separate state field and cited as context only.
Risk level is recorded on every strategy. By default it never skips human_review.
When the hotel enables auto-execute, only low-risk actions that also pass the
execution policy may continue to execution_node without waiting for a person.
Rollback restores the pre-execution checkpoint and is bound to checkpoint_id.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, TypedDict

from sqlalchemy.orm import Session

from app.adapters.ota.ctrip_mock import CtripMockAdapter
from app.gateway.protocol import LLMRequest
from app.gateway.router import get_gateway
from app.models import (
    Alert,
    AuditLog,
    DecisionCheckpoint,
    Hotel,
    Review,
    RoomType,
    Strategy,
    WorkflowNodeLog,
    WorkflowRun,
)
from app.services.diagnosis import collect_signals, heuristic_diagnosis
from app.services.campaigns import assess_campaign_risk, compute_campaign_advice
from app.services.pricing import compute_room_pricing

# Canonical node names required by the product docs.
DATA_NODE = "data_node"
SIGNAL_NODE = "signal_node"
ANALYSIS_NODE = "analysis_node"
STRATEGY_NODE = "strategy_node"
HUMAN_REVIEW = "human_review"
EXECUTION_NODE = "execution_node"
MEMORY_NODE = "memory_node"
ROLLBACK_NODE = "rollback"  # recorded when a rollback is applied

PRE_REVIEW_NODES = (DATA_NODE, SIGNAL_NODE, ANALYSIS_NODE, STRATEGY_NODE)

from app.services.trace import node_output_payload

USE_LANGGRAPH = False
_LG_GRAPH = None
_LG_REASON = "not_initialized"


def _try_langgraph():
    global USE_LANGGRAPH, _LG_GRAPH, _LG_REASON
    try:
        from langgraph.graph import END, START, StateGraph

        from app.services.checkpointer import open_checkpointer
    except Exception as exc:  # noqa: BLE001
        USE_LANGGRAPH = False
        _LG_REASON = f"langgraph_unavailable: {type(exc).__name__}: {exc}"
        return None

    builder = StateGraph(dict)
    builder.add_node(DATA_NODE, _lg_data)
    builder.add_node(SIGNAL_NODE, _lg_signal)
    builder.add_node(ANALYSIS_NODE, _lg_analysis)
    builder.add_node(STRATEGY_NODE, _lg_strategy)
    builder.add_node(HUMAN_REVIEW, _lg_human_review)
    builder.add_node(EXECUTION_NODE, _lg_execution)
    builder.add_node(MEMORY_NODE, _lg_memory)

    builder.add_edge(START, DATA_NODE)
    builder.add_edge(DATA_NODE, SIGNAL_NODE)
    builder.add_edge(SIGNAL_NODE, ANALYSIS_NODE)
    builder.add_edge(ANALYSIS_NODE, STRATEGY_NODE)
    builder.add_edge(STRATEGY_NODE, HUMAN_REVIEW)
    # The graph still parks at human_review. Optional low-risk auto-execute is
    # applied after persist, based on the hotel execution policy, not a model hop.
    builder.add_conditional_edges(
        HUMAN_REVIEW,
        _lg_after_review,
        {"execute": EXECUTION_NODE, "remember": MEMORY_NODE, "stop": END},
    )
    builder.add_edge(EXECUTION_NODE, MEMORY_NODE)
    builder.add_edge(MEMORY_NODE, END)

    try:
        checkpointer = open_checkpointer()
    except Exception as exc:  # noqa: BLE001
        USE_LANGGRAPH = False
        _LG_GRAPH = None
        _LG_REASON = f"checkpointer_unavailable: {type(exc).__name__}: {exc}"
        return None

    _LG_GRAPH = builder.compile(checkpointer=checkpointer, interrupt_before=[HUMAN_REVIEW])
    USE_LANGGRAPH = True
    _LG_REASON = "langgraph"
    return _LG_GRAPH


# --- LangGraph thin wrappers (real work lives in node functions below) ---

def _lg_data(state: dict) -> dict:
    return _run_named_node(DATA_NODE, state)


def _lg_signal(state: dict) -> dict:
    return _run_named_node(SIGNAL_NODE, state)


def _lg_analysis(state: dict) -> dict:
    return _run_named_node(ANALYSIS_NODE, state)


def _lg_strategy(state: dict) -> dict:
    return _run_named_node(STRATEGY_NODE, state)


def _lg_human_review(state: dict) -> dict:
    return _run_named_node(HUMAN_REVIEW, state)


def _lg_execution(state: dict) -> dict:
    return _run_named_node(EXECUTION_NODE, state)


def _lg_memory(state: dict) -> dict:
    return _run_named_node(MEMORY_NODE, state)


def _lg_after_review(state: dict) -> str:
    approval = (state.get("approval_status") or "").lower()
    status = (state.get("status") or "").lower()
    if approval == "approved" or status == "approved":
        return "execute"
    from app.services.memory import should_persist_episode

    if should_persist_episode(state) or approval in {"rejected", "rolled_back"} or status in {"rejected", "rolled_back"}:
        return "remember"
    return "stop"


def engine_info(db=None, hotel_id: int | None = None) -> dict:
    if _LG_GRAPH is None and _LG_REASON == "not_initialized":
        _try_langgraph()
    from app.services.risk_policy import load_execution_policy

    policy = load_execution_policy(db, hotel_id=hotel_id)
    from app.services.checkpointer import checkpointer_meta

    meta = checkpointer_meta()
    return {
        "engine": "langgraph" if USE_LANGGRAPH else "equivalent_state_machine",
        "reason": _LG_REASON,
        "checkpointer": meta.get("kind") or "none",
        "checkpointer_durable": bool(meta.get("durable")),
        "nodes": list(PRE_REVIEW_NODES) + [HUMAN_REVIEW, EXECUTION_NODE, MEMORY_NODE],
        "reserved_nodes": [],
        "auto_execute_on_low_risk": bool(policy.auto_execute_enabled),
        "execution_policy": policy.to_public_dict(),
    }


class OIROState(TypedDict, total=False):
    hotel_id: int
    hotel_profile: dict
    ota_snapshot: dict
    competitor_data: list
    signals: dict
    analysis_result: dict
    strategy: dict
    risk_level: str
    execution_result: dict
    approval_status: str
    checkpoint_id: str
    historical_strategies: list
    historical_memories: list
    peer_patterns: list
    industry_patterns: list
    run_id: int
    kind: str
    room_type_id: int | None
    review_id: int | None
    date_from: str | None
    date_to: str | None
    current_node: str
    status: str
    error: str | None
    degraded: bool
    actor: str
    review_comment: str
    path: list


# Context for node functions that need a DB session. Set around graph.invoke.
_CTX: dict[str, Any] = {}


def _db() -> Session:
    return _CTX["db"]


def _ota() -> CtripMockAdapter:
    return _CTX.get("ota") or CtripMockAdapter()


def _today() -> date:
    return _CTX.get("today") or date.today()


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _save_checkpoint(state: dict, node_name: str, kind: str = "node") -> str:
    cid = str(uuid.uuid4())
    row = DecisionCheckpoint(
        checkpoint_id=cid,
        run_id=int(state.get("run_id") or 0),
        hotel_id=int(state.get("hotel_id") or 0),
        node_name=node_name,
        kind=kind,
        state=_json_safe({k: v for k, v in state.items() if k != "checkpoint_id"}),
    )
    db = _db()
    db.add(row)
    db.commit()
    state["checkpoint_id"] = cid
    return cid


def _log_node(state: dict, node_name: str, status: str, input_data: dict, output_data: dict, error: str | None = None) -> None:
    db = _db()
    db.add(
        WorkflowNodeLog(
            run_id=int(state.get("run_id") or 0),
            node_name=node_name,
            status=status,
            input_json=_json_safe(input_data),
            output_json=_json_safe(output_data),
            error=error,
            finished_at=datetime.utcnow(),
        )
    )
    run = db.get(WorkflowRun, int(state.get("run_id") or 0))
    if run:
        run.current_node = node_name
        run.payload = _json_safe(state)
        run.updated_at = datetime.utcnow()
        if error:
            run.error = error
    db.commit()


def _run_named_node(name: str, state: dict) -> dict:
    fn = {
        DATA_NODE: data_node,
        SIGNAL_NODE: signal_node,
        ANALYSIS_NODE: analysis_node,
        STRATEGY_NODE: strategy_node,
        HUMAN_REVIEW: human_review_node,
        EXECUTION_NODE: execution_node,
        MEMORY_NODE: memory_node,
    }[name]
    before = {
        "current_node": state.get("current_node"),
        "approval_status": state.get("approval_status"),
        "risk_level": state.get("risk_level"),
    }
    try:
        updated = fn(dict(state))
        updated["current_node"] = name
        path = list(updated.get("path") or state.get("path") or [])
        if name not in path:
            path.append(name)
        updated["path"] = path
        _save_checkpoint(updated, name, kind="pre_execution" if name == STRATEGY_NODE else "node")
        _log_node(updated, name, "ok", before, node_output_payload(name, updated))
        return updated
    except Exception as exc:  # noqa: BLE001
        state["error"] = f"{name}: {exc}"
        state["status"] = "failed"
        _log_node(state, name, "error", before, {}, error=str(exc))
        raise


def data_node(state: dict) -> dict:
    """Collect hotel profile, OTA snapshot and competitor data."""
    db = _db()
    hotel_id = int(state["hotel_id"])
    hotel = db.get(Hotel, hotel_id)
    rooms = db.query(RoomType).filter(RoomType.hotel_id == hotel_id).order_by(RoomType.id).all()
    today = _today()
    start = today - timedelta(days=14)
    end = today + timedelta(days=13)
    ota = _ota()
    metrics = ota.fetch_metrics(db, hotel_id, start, end)
    comps = ota.fetch_competitors(db, hotel_id, start, end)
    reviews = ota.fetch_reviews(db, hotel_id)

    def _metric(m):
        return {
            "room_type_id": m.room_type_id,
            "date": m.date.isoformat(),
            "is_future": m.is_future,
            "available_rooms": m.available_rooms,
            "sold_rooms": m.sold_rooms,
            "occupancy": m.occupancy,
            "list_price": m.list_price,
            "adr": m.adr,
            "revenue": m.revenue,
            "search_rank": m.search_rank,
            "impressions": m.impressions,
            "clicks": m.clicks,
            "bookings": m.bookings,
            "conversion_rate": m.conversion_rate,
        }

    state["hotel_profile"] = {
        "hotel_id": hotel.id,
        "name": hotel.name,
        "city": hotel.city,
        "address": hotel.address,
        "positioning": hotel.positioning,
        "platform": hotel.platform,
        "rooms": [
            {
                "id": r.id,
                "name": r.name,
                "code": r.code,
                "inventory": r.inventory,
                "capacity": r.capacity,
                "base_price": r.base_price,
                "min_price": r.min_price,
                "max_price": r.max_price,
            }
            for r in rooms
        ],
    }
    state["ota_snapshot"] = {
        "platform": ota.platform,
        "mode": "simulated",
        "as_of": today.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "metrics": [_metric(m) for m in metrics],
        "reviews": [
            {
                "id": r.id,
                "date": r.review_date.isoformat(),
                "rating": r.rating,
                "guest_name": r.guest_name,
                "content": r.content,
                "sentiment": r.sentiment,
            }
            for r in reviews
        ],
    }
    state["competitor_data"] = [
        {
            "room_type_id": c.room_type_id,
            "date": c.date.isoformat(),
            "competitor_name": c.competitor_name,
            "price": c.price,
            "rank": c.rank,
        }
        for c in comps
    ]
    # Keep raw ORM lists only in context for later heuristic nodes.
    _CTX["rooms"] = rooms
    _CTX["metrics"] = metrics
    _CTX["competitors"] = comps
    _CTX["reviews"] = reviews
    state["status"] = "running"
    return state


def signal_node(state: dict) -> dict:
    db = _db()
    hotel_id = int(state["hotel_id"])
    rooms = _CTX.get("rooms") or db.query(RoomType).filter(RoomType.hotel_id == hotel_id).all()
    metrics = _CTX.get("metrics") or []
    competitors = _CTX.get("competitors") or []
    reviews = _CTX.get("reviews") or []
    alerts = db.query(Alert).filter(Alert.hotel_id == hotel_id).all()
    signals = collect_signals(_today(), rooms, metrics, competitors, reviews, alerts)
    state["signals"] = signals
    return state


def analysis_node(state: dict) -> dict:
    diagnosis = heuristic_diagnosis(state.get("signals") or {})
    facts = {
        "hotel_name": (state.get("hotel_profile") or {}).get("name"),
        "rank_trend": (state.get("signals") or {}).get("rank_trend"),
        "focus_room": (state.get("signals") or {}).get("focus_room"),
        "competitor_cut_pct": (state.get("signals") or {}).get("competitor_cut_pct"),
        "conversion_from": (state.get("signals") or {}).get("conversion_from"),
        "conversion_to": (state.get("signals") or {}).get("conversion_to"),
        "negative_review": (state.get("signals") or {}).get("negative_review"),
        "classification": diagnosis.get("classification"),
        "price_inversions": (state.get("signals") or {}).get("price_inversions"),
    }
    from app.services.memory import in_hotel_memory_facts

    memory_facts = in_hotel_memory_facts(state)
    if memory_facts:
        facts["in_hotel_recent_decisions"] = memory_facts
    from app.services.industry_patterns import peer_pattern_facts

    patterns = peer_pattern_facts(state)
    if patterns:
        facts["anonymous_peer_patterns"] = patterns
    gateway = get_gateway()
    resp = gateway.complete(
        LLMRequest(
            task_type="analysis",
            hotel_id=state.get("hotel_id"),
            messages=[
                {
                    "role": "system",
                    "content": "你是酒店 OTA 收益分析官。根据事实做因果拆解，区分短期波动与结构性问题。不要编造数据。",
                },
                {
                    "role": "user",
                    "content": "请解释当前经营异常的原因。\nFACTS_JSON: " + json.dumps(facts, ensure_ascii=False),
                },
            ],
        ),
        db=_db(),
    )
    diagnosis["narrative"] = resp.content
    if resp.structured:
        diagnosis["llm"] = resp.structured
    diagnosis["degraded"] = resp.degraded
    diagnosis["degrade_notice"] = resp.degrade_notice
    if memory_facts:
        diagnosis["in_hotel_memory_note"] = (
            "已引用本店近期已执行或已否决的决策作为上下文；这不是跨店学习，"
            "也不会据此跳过人审或自动改价。"
        )
    if patterns:
        diagnosis["peer_pattern_note"] = (
            "已引用匿名行业模式：同类酒店在类似信号下曾执行或否决类似动作。"
            "跨店学习为匿名模式，不展示其他酒店名称，不会训练模型，不会自动改价，也不会据此跳过人审。"
        )
    state["analysis_result"] = diagnosis
    state["degraded"] = bool(resp.degraded)
    return state


def _assess_risk(kind: str, pricing: dict | None, signals: dict, strategy_payload: dict | None = None) -> str:
    """Classify low/medium/high. Auto-execute is decided later by risk_policy."""
    flags = set((signals or {}).get("flags") or [])
    change = abs(((pricing or {}).get("expected_impact") or {}).get("price_change_pct") or 0)
    if kind == "review_reply":
        rating = (strategy_payload or {}).get("rating") or 5
        if rating <= 2 or "negative_review" in flags:
            return "high"
        return "medium"
    if change >= 15 or ("price_inversion" in flags and "rank_drop" in flags):
        return "high"
    if change >= 6 or "rank_drop" in flags or "price_inversion" in flags:
        return "medium"
    return "low"


def strategy_node(state: dict) -> dict:
    db = _db()
    kind = state.get("kind") or "pricing_strategy"
    rooms: list[RoomType] = _CTX.get("rooms") or []
    metrics = _CTX.get("metrics") or []
    competitors = _CTX.get("competitors") or []
    today = _today()
    date_from = date.fromisoformat(state["date_from"]) if state.get("date_from") else today
    date_to = date.fromisoformat(state["date_to"]) if state.get("date_to") else today + timedelta(days=13)

    if kind == "review_reply":
        review = db.get(Review, int(state["review_id"]))
        from app.services.memory import in_hotel_memory_facts

        facts = {
            "rating": review.rating,
            "guest_name": review.guest_name,
            "content": review.content,
            "title": review.title,
        }
        memory_facts = in_hotel_memory_facts(state)
        if memory_facts:
            facts["in_hotel_recent_decisions"] = memory_facts
        from app.services.industry_patterns import peer_pattern_facts

        patterns = peer_pattern_facts(state)
        if patterns:
            facts["anonymous_peer_patterns"] = patterns
        resp = get_gateway().complete(
            LLMRequest(
                task_type="review_reply",
                hotel_id=state.get("hotel_id"),
                execution_critical=True,
                messages=[
                    {
                        "role": "system",
                        "content": "你是酒店品质经理，撰写可对外发布的评价回复草稿。语气克制、诚恳，不推诿，不编造承诺。",
                    },
                    {
                        "role": "user",
                        "content": "请为这条评价写回复草稿。\nFACTS_JSON: " + json.dumps(facts, ensure_ascii=False),
                    },
                ],
            ),
            db=db,
        )
        draft = (resp.structured or {}).get("draft") or resp.content
        payload = {
            "kind": "review_reply",
            "review_id": review.id,
            "rating": review.rating,
            "draft": draft,
            "rationale": "差评需先安抚并承诺核查，回复须人审后才能记为已执行。",
            "degraded": resp.degraded,
            "degrade_notice": resp.degrade_notice,
        }
        risk = _assess_risk("review_reply", None, state.get("signals") or {}, payload)
        state["strategy"] = payload
        state["risk_level"] = risk
        state["approval_status"] = "pending"
        state["status"] = "pending_review"
        review.reply_draft = draft
        review.reply_status = "pending_review"
        review.workflow_run_id = state.get("run_id")
        db.commit()
        return state

    if kind == "campaign":
        pricing_results = []
        for room in rooms:
            pricing_results.append(compute_room_pricing(room, date_from, date_to, metrics, competitors))
        advice = compute_campaign_advice(
            rooms,
            pricing_results,
            signals=state.get("signals") or {},
            date_from=date_from,
            date_to=date_to,
        )
        from app.services.memory import in_hotel_memory_facts

        facts = {
            "kind": "campaign",
            "campaign_advice": advice,
            "rooms": [p["room_name"] for p in pricing_results],
        }
        memory_facts = in_hotel_memory_facts(state)
        if memory_facts:
            facts["in_hotel_recent_decisions"] = memory_facts
        from app.services.industry_patterns import peer_pattern_facts

        patterns = peer_pattern_facts(state)
        if patterns:
            facts["anonymous_peer_patterns"] = patterns
        resp = get_gateway().complete(
            LLMRequest(
                task_type="pricing_strategy",
                hotel_id=state.get("hotel_id"),
                execution_critical=True,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是酒店收益经理。根据已计算的活动建议撰写说明，"
                            "不要改动 recommendation、options 中是否参加的结论，不要编造数字。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": "请撰写活动参与建议说明。\nFACTS_JSON: " + json.dumps(facts, ensure_ascii=False),
                    },
                ],
            ),
            db=db,
        )
        advice["rationale"] = resp.content
        advice["degraded"] = resp.degraded
        state["strategy"] = {
            "kind": "campaign",
            "campaign_advice": advice,
            "rationale": resp.content,
            "confidence": advice.get("confidence") or 0,
            "expected_impact": advice.get("expected_impact"),
        }
        state["risk_level"] = assess_campaign_risk(advice, state.get("signals") or {})
        state["approval_status"] = "pending"
        state["status"] = "pending_review"
        state["degraded"] = bool(resp.degraded)
        return state

    # pricing
    room_id = state.get("room_type_id")
    room = next((r for r in rooms if r.id == room_id), rooms[0] if rooms else None)
    if room is None:
        raise RuntimeError("no room type")
    pricing = compute_room_pricing(room, date_from, date_to, metrics, competitors)
    facts = {
        "room_name": pricing["room_name"],
        "current_price": pricing["current_price"],
        "recommended_price": pricing["recommended_price"],
        "min_price": pricing["min_price"],
        "max_price": pricing["max_price"],
        "confidence": pricing["confidence"],
        "expected_impact": pricing["expected_impact"],
        "constraints": pricing["constraints"],
        "signals": pricing["signals"],
    }
    from app.services.memory import in_hotel_memory_facts

    memory_facts = in_hotel_memory_facts(state)
    if memory_facts:
        facts["in_hotel_recent_decisions"] = memory_facts
    from app.services.industry_patterns import peer_pattern_facts

    patterns = peer_pattern_facts(state)
    if patterns:
        facts["anonymous_peer_patterns"] = patterns
    resp = get_gateway().complete(
        LLMRequest(
            task_type="pricing_strategy",
            hotel_id=state.get("hotel_id"),
            execution_critical=True,
            messages=[
                {
                    "role": "system",
                    "content": "你是酒店收益经理。价格区间已由启发式模型算出，你只需解释理由与风险，不得给出无法复核的单一执行价，不得改动数字。",
                },
                {
                    "role": "user",
                    "content": "请为以下定价建议撰写人审说明。\nFACTS_JSON: " + json.dumps(facts, ensure_ascii=False),
                },
            ],
        ),
        db=db,
    )
    pricing["rationale"] = resp.content
    pricing["degraded"] = resp.degraded
    pricing["degrade_notice"] = resp.degrade_notice
    pricing["kind"] = "pricing"
    risk = _assess_risk("pricing", pricing, state.get("signals") or {})
    state["strategy"] = pricing
    state["risk_level"] = risk
    state["approval_status"] = "pending"
    state["status"] = "pending_review"
    state["degraded"] = bool(resp.degraded)
    return state


def human_review_node(state: dict) -> dict:
    """Apply an approval already placed on the state. Does not invent a decision."""
    decision = (state.get("approval_status") or "pending").lower()
    if decision in {"approve", "approved"}:
        state["approval_status"] = "approved"
        state["status"] = "approved"
    elif decision in {"reject", "rejected"}:
        state["approval_status"] = "rejected"
        state["status"] = "rejected"
    else:
        # Interrupt point: still waiting.
        state["approval_status"] = "pending"
        state["status"] = "pending_review"
    return state


def execution_node(state: dict) -> dict:
    db = _db()
    ota = _ota()
    kind = state.get("kind") or "pricing_strategy"
    pre_id = state.get("checkpoint_id") or ""
    # Always snapshot immediately before mutating anything.
    pre_id = _save_checkpoint(state, EXECUTION_NODE, kind="pre_execution")
    state["checkpoint_id"] = pre_id

    from app.services.risk_policy import load_execution_policy
    from app.services.writes import execute_state_write

    policy = load_execution_policy(db, hotel_id=state.get("hotel_id"))
    if kind == "review_reply":
        review = db.get(Review, int(state["review_id"]))
        state["previous_state"] = {"reply_status": review.reply_status, "executed_reply": review.executed_reply}

    job, result = execute_state_write(
        db,
        state,
        policy=policy,
        platform=ota.platform,
        simulate=state.get("_sandbox_simulate"),
    )
    if state.get("auto_executed"):
        result = {**result, "auto_executed": True, "actor": state.get("actor"), "write_job_id": job.id}
        job.receipt = result
        db.commit()
    else:
        result = {**result, "write_job_id": job.id}
    state["execution_result"] = result
    state["write_job_id"] = job.id
    if job.status in {"accepted", "reconciled"}:
        state["status"] = "executed"
        state["approval_status"] = "executed"
    elif job.status == "partial":
        state["status"] = "execution_partial"
        state["error"] = job.last_error
    elif job.status == "rolled_back":
        state["status"] = "rolled_back"
        state["approval_status"] = "rolled_back"
        state["error"] = job.last_error
    else:
        state["status"] = "execution_failed"
        state["error"] = job.last_error or result.get("message")
    return state


def memory_node(state: dict) -> dict:
    """Persist an in-hotel decision episode and an anonymized industry pattern. Idempotent."""
    from app.services.memory import persist_decision_episode, should_persist_episode

    if not should_persist_episode(state):
        state["memory_skipped"] = True
        return state
    row = persist_decision_episode(_db(), state)
    if row is not None:
        state["memory_id"] = row.id
        state["memory_kind"] = row.kind
    return state


def write_audit(
    db: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
    hotel_id: int | None = None,
) -> AuditLog:
    if hotel_id is None:
        from app.services.tenancy import session_hotel_id

        hotel_id = session_hotel_id(db)
    row = AuditLog(
        hotel_id=int(hotel_id) if hotel_id is not None else None,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before=_json_safe(before),
        after=_json_safe(after),
        reason=reason,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _persist_strategy_row(state: dict) -> Strategy | None:
    db = _db()
    kind = state.get("kind")
    if kind == "review_reply":
        return None
    payload = state.get("strategy") or {}
    room_type_id = payload.get("room_type_id") or state.get("room_type_id")
    date_from = payload.get("date_from") or state.get("date_from")
    date_to = payload.get("date_to") or state.get("date_to")
    row = Strategy(
        hotel_id=int(state["hotel_id"]),
        workflow_run_id=int(state["run_id"]),
        kind="campaign" if kind == "campaign" else "pricing",
        room_type_id=room_type_id,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
        current_price=payload.get("current_price"),
        min_price=payload.get("min_price"),
        max_price=payload.get("max_price"),
        recommended_price=payload.get("recommended_price"),
        confidence=payload.get("confidence") or 0,
        rationale=payload.get("rationale") or "",
        expected_impact=payload.get("expected_impact"),
        constraints=payload.get("constraints"),
        daily_breakdown=payload.get("daily_breakdown"),
        campaign_advice=payload.get("campaign_advice"),
        status="pending_review",
        risk_level=state.get("risk_level") or "medium",
        checkpoint_id=state.get("checkpoint_id") or "",
        previous_state={"list_snapshot": payload.get("current_price")},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    state["strategy_id"] = row.id
    run = db.get(WorkflowRun, int(state["run_id"]))
    if run:
        run.related_strategy_id = row.id
        run.status = "pending_review"
        run.current_node = HUMAN_REVIEW
        run.payload = _json_safe(state)
        db.commit()
    return row


def _new_run(db: Session, hotel_id: int, kind: str, actor: str, **extra) -> WorkflowRun:
    run = WorkflowRun(
        hotel_id=hotel_id,
        kind=kind,
        status="running",
        current_node=DATA_NODE,
        payload={},
        created_by=actor,
        related_review_id=extra.get("review_id"),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _initial_state(run: WorkflowRun, **kwargs) -> dict:
    today = date.today()
    state: dict = {
        "hotel_id": run.hotel_id,
        "hotel_profile": {},
        "ota_snapshot": {},
        "competitor_data": [],
        "signals": {},
        "analysis_result": {},
        "strategy": {},
        "risk_level": "medium",
        "execution_result": {},
        "approval_status": "pending",
        "checkpoint_id": "",
        "historical_strategies": [],
        "historical_memories": [],
        "peer_patterns": [],
        "industry_patterns": [],
        "run_id": run.id,
        "kind": run.kind,
        "room_type_id": kwargs.get("room_type_id"),
        "review_id": kwargs.get("review_id"),
        "date_from": (kwargs.get("date_from") or today).isoformat()
        if not isinstance(kwargs.get("date_from"), str)
        else kwargs.get("date_from"),
        "date_to": (kwargs.get("date_to") or (today + timedelta(days=13))).isoformat()
        if not isinstance(kwargs.get("date_to"), str)
        else kwargs.get("date_to"),
        "current_node": DATA_NODE,
        "status": "running",
        "error": None,
        "degraded": False,
        "actor": kwargs.get("actor") or run.created_by,
        "path": [],
    }
    from app.services.memory import attach_hotel_memories
    from app.services.industry_patterns import attach_peer_patterns

    attach_hotel_memories(state, _db())
    attach_peer_patterns(state, _db())
    return state


def _await_human_review(state: dict) -> dict:
    """Park the run at human_review and leave a visible path node."""
    state["current_node"] = HUMAN_REVIEW
    state["status"] = "pending_review"
    state["approval_status"] = "pending"
    path = list(state.get("path") or [])
    if HUMAN_REVIEW not in path:
        path.append(HUMAN_REVIEW)
    state["path"] = path
    _save_checkpoint(state, HUMAN_REVIEW, kind="node")
    _log_node(state, HUMAN_REVIEW, "pending", {"waiting": True}, node_output_payload(HUMAN_REVIEW, state))
    run = _db().get(WorkflowRun, int(state["run_id"]))
    if run:
        run.current_node = HUMAN_REVIEW
        run.status = "pending_review"
        run.payload = _json_safe(state)
        _db().commit()
    return state


def _run_until_review(state: dict) -> dict:

    """Run data→strategy and stop at human_review. Auto-execute is applied after persist."""
    if USE_LANGGRAPH and _LG_GRAPH is not None:
        from app.services.checkpointer import graph_thread_id

        config = {"configurable": {"thread_id": graph_thread_id(state.get("hotel_id"), state["run_id"])}}
        result = _LG_GRAPH.invoke(state, config)
        # interrupt_before human_review: LangGraph returns state after strategy_node.
        result = _await_human_review(result)
        return result
    # Equivalent state machine with identical node functions and names.
    for name in PRE_REVIEW_NODES:
        state = _run_named_node(name, state)
        if state.get("status") == "failed":
            return state
    return _await_human_review(state)


def start_pricing_run(
    db: Session,
    hotel_id: int,
    actor: str,
    room_type_id: int,
    date_from: date,
    date_to: date,
) -> tuple[WorkflowRun, Strategy, dict]:
    _prepare_ctx(db)
    run = _new_run(db, hotel_id, "pricing_strategy", actor)
    state = _initial_state(run, actor=actor, room_type_id=room_type_id, date_from=date_from, date_to=date_to)
    state = _run_until_review(state)
    row = _persist_strategy_row(state)
    state, row = maybe_auto_execute(db, state, row)
    return run, row, state


def start_campaign_run(
    db: Session, hotel_id: int, actor: str, date_from: date, date_to: date
) -> tuple[WorkflowRun, Strategy, dict]:
    _prepare_ctx(db)
    run = _new_run(db, hotel_id, "campaign", actor)
    state = _initial_state(run, actor=actor, date_from=date_from, date_to=date_to)
    state = _run_until_review(state)
    row = _persist_strategy_row(state)
    state, row = maybe_auto_execute(db, state, row)
    return run, row, state


def start_review_reply_run(db: Session, hotel_id: int, actor: str, review_id: int) -> tuple[WorkflowRun, dict]:
    _prepare_ctx(db)
    run = _new_run(db, hotel_id, "review_reply", actor, review_id=review_id)
    state = _initial_state(run, actor=actor, review_id=review_id)
    state = _run_until_review(state)
    run.related_review_id = review_id
    run.status = "pending_review"
    run.current_node = HUMAN_REVIEW
    run.payload = _json_safe(state)
    db.commit()
    return run, state


def apply_human_decision(
    db: Session,
    *,
    run: WorkflowRun,
    actor: str,
    decision: str,
    comment: str = "",
    auto: bool = False,
    execute: bool = True,
    execution_only: bool = False,
) -> dict:
    _prepare_ctx(db)
    from app.services.risk_policy import AUTO_EXECUTE_ACTOR

    state = dict(run.payload or {})
    state["run_id"] = run.id
    state["hotel_id"] = run.hotel_id
    state["actor"] = actor
    state["review_comment"] = comment
    if auto or actor == AUTO_EXECUTE_ACTOR:
        state["auto_executed"] = True
        state["actor"] = AUTO_EXECUTE_ACTOR
        actor = AUTO_EXECUTE_ACTOR
        if not state.get("policy_snapshot"):
            ev = state.get("auto_execute_evaluation") or {}
            if isinstance(ev, dict):
                state["policy_snapshot"] = ev.get("policy_snapshot")
    normalized = decision.lower().strip()
    if execution_only:
        normalized = "approve"
    if normalized in {"approve", "approved"}:
        state["approval_status"] = "approved"
    elif normalized in {"reject", "rejected"}:
        state["approval_status"] = "rejected"
    else:
        raise ValueError("decision must be approve or reject")

    # human_review node first (records the decision), then maybe execution.
    if not execution_only:
        state = _run_named_node(HUMAN_REVIEW, state)
    auto_exec = bool(state.get("auto_executed"))
    if not execution_only:
        write_audit(
            db,
            actor=actor,
            action="review_approve" if state["approval_status"] == "approved" else "review_reject",
            entity_type="workflow_run",
            entity_id=str(run.id),
            before={"status": "pending_review"},
            after={
                "status": state["status"],
                "approval_status": state["approval_status"],
                "risk_level": state.get("risk_level"),
                "checkpoint_id": state.get("checkpoint_id"),
                "comment": comment,
                "auto_executed": auto_exec,
                "policy_snapshot": state.get("policy_snapshot") or state.get("auto_execute_evaluation", {}).get("policy_snapshot") if isinstance(state.get("auto_execute_evaluation"), dict) else state.get("policy_snapshot"),
            },
            reason=comment or f"人审{state['approval_status']}，风险等级 {state.get('risk_level')}",
        )

    strategy = db.get(Strategy, run.related_strategy_id) if run.related_strategy_id else None
    review = db.get(Review, run.related_review_id) if run.related_review_id else None

    if state["approval_status"] == "approved":
        if strategy:
            strategy.status = "approved"
            if not execution_only:
                strategy.reviewer = actor
                strategy.reviewed_at = datetime.utcnow()
                strategy.review_comment = comment
            state["strategy_id"] = strategy.id
            db.commit()
        if review:
            review.reply_status = "approved"
            if not execution_only:
                review.review_comment = comment
            db.commit()
        if not execute:
            run.status = "approved"
            run.current_node = HUMAN_REVIEW
            run.payload = _json_safe(state)
            run.updated_at = datetime.utcnow()
            db.commit()
            return state
        state = _run_named_node(EXECUTION_NODE, state)
        result = state.get("execution_result") or {}
        accepted = bool(result.get("accepted", False))
        outcome = result.get("outcome") or ("accepted" if accepted else "failed")
        # Refresh entities; the write ledger may have already updated status or auto-rolled back.
        if strategy:
            db.refresh(strategy)
        if review:
            db.refresh(review)
        db.refresh(run)
        if strategy and state.get("auto_executed") and strategy.status in {"executed", "execution_partial", "execution_failed", "rolled_back"}:
            strategy.reviewer = actor
            db.commit()
        if run.status == "rolled_back" or state.get("status") == "rolled_back":
            write_audit(
                db,
                actor=actor,
                action="write_auto_rollback",
                entity_type="workflow_run",
                entity_id=str(run.id),
                after={
                    "execution_result": result,
                    "checkpoint_id": state.get("checkpoint_id"),
                    "write_job_id": state.get("write_job_id"),
                    "risk_level": state.get("risk_level"),
                    "mode": result.get("mode"),
                    "outcome": outcome,
                },
                reason=result.get("message") or "写入失败后已按策略自动回滚",
            )
        elif accepted or outcome == "accepted":
            if strategy:
                strategy.status = "executed"
                strategy.executed_at = strategy.executed_at or datetime.utcnow()
                strategy.execution_payload = result
                strategy.checkpoint_id = state.get("checkpoint_id") or strategy.checkpoint_id
                if state.get("auto_executed"):
                    strategy.reviewer = actor
                db.commit()
            if review:
                review.reply_status = "executed"
                db.commit()
            run.status = "executed"
            run.current_node = EXECUTION_NODE
            write_audit(
                db,
                actor=actor,
                action="auto_execute" if state.get("auto_executed") else "execute",
                entity_type="workflow_run",
                entity_id=str(run.id),
                after={
                    "execution_result": result,
                    "checkpoint_id": state.get("checkpoint_id"),
                    "write_job_id": state.get("write_job_id"),
                    "risk_level": state.get("risk_level"),
                    "mode": result.get("mode"),
                    "auto_executed": bool(state.get("auto_executed")),
                    "policy_snapshot": state.get("policy_snapshot"),
                },
                reason=(
                    "低风险策略按酒店执行策略自动执行（官方沙盒回执，未向真实 OTA 发送请求）"
                    if state.get("auto_executed")
                    else "人审通过后写入官方沙盒回执，未向真实 OTA 发送请求"
                ),
            )
        else:
            run.error = result.get("message") or state.get("error")
            if run.status not in {"execution_partial", "execution_failed", "rolled_back"}:
                run.status = "execution_partial" if outcome == "partial" else "execution_failed"
            write_audit(
                db,
                actor=actor,
                action="write_partial" if outcome == "partial" else "execution_refused",
                entity_type="workflow_run",
                entity_id=str(run.id),
                after={
                    "execution_result": result,
                    "checkpoint_id": state.get("checkpoint_id"),
                    "write_job_id": state.get("write_job_id"),
                    "risk_level": state.get("risk_level"),
                    "mode": result.get("mode"),
                    "outcome": outcome,
                },
                reason=result.get("message") or "写入未全部成功，未向真实 OTA 发送请求",
            )
    else:
        if strategy:
            strategy.status = "rejected"
            strategy.reviewer = actor
            strategy.reviewed_at = datetime.utcnow()
            strategy.review_comment = comment
            db.commit()
        if review:
            review.reply_status = "rejected"
            review.review_comment = comment
            db.commit()
        run.status = "rejected"
        run.current_node = HUMAN_REVIEW

    from app.services.memory import should_persist_episode

    if should_persist_episode(state):
        state = _run_named_node(MEMORY_NODE, state)
        run.current_node = MEMORY_NODE

    run.payload = _json_safe(state)
    run.updated_at = datetime.utcnow()
    db.commit()
    return state


def execute_approved_run(db: Session, run: WorkflowRun, actor: str) -> dict:
    """Execute a strategy or review that was approved but not yet written."""
    strategy = db.get(Strategy, run.related_strategy_id) if run.related_strategy_id else None
    review = db.get(Review, run.related_review_id) if run.related_review_id else None
    if strategy is None and review is None:
        raise ValueError("找不到可执行的策略或回复")
    if strategy and strategy.status != "approved":
        raise ValueError("仅已通过、尚未执行的策略可执行")
    if review and review.reply_status != "approved":
        raise ValueError("仅已通过、尚未执行的回复可执行")
    return apply_human_decision(
        db,
        run=run,
        actor=actor,
        decision="approve",
        comment="执行已通过的方案",
        execute=True,
        execution_only=True,
    )


def rollback_run(db: Session, run: WorkflowRun, actor: str, reason: str = "") -> dict:
    """Restore the pre-execution checkpoint. Never talks to a real OTA, including after auto-execute."""
    _prepare_ctx(db)
    state = dict(run.payload or {})
    allowed = {"executed", "execution_failed", "execution_partial"}
    if run.status not in allowed and (state.get("status") not in allowed):
        raise ValueError("只能回滚已执行、写入失败或部分成功的决策")

    pre = (
        db.query(DecisionCheckpoint)
        .filter(
            DecisionCheckpoint.run_id == run.id,
            DecisionCheckpoint.kind == "pre_execution",
        )
        .order_by(DecisionCheckpoint.id.desc())
        .first()
    )
    if pre is None:
        raise ValueError("找不到执行前检查点，无法回滚")

    checkpoint_id = pre.checkpoint_id
    strategy = db.get(Strategy, run.related_strategy_id) if run.related_strategy_id else None
    review = db.get(Review, run.related_review_id) if run.related_review_id else None
    before = {
        "run_status": run.status,
        "strategy_status": strategy.status if strategy else None,
        "review_status": review.reply_status if review else None,
        "checkpoint_id": checkpoint_id,
    }
    if strategy:
        strategy.status = "rolled_back"
        strategy.rolled_back_at = datetime.utcnow()
        strategy.rollback_reason = reason
        strategy.checkpoint_id = checkpoint_id
    if review:
        review.reply_status = "rolled_back"
        review.executed_reply = ""
    state["status"] = "rolled_back"
    state["approval_status"] = "rolled_back"
    state["current_node"] = ROLLBACK_NODE
    state["checkpoint_id"] = checkpoint_id
    path = list(state.get("path") or [])
    path.append(ROLLBACK_NODE)
    state["path"] = path
    state["execution_result"] = {
        **(state.get("execution_result") or {}),
        "rolled_back": True,
        "restored_checkpoint_id": checkpoint_id,
        "message": "已按执行前检查点回退状态（沙盒官方回执，未调用真实 OTA）。",
    }
    _log_node(
        {**state, "run_id": run.id},
        ROLLBACK_NODE,
        "ok",
        before,
        {**node_output_payload(ROLLBACK_NODE, state), "checkpoint_id": checkpoint_id},
    )
    # Persist a restore marker.
    _CTX["db"] = db
    restore = DecisionCheckpoint(
        checkpoint_id=str(uuid.uuid4()),
        run_id=run.id,
        hotel_id=run.hotel_id,
        node_name=ROLLBACK_NODE,
        kind="rollback_restore",
        state=_json_safe(state),
    )
    db.add(restore)
    run.status = "rolled_back"
    run.current_node = ROLLBACK_NODE
    run.payload = _json_safe(state)
    db.commit()
    write_audit(
        db,
        actor=actor,
        action="rollback",
        entity_type="workflow_run",
        entity_id=str(run.id),
        before=before,
        after={"status": "rolled_back", "checkpoint_id": checkpoint_id},
        reason=reason or f"回滚至检查点 {checkpoint_id}",
    )
    from app.services.writes import mark_jobs_rolled_back

    mark_jobs_rolled_back(db, workflow_run_id=run.id, actor=actor)
    from app.services.memory import should_persist_episode

    if should_persist_episode(state):
        state = _run_named_node(MEMORY_NODE, state)
        run.current_node = MEMORY_NODE
        run.payload = _json_safe(state)
        db.commit()
    return state


def _prepare_ctx(db: Session) -> None:
    if _LG_GRAPH is None and _LG_REASON == "not_initialized":
        _try_langgraph()
    _CTX.clear()
    _CTX["db"] = db
    _CTX["ota"] = CtripMockAdapter()
    _CTX["today"] = date.today()



def maybe_auto_execute(db: Session, state: dict, row: Strategy | None) -> tuple[dict, Strategy | None]:
    """If policy allows, auto-approve and execute a freshly persisted pending strategy."""
    from app.services.risk_policy import AUTO_EXECUTE_ACTOR, load_execution_policy, may_auto_execute

    if row is None:
        return state, row
    policy = load_execution_policy(db, hotel_id=state.get("hotel_id") or (row.hotel_id if row else None))
    decision = may_auto_execute(
        kind=state.get("kind") or (row.kind if row else None),
        risk_level=state.get("risk_level") or (row.risk_level if row else None),
        policy=policy,
        strategy_payload=state.get("strategy") or {},
        state=state,
        strategy=row,
    )
    state["auto_execute_evaluation"] = decision.as_dict()
    run = db.get(WorkflowRun, int(state.get("run_id") or 0))
    if run:
        run.payload = _json_safe(state)
        db.commit()
    if not decision.allowed:
        return state, row
    if run is None:
        return state, row
    state["auto_executed"] = True
    state["policy_snapshot"] = decision.policy_snapshot
    run.payload = _json_safe(state)
    db.commit()
    apply_human_decision(
        db,
        run=run,
        actor=AUTO_EXECUTE_ACTOR,
        decision="approve",
        comment=decision.reason,
        auto=True,
    )
    db.refresh(row)
    run = db.get(WorkflowRun, run.id)
    state = dict((run.payload if run else state) or state)
    return state, row


def generate_all_strategies(
    db: Session,
    hotel_id: int,
    actor: str,
    room_type_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    kind: str = "all",
) -> list[dict]:
    today = date.today()
    date_from = date_from or today
    date_to = date_to or (today + timedelta(days=13))
    mode = (kind or "all").lower().strip()
    if mode not in {"all", "pricing", "campaign"}:
        raise ValueError("kind must be all, pricing or campaign")
    rooms = db.query(RoomType).filter(RoomType.hotel_id == hotel_id).order_by(RoomType.id).all()
    if room_type_id:
        rooms = [r for r in rooms if r.id == room_type_id]
    created = []
    if mode in {"all", "pricing"}:
        for room in rooms:
            run, row, state = start_pricing_run(db, hotel_id, actor, room.id, date_from, date_to)
            created.append({"kind": "pricing", "strategy_id": row.id if row else None, "run_id": run.id, "state": state})
    if mode in {"all", "campaign"}:
        run, row, state = start_campaign_run(db, hotel_id, actor, date_from, date_to)
        created.append({"kind": "campaign", "strategy_id": row.id if row else None, "run_id": run.id, "state": state})
    return created


def reset_langgraph() -> None:
    """Drop the compiled graph so the next call rebuilds it against the current DB."""
    global USE_LANGGRAPH, _LG_GRAPH, _LG_REASON
    _LG_GRAPH = None
    USE_LANGGRAPH = False
    _LG_REASON = "not_initialized"


def load_graph_state(hotel_id: int, run_id: int):
    """Load durable LangGraph state for one hotel run. Refuses a cross-hotel thread id."""
    from app.services.checkpointer import assert_thread_belongs_to_hotel, graph_thread_id

    if _LG_GRAPH is None and _LG_REASON == "not_initialized":
        _try_langgraph()
    if _LG_GRAPH is None:
        return None
    thread_id = graph_thread_id(hotel_id, run_id)
    assert_thread_belongs_to_hotel(thread_id, hotel_id)
    snapshot = _LG_GRAPH.get_state({"configurable": {"thread_id": thread_id}})
    if snapshot is None or not getattr(snapshot, "values", None):
        return None
    values = snapshot.values or {}
    stored = values.get("hotel_id")
    if stored is not None and int(stored) != int(hotel_id):
        raise PermissionError("不能恢复或回滚其他酒店的工作流")
    return snapshot
