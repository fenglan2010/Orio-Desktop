"""Post-decision impact attribution (Phase IV increment 3).

Compares later packed KPIs with a decision-time baseline for the same hotel,
stay-date window, and room type when known. Observations are deltas versus
that baseline (or versus a recorded proposal scenario). They are not YoY,
not a forecast, and not proven causal revenue.
"""

from __future__ import annotations

import logging

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import DailyMetric, MemoryRecord, RoomType, Strategy
from app.services.kpis import pack_metrics

log = logging.getLogger(__name__)

OUTCOME_NOT_MEASURED = "not_measured"
OUTCOME_PARTIAL = "partially_measured"
OUTCOME_MEASURED = "measured"

MEASURABLE_STATUSES = frozenset(
    {"executed", "execution_partial", "rejected", "rolled_back", "execution_failed"}
)
MEASURABLE_KINDS = frozenset({"pricing", "campaign"})

LABEL_BASELINE = "对照为决策时的本店基线，不是上年同期，也不是经因果识别后的增量收入。"
LABEL_REALIZED = "已实现入住日：相对决策时基线的观察变化，不是因果收入。"
LABEL_OTB = "在手订单变化 / OTB pickup（尚未实现的入住日，不是已实现客房收入，也不是预测）。"
LABEL_IN_PROGRESS = "今日为在途经营日，不计入已实现切片。"
LABEL_SCENARIO = "建议情景与观察值对照，不是评分，也不是承诺兑现。"
LABEL_IMPACT = "相对决策时基线的观察变化，不是因果收入，也不是上年同期。"

OBSERVATION_TITLES = {
    "rejected": "否决后实际发生了什么",
    "rolled_back": "回滚后实际发生了什么",
    "execution_failed": "执行失败后实际发生了什么",
}
OBSERVATION_KINDS = {
    "executed": "after_change",
    "execution_partial": "after_change",
    "rejected": "after_no_change",
    "rolled_back": "after_rollback",
    "execution_failed": "after_failed_write",
}
OBSERVATION_LABELS = {
    "rejected": "这是人审否决、价格未被改动之后的观察路径，不是因果收入，也不是上年同期。",
    "rolled_back": "这是回滚之后的观察路径，不是因果收入，也不是上年同期。",
    "execution_failed": "这是执行失败、写入可能未生效之后的观察路径，不是因果收入，也不是上年同期。",
}


def observation_title(status: str, action_kind: str = "pricing") -> str:
    terminal = (status or "").strip()
    if terminal in OBSERVATION_TITLES:
        return OBSERVATION_TITLES[terminal]
    if (action_kind or "") == "campaign":
        return "这次活动实际带来了什么"
    return "这次调价实际带来了什么"


def observation_copy(status: str, action_kind: str = "pricing") -> dict[str, Any]:
    terminal = (status or "").strip()
    return {
        "terminal_status": terminal,
        "observation_kind": OBSERVATION_KINDS.get(terminal, "after_disposition"),
        "title": observation_title(terminal, action_kind),
        "label": OBSERVATION_LABELS.get(terminal, LABEL_IMPACT),
    }


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _pct_change(now: float, prev: float) -> float:
    if not prev:
        return 0.0
    return round((now - prev) / prev * 100, 1)


def _pp_change(now: float, prev: float) -> float:
    return round((now - prev) * 100, 1)


def observed_delta(current: dict | None, baseline: dict | None) -> dict[str, Any]:
    now = current or {}
    prev = baseline or {}
    return {
        "revenue_abs": int(round(float(now.get("revenue") or 0) - float(prev.get("revenue") or 0))),
        "revenue_pct": _pct_change(float(now.get("revenue") or 0), float(prev.get("revenue") or 0)),
        "occupancy_pp": _pp_change(float(now.get("occupancy") or 0), float(prev.get("occupancy") or 0)),
        "occupancy_pct": _pct_change(float(now.get("occupancy") or 0), float(prev.get("occupancy") or 0)),
        "adr_abs": round(float(now.get("adr") or 0) - float(prev.get("adr") or 0), 1),
        "adr_pct": _pct_change(float(now.get("adr") or 0), float(prev.get("adr") or 0)),
        "revpar_abs": round(float(now.get("revpar") or 0) - float(prev.get("revpar") or 0), 1),
        "revpar_pct": _pct_change(float(now.get("revpar") or 0), float(prev.get("revpar") or 0)),
        "sold_rooms_abs": int(round(float(now.get("sold_rooms") or 0) - float(prev.get("sold_rooms") or 0))),
        "available_rooms_abs": int(
            round(float(now.get("available_rooms") or 0) - float(prev.get("available_rooms") or 0))
        ),
        "orders_abs": int(round(float(now.get("orders") or 0) - float(prev.get("orders") or 0))),
        "pageviews_abs": int(round(float(now.get("pageviews") or 0) - float(prev.get("pageviews") or 0))),
        "conversion_pp": _pp_change(float(now.get("conversion_rate") or 0), float(prev.get("conversion_rate") or 0)),
        "conversion_pct": _pct_change(float(now.get("conversion_rate") or 0), float(prev.get("conversion_rate") or 0)),
    }


