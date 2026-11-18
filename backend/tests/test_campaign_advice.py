from datetime import date

from app.services.campaigns import compute_campaign_advice


def _rooms():
    class Room:
        def __init__(self, name, inventory):
            self.name = name
            self.inventory = inventory

    return [Room("豪华大床房", 20), Room("商务双床房", 16), Room("家庭房", 8)]


def _results(family_occ=0.50, avg_cut=-10.0):
    others = avg_cut
    return [
        {
            "room_name": "豪华大床房",
            "current_price": 688,
            "expected_impact": {"price_change_pct": others},
            "signals": {"recent_occupancy": 0.72},
        },
        {
            "room_name": "商务双床房",
            "current_price": 628,
            "expected_impact": {"price_change_pct": others},
            "signals": {"recent_occupancy": 0.68},
        },
        {
            "room_name": "家庭房",
            "current_price": 858,
            "expected_impact": {"price_change_pct": -6.0},
            "signals": {"recent_occupancy": family_occ},
        },
    ]


def test_skip_high_commission_when_already_cutting_and_limit_stay_midweek():
    advice = compute_campaign_advice(
        _rooms(),
        _results(),
        signals={"flags": ["rank_drop", "competitor_cut", "price_inversion"]},
        date_from=date(2026, 11, 16),
        date_to=date(2026, 11, 27),
    )
    by_id = {o["campaign_id"]: o for o in advice["options"]}
    assert advice["recommend_join"] is False
    assert advice["recommendation"] == "do_not_join"
    assert advice["recommendation_label"]
    assert advice["reason"]
    assert advice["risk"]
    assert advice["alternative"]
    assert by_id["ctrip_daily_deal"]["recommendation"] == "do_not_join"
    assert by_id["ctrip_stay"]["recommendation"] == "join_limited"
    assert "家庭房" in by_id["ctrip_stay"]["scope"]
    assert "连住优惠" in advice["alternative"]
    assert advice["expected_impact"] is not None
    assert "occupancy_delta_pct" in advice["expected_impact"]


def test_stay_discount_not_offered_on_weekend_only_window():
    advice = compute_campaign_advice(
        _rooms(),
        _results(),
        signals={"flags": ["rank_drop", "competitor_cut"]},
        date_from=date(2026, 11, 14),
        date_to=date(2026, 11, 15),
    )
    stay = next(o for o in advice["options"] if o["campaign_id"] == "ctrip_stay")
    assert stay["recommendation"] == "do_not_join"
    assert "周二" in stay["reason"] or "不适用" in stay["scope"] or "窗口" in stay["reason"]


def test_generate_kind_filters_and_advice_is_reviewable(client, auth_header):
    campaign_only = client.post(
        "/api/strategies/generate",
        headers=auth_header,
        json={"date_from": "2026-11-16", "date_to": "2026-11-27", "kind": "campaign"},
    )
    assert campaign_only.status_code == 200, campaign_only.text
    rows = campaign_only.json()["strategies"]
    assert len(rows) == 1
    s = rows[0]
    assert s["kind"] == "campaign"
    assert s["status"] == "pending_review"
    advice = s["campaign_advice"]
    assert advice["reason"] and advice["risk"] and advice["alternative"]
    assert advice["options"]
    assert {o["campaign_name"] for o in advice["options"]} >= {"携程天天特价", "连住优惠", "新客专享", "周末闪促"}
    assert s["confidence"] > 0
    assert s["expected_impact"]
    assert any(p["node"] == "human_review" for p in s["path"])

    pricing_only = client.post(
        "/api/strategies/generate",
        headers=auth_header,
        json={"date_from": "2026-11-16", "date_to": "2026-11-27", "kind": "pricing"},
    )
    assert pricing_only.status_code == 200
    kinds = {row["kind"] for row in pricing_only.json()["strategies"]}
    assert kinds == {"pricing"}

    catalog = client.get("/api/campaigns", headers=auth_header)
    assert catalog.status_code == 200
    body = catalog.json()
    assert len(body["campaigns"]) == 4
    assert "不会向 OTA 报名" in body["note"]
