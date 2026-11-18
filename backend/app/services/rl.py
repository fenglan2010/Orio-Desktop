"""Offline in-hotel contextual / tabular learner (Phase IV increment 4).

Fits action values from this hotel's measured episodes only, including
rejected / rolled_back / execution_failed observations as no-change,
reverted, or failed-write action buckets. Rewards are observed packed-KPI
deltas versus the decision-time baseline, never YoY, and never treated as
causal revenue. Anonymous industry patterns may be cited, never used as
another hotel's reward labels.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import MemoryRecord, RLPolicyValue
from app.services.industry_patterns import price_direction_of, signal_types_of
from app.services.memory import MEMORY_KIND

OUTCOMES_USED = frozenset({"measured", "partially_measured"})
MIN_EPISODES_FOR_POLICY = 3
MIN_EPISODES_TO_SUGGEST = 2

ACTION_LABELS = {
    "raise": "涨价",
    "hold": "价格不变",
    "cut": "降价",
    "no_change": "不改价",
    "reverted": "已回滚",
    "failed_write": "写入失败",
    "run_campaign": "推进活动",
}

REWARD_NOTE = (
    "奖励为相对决策时基线的观察变化：优先使用已实现入住日的客房收入变化；"
    "若尚无已实现切片，则使用在手订单 / OTB 已售间夜变化。观察值不是因果收入，也不是上年同期。"
)


def action_bucket_of(row: MemoryRecord, payload: dict | None = None) -> str:
    payload = payload if isinstance(payload, dict) else (row.payload or {})
    status = row.approval_status or payload.get("status") or ""
    kind = row.action_kind or payload.get("action_kind") or "pricing"
    if status == "rejected":
        return "no_change"
    if status == "rolled_back":
        return "reverted"
    if status == "execution_failed":
        return "failed_write"
    if kind == "campaign":
        return "run_campaign"
    direction = price_direction_of(None, payload)
    if direction == "涨":
        return "raise"
    if direction == "降":
        return "cut"
    if direction == "不变":
        return "hold"
    return "hold"


def situation_context(row: MemoryRecord, payload: dict | None = None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else (row.payload or {})
    flags = signal_types_of(None, payload)
    direction = price_direction_of(None, payload) or ""
    return {
        "action_kind": row.action_kind or payload.get("action_kind") or "",
        "signal_types": list(flags),
        "risk_level": (row.risk_level or payload.get("risk_level") or ""),
        "price_direction": direction,
    }


def context_key(context: dict[str, Any]) -> str:
    canonical = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def observed_reward(payload: dict | None) -> dict[str, Any] | None:
    """Documented observed reward. Never YoY, never causal."""
    data = payload or {}
    impact = data.get("impact")
    if not isinstance(impact, dict):
        return None
    realized = impact.get("realized") if isinstance(impact.get("realized"), dict) else None
    otb = impact.get("otb_pickup") if isinstance(impact.get("otb_pickup"), dict) else None
    if realized and isinstance(realized.get("delta"), dict):
        delta = realized["delta"]
        return {
            "value": float(delta.get("revenue_abs") or 0),
            "unit": "revenue_yuan",
            "source": "realized_room_revenue",
            "observed": True,
            "causal": False,
            "label": "已实现入住日客房收入相对基线的观察变化，不是因果收入。",
        }
    if otb and isinstance(otb.get("delta"), dict):
        delta = otb["delta"]
        return {
            "value": float(delta.get("sold_rooms_abs") or 0),
            "unit": "sold_rooms",
            "source": "otb_sold_rooms",
            "observed": True,
            "causal": False,
            "label": "尚未实现入住日的在手订单 / OTB 已售间夜变化，不是已实现客房收入，也不是预测。",
        }
    return None


def _measured_episodes(db: Session, hotel_id: int) -> list[MemoryRecord]:
    return (
        db.query(MemoryRecord)
        .filter(
            MemoryRecord.hotel_id == int(hotel_id),
            MemoryRecord.kind == MEMORY_KIND,
            MemoryRecord.action_kind.in_(("pricing", "campaign")),
        )
        .order_by(MemoryRecord.id.asc())
        .all()
    )


def fit_hotel_policy(db: Session, hotel_id: int) -> dict[str, Any]:
    """Refit tabular action values from this hotel's measured episodes only."""
    hotel_id = int(hotel_id)
    rows = _measured_episodes(db, hotel_id)
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    used = 0
    for row in rows:
        payload = row.payload or {}
        if (payload.get("outcome") or "") not in OUTCOMES_USED:
            continue
        reward = observed_reward(payload)
        if reward is None:
            continue
        ctx = situation_context(row, payload)
        key = context_key(ctx)
        action = action_bucket_of(row, payload)
        ident = (key, action, reward["unit"])
        slot = buckets.get(ident)
        if slot is None:
            slot = {
                "context_key": key,
                "context": ctx,
                "action_bucket": action,
                "reward_unit": reward["unit"],
                "values": [],
            }
            buckets[ident] = slot
        slot["values"].append(float(reward["value"]))
        used += 1

    db.query(RLPolicyValue).filter(RLPolicyValue.hotel_id == hotel_id).delete()
    db.flush()
    now = datetime.utcnow()
    for slot in buckets.values():
        values = slot["values"]
        db.add(
            RLPolicyValue(
                hotel_id=hotel_id,
                context_key=slot["context_key"],
                context=slot["context"],
                action_bucket=slot["action_bucket"],
                episode_count=len(values),
                mean_reward=round(sum(values) / len(values), 4) if values else 0.0,
                reward_unit=slot["reward_unit"],
                updated_at=now,
            )
        )
    db.commit()
    return serialize_policy(db, hotel_id)