def pack_stored_kpis(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows = []
    for item in items:
        kpis = (item or {}).get("kpis") if isinstance(item, dict) else None
        if not kpis:
            continue
        rows.append(
            SimpleNamespace(
                revenue=kpis.get("revenue") or 0,
                sold_rooms=kpis.get("sold_rooms") or 0,
                available_rooms=kpis.get("available_rooms") or 0,
                bookings=kpis.get("orders") or 0,
                clicks=kpis.get("pageviews") or 0,
                impressions=kpis.get("impressions") or 0,
                search_rank=kpis.get("search_rank") or 0,
            )
        )
    if not rows:
        return None
    return pack_metrics(rows)


def _copy_scenario(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value:
        return None
    # Only persist fields that were already stored on the proposal. No invented scores.
    out: dict[str, Any] = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if any(token in lowered for token in ("yoy", "year_ago", "accuracy", "revenue_lift", "caused")):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            out[str(key)] = item
        elif isinstance(item, dict):
            nested = _copy_scenario(item)
            if nested:
                out[str(key)] = nested
    return out or None


def resolve_scope(state: dict | None = None, payload: dict | None = None, strategy: Strategy | None = None) -> dict[str, Any]:
    state = state or {}
    payload = payload or {}
    strat = state.get("strategy") if isinstance(state.get("strategy"), dict) else {}
    proposed = payload.get("proposed_action") if isinstance(payload.get("proposed_action"), dict) else {}
    date_from = _as_date(
        payload.get("date_from")
        or state.get("date_from")
        or strat.get("date_from")
        or (strategy.date_from if strategy is not None else None)
    )
    date_to = _as_date(
        payload.get("date_to")
        or state.get("date_to")
        or strat.get("date_to")
        or (strategy.date_to if strategy is not None else None)
    )
    room_type_id = _as_int(
        payload.get("room_type_id")
        or proposed.get("room_type_id")
        or state.get("room_type_id")
        or strat.get("room_type_id")
        or (strategy.room_type_id if strategy is not None else None)
    )
    room_name = (
        payload.get("room_name")
        or proposed.get("room_name")
        or strat.get("room_name")
        or ""
    )
    scenario = _copy_scenario(
        payload.get("recorded_scenario")
        or payload.get("expected_impact")
        or strat.get("expected_impact")
        or (getattr(strategy, "expected_impact", None) if strategy is not None else None)
        or ((strat.get("campaign_advice") or {}).get("expected_impact") if isinstance(strat.get("campaign_advice"), dict) else None)
    )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "room_type_id": room_type_id,
        "room_name": str(room_name or ""),
        "recorded_scenario": scenario,
    }


def _strategy_row(db: Session, state: dict, payload: dict | None = None) -> Strategy | None:
    payload = payload or {}
    strategy_id = state.get("strategy_id") or payload.get("strategy_id")
    if strategy_id is None and isinstance(state.get("strategy"), dict):
        strategy_id = state["strategy"].get("id")
    if strategy_id is None:
        return None
    row = db.get(Strategy, int(strategy_id))
    hotel_id = _as_int(state.get("hotel_id") or payload.get("hotel_id"))
    if row is None:
        return None
    if hotel_id and row.hotel_id != hotel_id:
        return None
    return row


def load_metric_rows(
    db: Session,
    hotel_id: int,
    date_from: date,
    date_to: date,
    room_type_id: int | None = None,
) -> list[DailyMetric]:
    query = db.query(DailyMetric).filter(
        DailyMetric.hotel_id == int(hotel_id),
        DailyMetric.date >= date_from,
        DailyMetric.date <= date_to,
    )
    if room_type_id is not None:
        query = query.filter(DailyMetric.room_type_id == int(room_type_id))
    return list(query.order_by(DailyMetric.date.asc(), DailyMetric.room_type_id.asc()).all())


def _day_status(day: date, rows: list[DailyMetric], today: date) -> str:
    if not rows:
        return "missing"
    is_future = any(bool(getattr(row, "is_future", False)) for row in rows)
    if day < today and not is_future:
        return "realized"
    if day == today and not is_future:
        return "in_progress"
    return "otb"


def build_daily_facts(
    date_from: date,
    date_to: date,
    rows: list[DailyMetric],
    today: date,
) -> list[dict[str, Any]]:
    by_date: dict[date, list[DailyMetric]] = {}
    for row in rows:
        by_date.setdefault(row.date, []).append(row)
    facts: list[dict[str, Any]] = []
    day = date_from
    while day <= date_to:
        group = by_date.get(day) or []
        status = _day_status(day, group, today)
        item: dict[str, Any] = {
            "date": day.isoformat(),
            "status": status,
            "is_future": any(bool(getattr(row, "is_future", False)) for row in group) if group else (day > today),
        }
        item["kpis"] = pack_metrics(group) if group else None
        facts.append(item)
        day += timedelta(days=1)
    return facts


def _slice_from_daily(daily: list[dict[str, Any]], status: str) -> dict[str, Any] | None:
    matched = [item for item in daily if item.get("status") == status and item.get("kpis")]
    packed = pack_stored_kpis(matched)
    if not packed:
        return None
    dates = [item["date"] for item in matched]
    return {
        "date_from": dates[0],
        "date_to": dates[-1],
        "stay_dates": dates,
        "kpis": packed,
    }


def snapshot_baseline(
    db: Session,
    hotel_id: int,
    *,
    date_from: date | None,
    date_to: date | None,
    room_type_id: int | None = None,
    room_name: str = "",
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    baseline: dict[str, Any] = {
        "captured_at": today.isoformat(),
        "date_from": _iso(date_from),
        "date_to": _iso(date_to),
        "room_type_id": int(room_type_id) if room_type_id is not None else None,
        "room_name": room_name or "",
        "metrics_available": False,
        "daily": [],
        "realized": None,
        "otb": None,
        "in_progress": None,
        "note": LABEL_BASELINE,
    }
    if date_from is None or date_to is None:
        baseline["missing_reason"] = "缺少入住日期范围，无法对照基线。"
        return baseline
    if date_to < date_from:
        baseline["missing_reason"] = "入住日期范围无效，无法对照基线。"
        return baseline
    if room_type_id is not None and not room_name:
        room = db.get(RoomType, int(room_type_id))
        if room is not None and room.hotel_id == hotel_id:
            baseline["room_name"] = room.name
    rows = load_metric_rows(db, hotel_id, date_from, date_to, room_type_id)
    daily = build_daily_facts(date_from, date_to, rows, today)
    baseline["daily"] = daily
    baseline["realized"] = _slice_from_daily(daily, "realized")
    baseline["otb"] = _slice_from_daily(daily, "otb")
    baseline["in_progress"] = _slice_from_daily(daily, "in_progress")
    if any(item.get("kpis") for item in daily):
        baseline["metrics_available"] = True
    else:
        baseline["missing_reason"] = "该入住窗口尚无可用客房指标。"
    return baseline


def attach_decision_baseline(db: Session, state: dict, payload: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Snapshot decision-time facts onto a freshly built episode payload. Does not measure."""
    kind = payload.get("action_kind") or ""
    if kind not in MEASURABLE_KINDS:
        return payload
    hotel_id = _as_int(state.get("hotel_id"))
    if not hotel_id:
        payload["baseline"] = {
            "metrics_available": False,
            "missing_reason": "缺少酒店范围，无法记录基线。",
            "note": LABEL_BASELINE,
        }
        return payload
    today = _as_date(today) or _as_date(state.get("as_of")) or date.today()
    strategy = _strategy_row(db, state, payload)
    scope = resolve_scope(state, payload, strategy)
    payload["baseline"] = snapshot_baseline(
        db,
        hotel_id,
        date_from=scope["date_from"],
        date_to=scope["date_to"],
        room_type_id=scope["room_type_id"],
        room_name=scope["room_name"],
        today=today,
    )
    if scope["recorded_scenario"]:
        payload["recorded_scenario"] = scope["recorded_scenario"]
    # Measurement happens later, after metrics update or stay dates move.
    payload["outcome"] = OUTCOME_NOT_MEASURED
    payload["impact"] = None
    return payload


def _index_daily(daily: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in daily or []:
        key = str(item.get("date") or "")
        if key:
            out[key] = item
    return out


def _paired_slice(
    baseline_daily: list[dict[str, Any]],
    current_daily: list[dict[str, Any]],
    status: str,
) -> dict[str, Any] | None:
    baseline_index = _index_daily(baseline_daily)
    paired_baseline: list[dict[str, Any]] = []
    paired_current: list[dict[str, Any]] = []
    for item in current_daily:
        if item.get("status") != status or not item.get("kpis"):
            continue
        prior = baseline_index.get(item["date"])
        if not prior or not prior.get("kpis"):
            continue
        paired_baseline.append(prior)
        paired_current.append(item)
    current_kpis = pack_stored_kpis(paired_current)
    baseline_kpis = pack_stored_kpis(paired_baseline)
    if not current_kpis or not baseline_kpis:
        return None
    dates = [item["date"] for item in paired_current]
    return {
        "date_from": dates[0],
        "date_to": dates[-1],
        "stay_dates": dates,
        "baseline_kpis": baseline_kpis,
        "current_kpis": current_kpis,
        "delta": observed_delta(current_kpis, baseline_kpis),
    }


def _window_realized(daily: list[dict[str, Any]], date_from: date, date_to: date, today: date) -> bool:
    if date_to >= today:
        return False
    expected = (date_to - date_from).days + 1
    if expected <= 0:
        return False
    if len(daily) < expected:
        return False
    return all(item.get("status") == "realized" for item in daily)


def _scenario_comparison(
    recorded: dict[str, Any] | None,
    realized: dict[str, Any] | None,
    otb: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not recorded:
        return None
    observed: dict[str, Any] = {}
    source = "realized" if realized else ("otb_pickup" if otb else None)
    slice_delta = (realized or otb or {}).get("delta") if (realized or otb) else None
    if not slice_delta:
        return {
            "label": LABEL_SCENARIO,
            "recorded_scenario": recorded,
            "observed": None,
            "compared_slice": None,
            "note": "已记录建议情景，但尚无可用的观察切片可对照。",
        }
    if "occupancy_delta_pct" in recorded:
        observed["occupancy_delta_pct"] = slice_delta.get("occupancy_pp")
    if "adr_delta_pct" in recorded:
        observed["adr_delta_pct"] = slice_delta.get("adr_pct")
    if "revenue_delta" in recorded:
        if source == "realized":
            observed["revenue_delta"] = slice_delta.get("revenue_abs")
        # Do not treat OTB packed revenue as earned revenue versus the scenario.
    if "price_change_pct" in recorded:
        # Price recommendation is not re-derived here; keep the recorded field only.
        pass
    note = recorded.get("note") if isinstance(recorded.get("note"), str) else ""
    return {
        "label": LABEL_SCENARIO,
        "recorded_scenario": recorded,
        "observed": observed or None,
        "compared_slice": "已实现入住日" if source == "realized" else "在手订单变化",
        "note": note or "建议情景为提出时的启发式测算；观察值为相对决策时基线的变化。",
    }


def compare_to_baseline(
    baseline: dict[str, Any] | None,
    current_daily: list[dict[str, Any]],
    *,
    today: date,
    recorded_scenario: dict[str, Any] | None = None,
    terminal_status: str = "",
    action_kind: str = "pricing",
) -> tuple[str, dict[str, Any] | None]:
    baseline = baseline or {}
    date_from = _as_date(baseline.get("date_from"))
    date_to = _as_date(baseline.get("date_to"))
    if date_from is None or date_to is None:
        return OUTCOME_NOT_MEASURED, None
    if not baseline.get("metrics_available") and not any(item.get("kpis") for item in (baseline.get("daily") or [])):
        return OUTCOME_NOT_MEASURED, None
    realized = _paired_slice(baseline.get("daily") or [], current_daily, "realized")
    otb = _paired_slice(baseline.get("daily") or [], current_daily, "otb")
    if realized:
        realized = dict(realized)
        realized["label"] = LABEL_REALIZED
    if otb:
        otb = dict(otb)
        otb["label"] = LABEL_OTB
    if not realized and not otb:
        return OUTCOME_NOT_MEASURED, None
    fully_realized = _window_realized(current_daily, date_from, date_to, today)
    outcome = OUTCOME_MEASURED if fully_realized and realized else OUTCOME_PARTIAL
    copy = observation_copy(terminal_status, action_kind)
    notes = [copy["label"]]
    if any(item.get("status") == "in_progress" for item in current_daily):
        notes.append(LABEL_IN_PROGRESS)
    if fully_realized and any(item.get("status") == "realized" and not item.get("kpis") for item in current_daily):
        notes.append("入住窗口已过，但部分日期仍无客房指标，这些日期未编入观察值。")
    impact: dict[str, Any] = {
        "compared_at": today.isoformat(),
        "terminal_status": copy["terminal_status"],
        "observation_kind": copy["observation_kind"],
        "title": copy["title"],
        "label": copy["label"],
        "realized": realized,
        "otb_pickup": otb,
        "notes": notes,
    }
    scenario = _scenario_comparison(recorded_scenario, realized, otb)
    if scenario:
        impact["scenario_comparison"] = scenario
    return outcome, impact


def measure_episode(
    db: Session,
    row: MemoryRecord,
    *,
    today: date | None = None,
    commit: bool = True,
) -> bool:
    """Update outcome/impact on one episode. Never overwrites the decision-time baseline."""
    today = today or date.today()
    if row.hotel_id is None:
        return False
    if (row.approval_status or "") not in MEASURABLE_STATUSES:
        return False
    if (row.action_kind or "") not in MEASURABLE_KINDS:
        return False
    payload = dict(row.payload or {})
    baseline = payload.get("baseline")
    if not isinstance(baseline, dict):
        return False
    date_from = _as_date(baseline.get("date_from") or payload.get("date_from"))
    date_to = _as_date(baseline.get("date_to") or payload.get("date_to"))
    if date_from is None or date_to is None:
        return False
    room_type_id = _as_int(baseline.get("room_type_id") or payload.get("room_type_id"))
    rows = load_metric_rows(db, row.hotel_id, date_from, date_to, room_type_id)
    current_daily = build_daily_facts(date_from, date_to, rows, today)
    recorded = _copy_scenario(payload.get("recorded_scenario") or payload.get("expected_impact"))
    outcome, impact = compare_to_baseline(
        baseline,
        current_daily,
        today=today,
        recorded_scenario=recorded,
        terminal_status=row.approval_status or payload.get("status") or "",
        action_kind=row.action_kind or payload.get("action_kind") or "pricing",
    )
    if outcome == OUTCOME_NOT_MEASURED or impact is None:
        return False
    frozen_baseline = dict(baseline)
    payload["baseline"] = frozen_baseline
    payload["outcome"] = outcome
    payload["impact"] = impact
    if recorded and "recorded_scenario" not in payload:
        payload["recorded_scenario"] = recorded
    row.payload = payload
    flag_modified(row, "payload")
    if commit:
        db.commit()
        db.refresh(row)
    return True


def measure_hotel_episodes(db: Session, hotel_id: int, *, today: date | None = None) -> dict[str, Any]:
    """Re-read metrics for one hotel's measurable episodes and update the same rows."""
    today = today or date.today()
    rows = (
        db.query(MemoryRecord)
        .filter(
            MemoryRecord.hotel_id == int(hotel_id),
            MemoryRecord.kind == "decision_episode",
            MemoryRecord.approval_status.in_(tuple(MEASURABLE_STATUSES)),
            MemoryRecord.action_kind.in_(tuple(MEASURABLE_KINDS)),
        )
        .order_by(MemoryRecord.id.asc())
        .all()
    )
    updated = 0
    skipped = 0
    for row in rows:
        if measure_episode(db, row, today=today, commit=False):
            updated += 1
        else:
            skipped += 1
    db.commit()
    try:
        from app.services.knowledge_graph import rebuild_hotel_graph

        rebuild_hotel_graph(db, hotel_id)
    except Exception:
        log.exception("knowledge graph rebuild failed hotel_id=%s", hotel_id)
        db.rollback()
    try:
        from app.services.rl import fit_hotel_policy

        fit_hotel_policy(db, hotel_id)
    except Exception:
        log.exception("offline policy fit failed hotel_id=%s", hotel_id)
        db.rollback()
    return {
        "hotel_id": int(hotel_id),
        "as_of": today.isoformat(),
        "checked": len(rows),
        "updated": updated,
        "unchanged": skipped,
    }


def measure_all_hotels(db: Session, *, today: date | None = None) -> dict[str, Any]:
    from app.models import Hotel

    today = today or date.today()
    hotels = db.query(Hotel).order_by(Hotel.id.asc()).all()
    runs = [measure_hotel_episodes(db, hotel.id, today=today) for hotel in hotels]
    return {"as_of": today.isoformat(), "hotels": len(hotels), "runs": runs}
