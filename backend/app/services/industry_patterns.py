"""Anonymized cross-hotel industry patterns (Phase IV increment 2).

Identifiable hotel data never crosses hotels. Patterns are visible only when
at least two hotels have contributed the same de-identified feature signature.
Hotel-facing APIs never return contributor hotel ids or other identity fields.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Hotel, IndustryPattern, IndustryPatternContribution, MemoryRecord, WorkflowRun
from app.services.memory import (
    ACTION_KIND_LABELS,
    OUTCOME_NOT_MEASURED,
    STATUS_LABELS,
    action_kind_of,
    terminal_status_of,
)

KNOWN_SIGNAL_TYPES = (
    "rank_drop",
    "price_inversion",
    "conversion_drop",
    "competitor_cut",
    "negative_review",
)

STATUS_BUCKETS = {
    "executed": "executed",
    "rejected": "rejected",
    "rolled_back": "rolled_back",
    "execution_failed": "execution_failed",
    "execution_partial": "partial",
    "partial": "partial",
}

PRICE_DIRECTIONS = frozenset({"涨", "降", "不变"})
POSITIONING_TOKENS = ("精品", "商务")

API_NOTE = "跨店学习已开始，但是匿名模式，不展示其他酒店名称，不会训练模型，不会自动改价。"


def status_bucket(status: str | None) -> str:
    raw = (status or "").lower().strip()
    return STATUS_BUCKETS.get(raw, raw)


def signal_types_of(state: dict | None, payload: dict | None = None) -> list[str]:
    """Return known signal flags already present on the run / episode. Never invent flags."""
    found: list[str] = []
    sources: list[Any] = []
    if isinstance(state, dict):
        signals = state.get("signals")
        if isinstance(signals, dict):
            sources.append(signals.get("flags"))
        sources.append(state.get("signal_types"))
        payload_from_state = state.get("payload") if isinstance(state.get("payload"), dict) else None
        if payload_from_state:
            sources.append(payload_from_state.get("signal_types"))
    if isinstance(payload, dict):
        sources.append(payload.get("signal_types"))
        signals = payload.get("signals")
        if isinstance(signals, dict):
            sources.append(signals.get("flags"))
        elif isinstance(signals, list):
            sources.append(signals)
    known = set(KNOWN_SIGNAL_TYPES)
    for src in sources:
        if not src:
            continue
        if isinstance(src, str):
            src = [src]
        if not isinstance(src, (list, tuple, set)):
            continue
        for item in src:
            flag = str(item or "").strip()
            if flag in known and flag not in found:
                found.append(flag)
        if found:
            break
    # Stable, documented order (not first-seen order) so the pattern key is deterministic.
    return [flag for flag in KNOWN_SIGNAL_TYPES if flag in found]


def price_direction_of(state: dict | None, payload: dict | None = None) -> str:
    current = None
    recommended = None
    blobs: list[dict] = []
    if isinstance(payload, dict):
        blobs.append(payload)
        if isinstance(payload.get("proposed_action"), dict):
            blobs.append(payload["proposed_action"])
    if isinstance(state, dict):
        blobs.append(state)
        if isinstance(state.get("strategy"), dict):
            blobs.append(state["strategy"])
        if isinstance(state.get("proposed_action"), dict):
            blobs.append(state["proposed_action"])
        payload_from_state = state.get("payload") if isinstance(state.get("payload"), dict) else None
        if payload_from_state:
            blobs.append(payload_from_state)
            if isinstance(payload_from_state.get("proposed_action"), dict):
                blobs.append(payload_from_state["proposed_action"])
    for blob in blobs:
        if current is None and blob.get("current_price") is not None:
            current = blob.get("current_price")
        if recommended is None and blob.get("recommended_price") is not None:
            recommended = blob.get("recommended_price")
        if current is not None and recommended is not None:
            break
    if current is None or recommended is None:
        return ""
    try:
        cur = float(current)
        rec = float(recommended)
    except (TypeError, ValueError):
        return ""
    if rec > cur:
        return "涨"
    if rec < cur:
        return "降"
    return "不变"


def positioning_tokens_of(text: str | None) -> list[str]:
    value = text or ""
    return [token for token in POSITIONING_TOKENS if token in value]


def pattern_features(
    *,
    action_kind: str,
    signal_types: list[str],
    risk_level: str,
    terminal_status: str,
    price_direction: str = "",
) -> dict[str, Any]:
    return {
        "action_kind": action_kind or "",
        "signal_types": list(signal_types or []),
        "risk_level": (risk_level or "").lower().strip(),
        "terminal_status": status_bucket(terminal_status),
        "price_direction": price_direction if price_direction in PRICE_DIRECTIONS else "",
    }


def pattern_key(features: dict[str, Any]) -> str:
    canonical = json.dumps(features, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_features(db: Session, hotel_id: int, state: dict | None, payload: dict | None = None) -> dict[str, Any]:
    state = state or {}
    if payload is None and isinstance(state.get("payload"), dict):
        payload = state.get("payload")
    payload = payload or {}
    kind = action_kind_of(state) if state else ""
    if not kind:
        kind = str(payload.get("action_kind") or "")
    terminal = terminal_status_of(state) if state else None
    if not terminal:
        terminal = payload.get("status") or payload.get("approval_status") or state.get("approval_status") or ""
    risk = str(state.get("risk_level") or payload.get("risk_level") or "")
    flags = signal_types_of(state, payload)
    direction = price_direction_of(state, payload)
    # Positioning is optional and is not part of the pattern key: the two demo
    # hotels share 商务 but Hangzhou also contains 精品, and a key split would
    # hide otherwise matching anonymous episodes.
    _ = db, hotel_id
    return pattern_features(
        action_kind=kind,
        signal_types=flags,
        risk_level=risk,
        terminal_status=str(terminal or ""),
        price_direction=direction,
    )


def _shared_peer_positioning_bucket(db: Session, pattern_id: int, current_hotel_id: int) -> str:
    rows = (
        db.query(Hotel.positioning)
        .join(IndustryPatternContribution, IndustryPatternContribution.hotel_id == Hotel.id)
        .filter(
            IndustryPatternContribution.pattern_id == pattern_id,
            IndustryPatternContribution.hotel_id != int(current_hotel_id),
        )
        .all()
    )
    token_sets = [set(positioning_tokens_of(row[0])) for row in rows]
    if not token_sets:
        return ""
    shared = set.intersection(*token_sets) if token_sets else set()
    for token in POSITIONING_TOKENS:
        if token in shared:
            return token
    return ""


def pattern_summary(features: dict[str, Any], *, peer_hotel_count: int | None = None) -> str:
    kind = features.get("action_kind") or ""
    terminal = status_bucket(features.get("terminal_status"))
    kind_label = ACTION_KIND_LABELS.get(kind, "决策")
    if terminal == "rejected":
        verb = "否决"
    elif terminal == "rolled_back":
        verb = "回滚"
    elif terminal in {"execution_failed", "partial"}:
        verb = "尝试执行"
    else:
        verb = "执行"
    signals = features.get("signal_types") or []
    if signals:
        signal_bit = "在类似信号下"
    else:
        signal_bit = "在类似情形下"
    direction = features.get("price_direction") or ""
    direction_bit = f"（价格方向：{direction}）" if direction else ""
    count_bit = ""
    if peer_hotel_count:
        count_bit = f"已有其他 {int(peer_hotel_count)} 家同类酒店的匿名记录。"
    return (
        f"同类酒店{signal_bit}曾{verb}类似{kind_label}动作{direction_bit}。"
        f"{count_bit}"
        "这是匿名模式引用，不展示其他酒店名称。结果影响尚未测算。"
    )


def serialize_pattern(
    row: IndustryPattern,
    *,
    peer_hotel_count: int,
    peer_episode_count: int,
    positioning_bucket: str = "",
) -> dict[str, Any]:
    features = pattern_features(
        action_kind=row.action_kind,
        signal_types=list(row.signal_types or []),
        risk_level=row.risk_level or "",
        terminal_status=row.terminal_status,
        price_direction=row.price_direction or "",
    )
    terminal = features["terminal_status"]
    kind = features["action_kind"]
    return {
        "pattern_key": row.pattern_key,
        "action_kind": kind,
        "action_kind_label": ACTION_KIND_LABELS.get(kind, kind or "决策"),
        "signal_types": features["signal_types"],
        "risk_level": features["risk_level"],
        "terminal_status": terminal,
        "status_label": STATUS_LABELS.get(
            "execution_partial" if terminal == "partial" else terminal,
            terminal or "",
        ),
        "price_direction": features["price_direction"],
        "positioning_bucket": positioning_bucket or "",
        "peer_hotel_count": int(peer_hotel_count),
        "peer_episode_count": int(peer_episode_count),
        "outcome": OUTCOME_NOT_MEASURED,
        "impact": None,
        "summary": pattern_summary(features, peer_hotel_count=peer_hotel_count),
        "updated_at": row.last_contributed_at.isoformat() if row.last_contributed_at else (
            row.updated_at.isoformat() if row.updated_at else None
        ),
    }


def _contribution_counts(db: Session, hotel_id: int):
    hotel_counts = (
        db.query(
            IndustryPatternContribution.pattern_id.label("pattern_id"),
            func.count(func.distinct(IndustryPatternContribution.hotel_id)).label("hotels"),
        )
        .group_by(IndustryPatternContribution.pattern_id)
        .having(func.count(func.distinct(IndustryPatternContribution.hotel_id)) >= 2)
        .subquery()
    )
    other_counts = (
        db.query(
            IndustryPatternContribution.pattern_id.label("pattern_id"),
            func.count(func.distinct(IndustryPatternContribution.hotel_id)).label("peer_hotels"),
            func.count(IndustryPatternContribution.id).label("peer_episodes"),
        )
        .filter(IndustryPatternContribution.hotel_id != int(hotel_id))
        .group_by(IndustryPatternContribution.pattern_id)
        .having(func.count(func.distinct(IndustryPatternContribution.hotel_id)) >= 1)
        .subquery()
    )
    return hotel_counts, other_counts


def load_visible_patterns(db: Session, hotel_id: int, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Patterns visible to this hotel: k>=2 hotels, at least one other contributor. Counts exclude self."""
    if not hotel_id:
        return [], 0
    hotel_counts, other_counts = _contribution_counts(db, hotel_id)
    q = (
        db.query(
            IndustryPattern,
            other_counts.c.peer_hotels,
            other_counts.c.peer_episodes,
        )
        .join(hotel_counts, hotel_counts.c.pattern_id == IndustryPattern.id)
        .join(other_counts, other_counts.c.pattern_id == IndustryPattern.id)
        .order_by(
            other_counts.c.peer_episodes.desc(),
            IndustryPattern.last_contributed_at.desc(),
            IndustryPattern.id.desc(),
        )
    )
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    out: list[dict[str, Any]] = []
    for row, peer_hotels, peer_episodes in rows:
        bucket = _shared_peer_positioning_bucket(db, row.id, hotel_id)
        out.append(
            serialize_pattern(
                row,
                peer_hotel_count=int(peer_hotels),
                peer_episode_count=int(peer_episodes),
                positioning_bucket=bucket,
            )
        )
    return out, total