def _unit_label(unit: str) -> str:
    if unit == "revenue_yuan":
        return "元（已实现客房收入观察变化）"
    if unit == "sold_rooms":
        return "间夜（在手订单观察变化）"
    return unit or "观察单位"


def suggestions_from_values(rows: list[RLPolicyValue], *, measured_count: int) -> list[dict[str, Any]]:
    if measured_count < MIN_EPISODES_FOR_POLICY:
        return []
    grouped: dict[str, list[RLPolicyValue]] = defaultdict(list)
    for row in rows:
        grouped[row.context_key].append(row)
    suggestions: list[dict[str, Any]] = []
    for context_key_, items in grouped.items():
        # Prefer revenue-unit rows when mixed.
        revenue = [r for r in items if r.reward_unit == "revenue_yuan"]
        pool = revenue or items
        best = max(pool, key=lambda r: (r.mean_reward, r.episode_count))
        if best.episode_count < MIN_EPISODES_TO_SUGGEST:
            continue
        context = best.context or {}
        suggestions.append(
            {
                "context_key": context_key_,
                "context": context,
                "action_bucket": best.action_bucket,
                "action_label": ACTION_LABELS.get(best.action_bucket, best.action_bucket),
                "episode_count": best.episode_count,
                "mean_observed_reward": best.mean_reward,
                "reward_unit": best.reward_unit,
                "reward_unit_label": _unit_label(best.reward_unit),
                "observed": True,
                "causal": False,
                "supporting_actions": [
                    {
                        "action_bucket": r.action_bucket,
                        "action_label": ACTION_LABELS.get(r.action_bucket, r.action_bucket),
                        "episode_count": r.episode_count,
                        "mean_observed_reward": r.mean_reward,
                        "reward_unit": r.reward_unit,
                    }
                    for r in sorted(pool, key=lambda r: (-r.mean_reward, -r.episode_count))
                ],
                "note": "根据本店已复盘记录的观察奖励选出，不是因果结论，也不会自动改价。",
            }
        )
    suggestions.sort(key=lambda s: (-s["mean_observed_reward"], -s["episode_count"]))
    return suggestions


def serialize_policy(db: Session, hotel_id: int) -> dict[str, Any]:
    hotel_id = int(hotel_id)
    rows = (
        db.query(RLPolicyValue)
        .filter(RLPolicyValue.hotel_id == hotel_id)
        .order_by(RLPolicyValue.context_key.asc(), RLPolicyValue.action_bucket.asc())
        .all()
    )
    measured = 0
    for episode in _measured_episodes(db, hotel_id):
        payload = episode.payload or {}
        if payload.get("outcome") in OUTCOMES_USED and observed_reward(payload):
            measured += 1
    insufficient = measured < MIN_EPISODES_FOR_POLICY
    values = [
        {
            "context_key": row.context_key,
            "context": row.context or {},
            "action_bucket": row.action_bucket,
            "action_label": ACTION_LABELS.get(row.action_bucket, row.action_bucket),
            "episode_count": row.episode_count,
            "mean_observed_reward": row.mean_reward,
            "reward_unit": row.reward_unit,
            "reward_unit_label": _unit_label(row.reward_unit),
            "observed": True,
            "causal": False,
        }
        for row in rows
    ]
    suggestions = [] if insufficient else suggestions_from_values(rows, measured_count=measured)
    reason = ""
    if insufficient:
        reason = (
            f"本店已复盘且可计算观察奖励的决策片段不足 {MIN_EPISODES_FOR_POLICY} 条"
            f"（当前 {measured} 条），暂不给出有把握的策略建议。"
        )
    return {
        "hotel_id": hotel_id,
        "measured_episode_count": measured,
        "insufficient": insufficient,
        "insufficient_reason": reason,
        "min_episodes": MIN_EPISODES_FOR_POLICY,
        "reward_definition": {
            "primary": "realized stay room-revenue delta vs decision-time baseline",
            "fallback": "OTB sold-room / pickup delta vs baseline",
            "observed": True,
            "causal": False,
            "label": REWARD_NOTE,
        },
        "action_values": values,
        "suggestions": suggestions,
        "note": REWARD_NOTE,
    }
