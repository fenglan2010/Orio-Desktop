"""Interpretable campaign participation advice. The LLM does not decide join / skip."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

REC_JOIN = "join"
REC_LIMITED = "join_limited"
REC_SKIP = "do_not_join"

RECOMMENDATION_LABELS = {
    REC_JOIN: "建议参加",
    REC_LIMITED: "建议限范围试参加",
    REC_SKIP: "暂不建议参加",
}

# Pre-set demo catalog (not crawled). These are observable Ctrip-style programmes.
CAMPAIGN_CATALOG: list[dict[str, Any]] = [
    {
        "id": "ctrip_daily_deal",
        "name": "携程天天特价",
        "platform": "携程",
        "commission_uplift_pp": 8,
        "typical_discount_pct": 15,
        "stay_min_nights": 1,
        "dayparts": "all",
        "eligible": "all_rooms",
        "competitor_join_rate": 0.62,
        "description": "平台主推高曝光促销，佣金上浮明显，活动价受限更多，回滚灵活性低。",
    },
    {
        "id": "ctrip_stay",
        "name": "连住优惠",
        "platform": "携程",
        "commission_uplift_pp": 3,
        "typical_discount_pct": 8,
        "stay_min_nights": 2,
        "dayparts": "weekday",
        "eligible": "all_rooms",
        "competitor_join_rate": 0.28,
        "description": "连住两晚及以上优惠，佣金上浮较小，更适合周中去库存。",
    },
    {
        "id": "ctrip_newguest",
        "name": "新客专享",
        "platform": "携程",
        "commission_uplift_pp": 4,
        "typical_discount_pct": 10,
        "stay_min_nights": 1,
        "dayparts": "all",
        "eligible": "new_guest",
        "competitor_join_rate": 0.35,
        "description": "仅对新客展示的让利，影响面比全量闪促小，更偏获客而非清库存。",
    },
    {
        "id": "ctrip_weekend",
        "name": "周末闪促",
        "platform": "携程",
        "commission_uplift_pp": 5,
        "typical_discount_pct": 12,
        "stay_min_nights": 1,
        "dayparts": "weekend",
        "eligible": "all_rooms",
        "competitor_join_rate": 0.4,
        "description": "周五至周日短时特价，适合周末需求偏弱时拉动，旺季不必叠加。",
    },
]


def catalog_public() -> list[dict[str, Any]]:
    keys = (
        "id",
        "name",
        "platform",
        "commission_uplift_pp",
        "typical_discount_pct",
        "stay_min_nights",
        "dayparts",
        "eligible",
        "competitor_join_rate",
        "description",
    )
    return [{k: row[k] for k in keys} for row in CAMPAIGN_CATALOG]


def _daterange(start: date, end: date) -> list[date]:
    if end < start:
        start, end = end, start
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _has_dow(days: list[date], allowed: set[int]) -> bool:
    return any(d.weekday() in allowed for d in days)


def _mean(values: list[float], default: float = 0.0) -> float:
    return sum(values) / len(values) if values else default


def _label(rec: str) -> str:
    return RECOMMENDATION_LABELS.get(rec, rec)


def _soft_rooms(pricing_results: list[dict[str, Any]], threshold: float = 0.55) -> list[dict[str, Any]]:
    soft = []
    for row in pricing_results:
        occ = (row.get("signals") or {}).get("recent_occupancy")
        if occ is not None and occ < threshold:
            soft.append(row)
    return soft


def _avg_price_change_pct(pricing_results: list[dict[str, Any]]) -> float:
    cuts = [
        float((row.get("expected_impact") or {}).get("price_change_pct") or 0)
        for row in pricing_results
    ]
    return _mean(cuts, 0.0)


def _inventory(rooms: list[Any] | None, room_name: str) -> int:
    for room in rooms or []:
        name = getattr(room, "name", None) or (room.get("name") if isinstance(room, dict) else None)
        if name == room_name:
            return int(getattr(room, "inventory", None) or (room.get("inventory") if isinstance(room, dict) else 0) or 0)
    return 0


def _base_revenue(rooms: list[Any] | None, pricing_results: list[dict[str, Any]], days: int) -> float:
    total = 0.0
    for row in pricing_results:
        occ = float((row.get("signals") or {}).get("recent_occupancy") or 0.6)
        price = float(row.get("current_price") or 0)
        inv = _inventory(rooms, row.get("room_name") or "") or 8
        total += inv * days * price * occ
    return total


def _impact(
    *,
    occ_pp: float,
    adr_pct: float,
    base_rev: float,
    note: str,
) -> dict[str, Any]:
    # Heuristic only: occupancy lift and ADR movement both apply to the window revenue base.
    revenue_delta = int(base_rev * ((occ_pp / 100.0) * 0.7 + (adr_pct / 100.0)))
    return {
        "occupancy_delta_pct": round(occ_pp, 1),
        "adr_delta_pct": round(adr_pct, 1),
        "revenue_delta": revenue_delta,
        "note": note,
    }


def _option(
    camp: dict[str, Any],
    rec: str,
    *,
    scope: str,
    reason: str,
    risk: str,
    confidence: float,
    expected_impact: dict[str, Any],
) -> dict[str, Any]:
    return {
        "campaign_id": camp["id"],
        "campaign_name": camp["name"],
        "platform": camp["platform"],
        "recommendation": rec,
        "recommendation_label": _label(rec),
        "scope": scope,
        "reason": reason,
        "risk": risk,
        "commission_uplift_pp": camp["commission_uplift_pp"],
        "typical_discount_pct": camp["typical_discount_pct"],
        "stay_min_nights": camp["stay_min_nights"],
        "confidence": confidence,
        "expected_impact": expected_impact,
    }


def _evaluate_one(
    camp: dict[str, Any],
    *,
    pricing_results: list[dict[str, Any]],
    flags: set[str],
    days: list[date],
    rooms: list[Any] | None,
    avg_cut: float,
    soft: list[dict[str, Any]],
    base_rev: float,
) -> dict[str, Any]:
    cid = camp["id"]
    soft_names = "、".join(r.get("room_name") or "" for r in soft if r.get("room_name")) or "出租率偏低房型"
    confidence = 0.84 if {"rank_drop", "competitor_cut"} <= flags or "price_inversion" in flags else 0.76
    window_note = "启发式测算，仅供人审参考，不是承诺。"

    if cid == "ctrip_daily_deal":
        if avg_cut <= -5 or "price_inversion" in flags:
            return _option(
                camp,
                REC_SKIP,
                scope="暂不建议全房型参加",
                reason=(
                    f"本窗口已准备用自主调价（窗口均价大约变动 {avg_cut:.1f}%）换回排名。"
                    f"再叠加「{camp['name']}」约 {camp['typical_discount_pct']}% 活动价、佣金上浮约 "
                    f"{camp['commission_uplift_pp']} 个百分点，会把已经收窄的价格带利润进一步削薄，且活动价更难灵活回滚。"
                ),
                risk="不参加时，部分已报名竞对可能继续占据列表靠前位置，曝光修复会更慢。",
                confidence=confidence,
                expected_impact=_impact(
                    occ_pp=0.0,
                    adr_pct=0.0,
                    base_rev=base_rev,
                    note="按建议不参加该高佣金活动，营收影响主要来自并行的自主调价，不在此重复计算。" + window_note,
                ),
            )
        if all(float((r.get("signals") or {}).get("recent_occupancy") or 1) < 0.45 for r in pricing_results) and pricing_results:
            return _option(
                camp,
                REC_JOIN,
                scope="全房型，观察窗口内可参加",
                reason=(
                    f"各房型近期出租率均偏低，且本轮没有大幅度自主降价。"
                    f"此时「{camp['name']}」的曝光可能换来可观的需求，但仍须人审确认佣金与活动价约束可接受。"
                ),
                risk="活动期价格与库存受限，若需求回升较快，可能错过更好的自主定价窗口。",
                confidence=0.72,
                expected_impact=_impact(
                    occ_pp=4.5,
                    adr_pct=-float(camp["typical_discount_pct"]),
                    base_rev=base_rev,
                    note="若全量参加，出租率或有提升，但 ADR 与净费率会明显下降。" + window_note,
                ),
            )
        return _option(
            camp,
            REC_SKIP,
            scope="暂不建议全房型参加",
            reason=f"当前出租率与价格带尚不必用「{camp['name']}」这类高佣金活动换曝光。",
            risk="若竞对集中报名，列表位次可能短时落后。",
            confidence=confidence,
            expected_impact=_impact(occ_pp=0.0, adr_pct=0.0, base_rev=base_rev, note=window_note),
        )

    if cid == "ctrip_stay":
        midweek = {1, 2, 3}  # Tue–Thu
        if not _has_dow(days, midweek):
            return _option(
                camp,
                REC_SKIP,
                scope="本观察窗口不适用",
                reason="本观察窗口几乎没有周二至周四，连住周中试参没有可落位的日期。",
                risk="若只用周末评估连住活动，容易得出偏乐观或偏悲观的结论。",
                confidence=0.8,
                expected_impact=_impact(occ_pp=0.0, adr_pct=0.0, base_rev=base_rev, note=window_note),
            )
        if avg_cut <= -10:
            return _option(
                camp,
                REC_SKIP,
                scope="本窗口暂不叠加",
                reason=f"自主调价幅度已经较大（约 {avg_cut:.1f}%），再叠加连住优惠容易重复让利。",
                risk="部分家庭客仍可能流向已报名连住的竞对。",
                confidence=confidence,
                expected_impact=_impact(occ_pp=0.0, adr_pct=0.0, base_rev=base_rev, note=window_note),
            )
        if soft:
            return _option(
                camp,
                REC_LIMITED,
                scope=f"{soft_names} · 周二至周四 · 连住{camp['stay_min_nights']}晚起",
                reason=(
                    f"{soft_names}近期出租率偏低，而「{camp['name']}」佣金上浮约 "
                    f"{camp['commission_uplift_pp']} 个百分点、折扣约 {camp['typical_discount_pct']}%，"
                    "适合作为小范围周中试参，而不是全房型跟进高佣金活动。"
                ),
                risk="试参房型在活动价下的利润更薄，若取消规则不严，可能占住可售库存。",
                confidence=confidence,
                expected_impact=_impact(
                    occ_pp=2.5,
                    adr_pct=-4.0,
                    base_rev=base_rev * 0.35,
                    note="仅按低出租率房型、周中连住估算。" + window_note,
                ),
            )
        return _option(
            camp,
            REC_SKIP,
            scope="暂不建议参加",
            reason="各房型出租率尚未低到需要用连住优惠换量。",
            risk="周中若突然走弱，可能需要再评估一次。",
            confidence=0.74,
            expected_impact=_impact(occ_pp=0.0, adr_pct=0.0, base_rev=base_rev, note=window_note),
        )

    if cid == "ctrip_newguest":
        if "rank_drop" in flags and avg_cut > -5 and "price_inversion" not in flags:
            return _option(
                camp,
                REC_LIMITED,
                scope="全房型 · 仅新客可见",
                reason="搜索排名偏弱，但本轮不必大幅自主降价，可用新客专享做小范围获客，避免全量让利。",
                risk="新客券对回头客与已有流量几乎无帮助，且可能拉低部分成交均价。",
                confidence=0.7,
                expected_impact=_impact(
                    occ_pp=1.5,
                    adr_pct=-3.0,
                    base_rev=base_rev * 0.25,
                    note="仅按新客份额粗估。" + window_note,
                ),
            )
        return _option(
            camp,
            REC_SKIP,
            scope="本窗口暂缓",
            reason="当前应先完成自主调价与差评处理，待排名企稳后再评估新客专享，以免把获客券叠在已经收窄的价格带上。",
            risk="短期获客节奏可能慢于已投放新客券的竞对。",
            confidence=confidence,
            expected_impact=_impact(occ_pp=0.0, adr_pct=0.0, base_rev=base_rev, note=window_note),
        )

    if cid == "ctrip_weekend":
        if not _has_dow(days, {4, 5, 6}):  # Fri–Sun
            return _option(
                camp,
                REC_SKIP,
                scope="本观察窗口不含周末",
                reason="本观察窗口不含周五至周日，周末闪促无法落位。",
                risk="无。",
                confidence=0.86,
                expected_impact=_impact(occ_pp=0.0, adr_pct=0.0, base_rev=base_rev, note=window_note),
            )
        weekend_soft = any(float((r.get("signals") or {}).get("recent_occupancy") or 1) < 0.6 for r in pricing_results)
        if weekend_soft and avg_cut > -8:
            return _option(
                camp,
                REC_LIMITED,
                scope=f"{soft_names if soft else '出租率偏低房型'} · 周五至周日",
                reason="周末需求偏弱，可用闪促做短时拉动；仍建议限制房型，避免旺季房型一起让利。",
                risk="周末活动价一旦放出，对已有高意向订单可能产生不必要的折扣。",
                confidence=0.7,
                expected_impact=_impact(
                    occ_pp=2.0,
                    adr_pct=-5.0,
                    base_rev=base_rev * 0.3,
                    note="仅按周末几天粗估。" + window_note,
                ),
            )
        return _option(
            camp,
            REC_SKIP,
            scope="暂不建议参加",
            reason="周末需求并不弱，不必再用闪促让利；把调整放在自主定价上更灵活。",
            risk="若临近周末突然掉量，需要重新评估。",
            confidence=confidence,
            expected_impact=_impact(occ_pp=0.0, adr_pct=0.0, base_rev=base_rev, note=window_note),
        )

    return _option(
        camp,
        REC_SKIP,
        scope="暂不评估",
        reason="该活动暂无适用规则。",
        risk="—",
        confidence=0.5,
        expected_impact=_impact(occ_pp=0.0, adr_pct=0.0, base_rev=base_rev, note=window_note),
    )


def compute_campaign_advice(
    rooms: list[Any] | None,
    pricing_results: list[dict[str, Any]],
    signals: dict[str, Any] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    today = date.today()
    start = date_from or today
    end = date_to or (today + timedelta(days=13))
    days = _daterange(start, end)
    flags = set((signals or {}).get("flags") or [])
    avg_cut = _avg_price_change_pct(pricing_results)
    soft = _soft_rooms(pricing_results)
    base_rev = _base_revenue(rooms, pricing_results, len(days) or 1)

    options = [
        _evaluate_one(
            camp,
            pricing_results=pricing_results,
            flags=flags,
            days=days,
            rooms=rooms,
            avg_cut=avg_cut,
            soft=soft,
            base_rev=base_rev,
        )
        for camp in CAMPAIGN_CATALOG
    ]

    # Headline is the platform's main programme; limited-join options stay in the same brief.
    primary = next((o for o in options if o["campaign_id"] == "ctrip_daily_deal"), options[0])
    limited = [o for o in options if o["recommendation"] == REC_LIMITED]
    joins = [o for o in options if o["recommendation"] == REC_JOIN]

    alternative_bits = []
    if primary["recommendation"] == REC_SKIP:
        alternative_bits.append("建议先执行人审后的自主调价并观察 5–7 天。")
    for item in limited:
        alternative_bits.append(f"可将「{item['campaign_name']}」作为限范围试参：{item['scope']}。")
    if not limited and not joins and primary["recommendation"] == REC_SKIP:
        alternative_bits.append("活动参与可在观察一周后再评估。")

    package_impact = primary["expected_impact"]
    if limited and not joins:
        package_impact = {
            "occupancy_delta_pct": limited[0]["expected_impact"]["occupancy_delta_pct"],
            "adr_delta_pct": limited[0]["expected_impact"]["adr_delta_pct"],
            "revenue_delta": sum(o["expected_impact"]["revenue_delta"] for o in limited),
            "note": "按建议执行：不参加高佣金主推活动，仅限范围试参。" + (limited[0]["expected_impact"].get("note") or ""),
        }

    confidence = max((o["confidence"] for o in options), default=0.76)
    advice = {
        "campaign_name": primary["campaign_name"],
        "recommend_join": primary["recommendation"] == REC_JOIN,
        "recommendation": primary["recommendation"],
        "recommendation_label": primary["recommendation_label"],
        "reason": primary["reason"],
        "risk": primary["risk"],
        "alternative": "".join(alternative_bits) or primary.get("reason", ""),
        "scope": primary["scope"],
        "confidence": confidence,
        "expected_impact": package_impact,
        "options": options,
    }
    return advice


def assess_campaign_risk(advice: dict[str, Any] | None, signals: dict[str, Any] | None = None) -> str:
    """Classify campaign risk. Join / high commission stays at least medium."""
    flags = set((signals or {}).get("flags") or [])
    options = (advice or {}).get("options") or []
    recs = {o.get("recommendation") for o in options}
    high_commission_join = any(
        o.get("recommendation") == REC_JOIN and (o.get("commission_uplift_pp") or 0) >= 6 for o in options
    )
    if high_commission_join and ("price_inversion" in flags or "rank_drop" in flags):
        return "high"
    if REC_JOIN in recs:
        return "medium"
    if REC_LIMITED in recs:
        return "medium"
    if "rank_drop" in flags:
        return "medium"
    return "low"
