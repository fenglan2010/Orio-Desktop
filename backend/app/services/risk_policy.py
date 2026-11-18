"""Hotel execution policy and auto-execute gates for Phase II increment 1.

Default remains Phase I: every pricing, campaign and review reply waits for
human review. Auto-execute is opt-in and only low-risk actions that also pass
hard rules may skip the waiting step.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import SystemSetting

POLICY_KEY = "execution_policy"
AUTO_EXECUTE_ACTOR = "system:auto_execute"
ALLOWED_MAX_RISK = ("low",)
HARD_PRICE_CHANGE_FLOOR_PCT = 15.0
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
ROLLBACK_POLICIES = ("manual", "auto_on_write_failure")
WRITE_MODES = ("sandbox", "live")

# Official nationwide public-holiday rest days (放假调休), not ordinary weekends
# and not the compensating workdays (上班).
# 2026: 国务院办公厅关于2026年部分节假日安排的通知（国办发明电〔2025〕7号）
# 2027: 国务院办公厅关于2027年部分节假日安排的通知（国办发明电〔2026〕10号）
_HOLIDAY_RANGES: tuple[tuple[str, str, str], ...] = (
    ("2026-01-01", "2026-01-03", "元旦"),
    ("2026-02-15", "2026-02-23", "春节"),
    ("2026-04-04", "2026-04-06", "清明节"),
    ("2026-05-01", "2026-05-05", "劳动节"),
    ("2026-06-19", "2026-06-21", "端午节"),
    ("2026-09-25", "2026-09-27", "中秋节"),
    ("2026-10-01", "2026-10-07", "国庆节"),
    ("2027-01-01", "2027-01-03", "元旦"),
    ("2027-02-05", "2027-02-13", "春节"),
    ("2027-04-03", "2027-04-05", "清明节"),
    ("2027-05-01", "2027-05-05", "劳动节"),
    ("2027-06-09", "2027-06-09", "端午节"),
    ("2027-09-15", "2027-09-15", "中秋节"),
    ("2027-10-01", "2027-10-07", "国庆节"),
)


def _parse_iso(value: date | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def iter_chinese_holidays(start: date, end: date) -> list[dict[str, str]]:
    """Return official rest days that overlap [start, end], inclusive."""
    if end < start:
        start, end = end, start
    hits: list[dict[str, str]] = []
    for raw_from, raw_to, name in _HOLIDAY_RANGES:
        lo = date.fromisoformat(raw_from)
        hi = date.fromisoformat(raw_to)
        if lo <= end and hi >= start:
            overlap_lo = max(lo, start)
            overlap_hi = min(hi, end)
            hits.append(
                {
                    "name": name,
                    "holiday_from": lo.isoformat(),
                    "holiday_to": hi.isoformat(),
                    "overlap_from": overlap_lo.isoformat(),
                    "overlap_to": overlap_hi.isoformat(),
                }
            )
    return hits


def is_chinese_holiday(day: date) -> bool:
    return bool(iter_chinese_holidays(day, day))


@dataclass
class ExecutionPolicy:
    auto_execute_enabled: bool = False
    auto_execute_max_risk: str = "low"
    holiday_requires_review: bool = True
    price_change_human_threshold_pct: float = HARD_PRICE_CHANGE_FLOOR_PCT
    rollback_policy: str = "manual"
    write_mode: str = "sandbox"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "auto_execute_enabled": bool(self.auto_execute_enabled),
            "auto_execute_max_risk": self.auto_execute_max_risk,
            "holiday_requires_review": bool(self.holiday_requires_review),
            "price_change_human_threshold_pct": float(self.price_change_human_threshold_pct),
            "rollback_policy": self.rollback_policy,
            "write_mode": self.write_mode,
            "hard_rules": {
                "max_auto_risk": "low",
                "price_change_floor_pct": HARD_PRICE_CHANGE_FLOOR_PCT,
                "review_replies_always_human": True,
                "campaign_join_at_least_medium": True,
            },
        }

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


def default_execution_policy() -> ExecutionPolicy:
    return ExecutionPolicy()


def validate_execution_policy(payload: dict[str, Any] | None, base: ExecutionPolicy | None = None) -> ExecutionPolicy:
    current = base or default_execution_policy()
    data = payload or {}
    enabled = current.auto_execute_enabled
    if "auto_execute_enabled" in data and data["auto_execute_enabled"] is not None:
        enabled = bool(data["auto_execute_enabled"])

    max_risk = current.auto_execute_max_risk
    if "auto_execute_max_risk" in data and data["auto_execute_max_risk"] is not None:
        max_risk = str(data["auto_execute_max_risk"]).strip().lower()
    if max_risk not in ALLOWED_MAX_RISK:
        raise ValueError("本增量自动执行最高风险仅允许 low，中高风险必须人审")

    holiday = current.holiday_requires_review
    if "holiday_requires_review" in data and data["holiday_requires_review"] is not None:
        holiday = bool(data["holiday_requires_review"])

    threshold = current.price_change_human_threshold_pct
    if "price_change_human_threshold_pct" in data and data["price_change_human_threshold_pct"] is not None:
        try:
            threshold = float(data["price_change_human_threshold_pct"])
        except (TypeError, ValueError) as exc:
            raise ValueError("人审调价幅度阈值必须是数字") from exc
    if threshold <= 0 or threshold > HARD_PRICE_CHANGE_FLOOR_PCT:
        raise ValueError("人审调价幅度阈值须大于 0 且不得超过 15%")

    rollback = current.rollback_policy
    if "rollback_policy" in data and data["rollback_policy"] is not None:
        rollback = str(data["rollback_policy"]).strip()
    if rollback not in ROLLBACK_POLICIES:
        raise ValueError("回滚策略仅支持 manual 或 auto_on_write_failure")

    write_mode = current.write_mode
    if "write_mode" in data and data["write_mode"] is not None:
        write_mode = str(data["write_mode"]).strip().lower()
    if write_mode not in WRITE_MODES:
        raise ValueError("写入模式仅支持 sandbox 或 live")

    return ExecutionPolicy(
        auto_execute_enabled=enabled,
        auto_execute_max_risk=max_risk,
        holiday_requires_review=holiday,
        price_change_human_threshold_pct=threshold,
        rollback_policy=rollback,
        write_mode=write_mode,
    )


def _policy_key(hotel_id: int | None, db: Session | None = None) -> str | None:
    if hotel_id is None:
        from app.services.tenancy import session_hotel_id

        hotel_id = session_hotel_id(db)
    if hotel_id is None:
        return None
    from app.services.tenancy import policy_setting_key

    return policy_setting_key(int(hotel_id))


def load_execution_policy(db: Session | None = None, hotel_id: int | None = None) -> ExecutionPolicy:
    if db is None:
        return default_execution_policy()
    key = _policy_key(hotel_id, db)
    if key is None:
        return default_execution_policy()
    row = db.get(SystemSetting, key)
    if row is None or not (row.value or "").strip():
        return default_execution_policy()
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return default_execution_policy()
    if not isinstance(raw, dict):
        return default_execution_policy()
    try:
        return validate_execution_policy(raw, default_execution_policy())
    except ValueError:
        return default_execution_policy()


def save_execution_policy(db: Session, policy: ExecutionPolicy, hotel_id: int | None = None) -> ExecutionPolicy:
    key = _policy_key(hotel_id, db)
    if key is None:
        raise ValueError("保存执行策略必须指定酒店")
    payload = json.dumps(policy.snapshot(), ensure_ascii=False)
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=payload))
    else:
        row.value = payload
    db.commit()
    return policy


def _norm_kind(kind: str | None) -> str:
    value = (kind or "pricing").lower().strip()
    if value in {"pricing_strategy", "pricing"}:
        return "pricing"
    if value in {"review_reply", "review"}:
        return "review_reply"
    if value in {"campaign", "campaign_advice"}:
        return "campaign"
    return value


def _price_change_pct(strategy_payload: dict | None, strategy: Any | None = None) -> float:
    payload = strategy_payload or {}
    impact = payload.get("expected_impact") or {}
    if strategy is not None and not impact:
        impact = getattr(strategy, "expected_impact", None) or {}
    value = impact.get("price_change_pct")
    changes = []
    if value is not None:
        try:
            changes.append(abs(float(value)))
        except (TypeError, ValueError):
            pass
    current = payload.get("current_price")
    recommended = payload.get("recommended_price")
    if strategy is not None:
        current = current if current is not None else getattr(strategy, "current_price", None)
        recommended = recommended if recommended is not None else getattr(strategy, "recommended_price", None)
    try:
        if current and recommended is not None and float(current) != 0:
            changes.append(abs((float(recommended) - float(current)) / float(current) * 100.0))
    except (TypeError, ValueError):
        pass
    for day in payload.get("daily_breakdown") or []:
        pct = day.get("price_change_pct")
        if pct is None and day.get("current_price") and day.get("recommended_price"):
            try:
                cur = float(day["current_price"])
                if cur:
                    pct = (float(day["recommended_price"]) - cur) / cur * 100.0
            except (TypeError, ValueError, KeyError):
                pct = None
        if pct is not None:
            try:
                changes.append(abs(float(pct)))
            except (TypeError, ValueError):
                continue
    return max(changes) if changes else 0.0


def _window(state: dict | None, strategy: Any | None, payload: dict | None) -> tuple[date | None, date | None]:
    payload = payload or {}
    start = (
        (payload.get("date_from") if payload else None)
        or (state or {}).get("date_from")
        or (getattr(strategy, "date_from", None) if strategy is not None else None)
    )
    end = (
        (payload.get("date_to") if payload else None)
        or (state or {}).get("date_to")
        or (getattr(strategy, "date_to", None) if strategy is not None else None)
    )
    return _parse_iso(start), _parse_iso(end)


def _campaign_join_reasons(payload: dict | None, strategy: Any | None) -> list[str]:
    advice = (payload or {}).get("campaign_advice")
    if advice is None and strategy is not None:
        advice = getattr(strategy, "campaign_advice", None)
    if not isinstance(advice, dict):
        return []
    reasons: list[str] = []
    options = list(advice.get("options") or [])
    recs = {advice.get("recommendation")}
    recs.update(o.get("recommendation") for o in options)
    if advice.get("recommend_join") or "join" in recs:
        reasons.append("建议报名参加活动，必须人审")
    if "join_limited" in recs:
        reasons.append("建议限范围参加活动，必须人审")
    high = any(
        (o.get("recommendation") in {"join", "join_limited"} and (o.get("commission_uplift_pp") or 0) >= 6)
        for o in options
    )
    if not high and (advice.get("recommendation") in {"join", "join_limited"}):
        high = (advice.get("commission_uplift_pp") or 0) >= 6
    if high:
        reasons.append("高佣金活动方案至少为中风险，必须人审")
    return reasons


@dataclass
class AutoExecuteDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "medium"
    kind: str = "pricing"
    price_change_pct: float = 0.0
    holidays: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "policy_snapshot": self.policy_snapshot,
            "risk_level": self.risk_level,
            "kind": self.kind,
            "price_change_pct": self.price_change_pct,
            "holidays": self.holidays,
        }

    @property
    def reason(self) -> str:
        return "；".join(self.reasons) if self.reasons else ""


def may_auto_execute(
    *,
    kind: str | None,
    risk_level: str | None,
    policy: ExecutionPolicy | None = None,
    strategy_payload: dict | None = None,
    state: dict | None = None,
    strategy: Any | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
) -> AutoExecuteDecision:
    """Return whether a generated strategy may skip waiting for a human."""
    policy = policy or default_execution_policy()
    kind_n = _norm_kind(kind or (state or {}).get("kind") or (getattr(strategy, "kind", None) if strategy else None))
    risk = (risk_level or (state or {}).get("risk_level") or (getattr(strategy, "risk_level", None) if strategy else None) or "medium").lower()
    payload = strategy_payload or (state or {}).get("strategy") or {}
    start, end = _window(state, strategy, payload)
    if date_from:
        start = _parse_iso(date_from)
    if date_to:
        end = _parse_iso(date_to)
    if start and not end:
        end = start
    if end and not start:
        start = end

    change = _price_change_pct(payload, strategy)
    holidays = iter_chinese_holidays(start, end) if start and end else []
    reasons: list[str] = []

    if not policy.auto_execute_enabled:
        reasons.append("自动执行未开启，所有策略保持人审")

    if kind_n == "review_reply":
        reasons.append("评价回复本增量必须人审，不可自动发布")

    max_risk = policy.auto_execute_max_risk if policy.auto_execute_max_risk in RISK_ORDER else "low"
    if RISK_ORDER.get(risk, 99) > RISK_ORDER[max_risk]:
        reasons.append(f"风险等级为{risk}，超过自动执行上限{max_risk}，必须人审")

    effective_threshold = min(float(policy.price_change_human_threshold_pct), HARD_PRICE_CHANGE_FLOOR_PCT)
    if kind_n == "pricing" and change >= effective_threshold:
        reasons.append(f"调价幅度 {change:.1f}% 达到人审阈值 {effective_threshold:.0f}%，必须人审")

    if policy.holiday_requires_review and holidays:
        labels = "、".join(f"{h['name']}（{h['overlap_from']} 至 {h['overlap_to']}）" for h in holidays)
        reasons.append(f"入住窗口覆盖法定节假日：{labels}，必须人审")

    if kind_n == "campaign":
        reasons.extend(_campaign_join_reasons(payload, strategy))

    allowed = not reasons
    if allowed:
        reasons.append("低风险且通过执行策略，允许自动执行")

    return AutoExecuteDecision(
        allowed=allowed,
        reasons=reasons,
        policy_snapshot=policy.snapshot(),
        risk_level=risk,
        kind=kind_n,
        price_change_pct=round(change, 1),
        holidays=holidays,
    )