def attach_peer_patterns(state: dict, db: Session, *, limit: int = 20) -> dict:
    hotel_id = int(state.get("hotel_id") or 0)
    patterns, _total = load_visible_patterns(db, hotel_id, limit=limit)
    state["peer_patterns"] = patterns
    state["industry_patterns"] = patterns
    return state


def peer_pattern_facts(state: dict) -> list[dict[str, Any]]:
    """De-identified facts for analysis / strategy nodes. No hotel identity, prices, or impact claims."""
    facts: list[dict[str, Any]] = []
    for row in state.get("peer_patterns") or state.get("industry_patterns") or []:
        facts.append(
            {
                "action_kind": row.get("action_kind"),
                "signal_types": list(row.get("signal_types") or []),
                "risk_level": row.get("risk_level") or "",
                "terminal_status": row.get("terminal_status") or "",
                "price_direction": row.get("price_direction") or "",
                "positioning_bucket": row.get("positioning_bucket") or "",
                "peer_hotel_count": row.get("peer_hotel_count"),
                "peer_episode_count": row.get("peer_episode_count"),
                "outcome": row.get("outcome") or OUTCOME_NOT_MEASURED,
                "note": (
                    "同类酒店在类似信号下曾执行或否决类似动作。"
                    "这是匿名模式引用，不是其他酒店的真实策略，"
                    "也不会据此跳过人审或自动改价。"
                ),
            }
        )
    return facts


def _state_from_episode(db: Session, row: MemoryRecord, state: dict | None) -> dict:
    merged: dict[str, Any] = {}
    if row.run_id:
        run = db.get(WorkflowRun, int(row.run_id))
        if run and isinstance(run.payload, dict):
            merged.update(run.payload)
    if isinstance(row.payload, dict):
        merged["payload"] = row.payload
        if row.payload.get("signal_types") and "signals" not in merged:
            merged["signal_types"] = row.payload.get("signal_types")
    merged["hotel_id"] = row.hotel_id
    merged["run_id"] = row.run_id
    merged["strategy_id"] = row.strategy_id
    merged["action_kind"] = row.action_kind or merged.get("kind")
    merged["kind"] = merged.get("kind") or row.action_kind
    merged["risk_level"] = row.risk_level or merged.get("risk_level")
    merged["approval_status"] = row.approval_status or merged.get("approval_status")
    merged["status"] = row.approval_status or merged.get("status")
    if isinstance(state, dict):
        merged.update({k: v for k, v in state.items() if v is not None})
    return merged


def record_episode_pattern(db: Session, row: MemoryRecord, state: dict | None = None) -> IndustryPattern | None:
    """Upsert an anonymized pattern and one contribution for this in-hotel episode. Idempotent."""
    if row is None or not row.hotel_id or not row.episode_key:
        return None
    merged = _state_from_episode(db, row, state)
    features = extract_features(db, row.hotel_id, merged, row.payload)
    if not features.get("action_kind") or not features.get("terminal_status"):
        return None
    key = pattern_key(features)
    now = datetime.utcnow()
    pattern = db.query(IndustryPattern).filter(IndustryPattern.pattern_key == key).one_or_none()
    if pattern is None:
        pattern = IndustryPattern(
            pattern_key=key,
            action_kind=features["action_kind"],
            signal_types=features["signal_types"],
            risk_level=features["risk_level"],
            terminal_status=features["terminal_status"],
            price_direction=features["price_direction"],
            episode_count=0,
            last_contributed_at=now,
        )
        db.add(pattern)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            pattern = db.query(IndustryPattern).filter(IndustryPattern.pattern_key == key).one()

    existing = (
        db.query(IndustryPatternContribution)
        .filter(
            IndustryPatternContribution.pattern_id == pattern.id,
            IndustryPatternContribution.episode_key == row.episode_key,
        )
        .one_or_none()
    )
    if existing is not None:
        return pattern

    db.add(
        IndustryPatternContribution(
            pattern_id=pattern.id,
            hotel_id=row.hotel_id,
            memory_record_id=row.id,
            episode_key=row.episode_key,
        )
    )
    pattern.episode_count = int(pattern.episode_count or 0) + 1
    pattern.last_contributed_at = now
    pattern.updated_at = now
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        pattern = db.query(IndustryPattern).filter(IndustryPattern.pattern_key == key).one_or_none()
        return pattern
    db.refresh(pattern)
    return pattern
