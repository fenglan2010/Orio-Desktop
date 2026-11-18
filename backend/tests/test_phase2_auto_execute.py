from __future__ import annotations

from datetime import date, timedelta

from app.adapters.ota.official_write import OfficialWriteAdapter
from app.database import SessionLocal
from app.models import CompetitorSnapshot, DailyMetric, Review
from app.services.risk_policy import (
    ExecutionPolicy,
    default_execution_policy,
    iter_chinese_holidays,
    may_auto_execute,
    validate_execution_policy,
)


def _policy(client, headers, **fields):
    resp = client.put("/api/settings", headers=headers, json={"execution_policy": fields})
    return resp


def _calm_demo_market():
    """Remove rank-drop / inversion / negative-review so a pricing run can be genuinely low risk."""
    db = SessionLocal()
    try:
        for metric in db.query(DailyMetric).all():
            metric.search_rank = 8
        list_by_room: dict[int, float] = {}
        for metric in db.query(DailyMetric).all():
            if metric.list_price:
                list_by_room.setdefault(metric.room_type_id, metric.list_price)
        for snap in db.query(CompetitorSnapshot).all():
            base = list_by_room.get(snap.room_type_id) or snap.price
            snap.price = float(base)
        for review in db.query(Review).all():
            if review.rating <= 2:
                review.rating = 4
        db.commit()
    finally:
        db.close()


def test_defaults_keep_phase1_human_review(client, auth_header):
    settings = client.get("/api/settings", headers=auth_header)
    assert settings.status_code == 200
    body = settings.json()
    assert body["workflow"]["auto_execute_on_low_risk"] is False
    policy = body["execution_policy"]
    assert policy["auto_execute_enabled"] is False
    assert policy["auto_execute_max_risk"] == "low"
    assert policy["holiday_requires_review"] is True
    assert policy["price_change_human_threshold_pct"] == 15
    assert policy["rollback_policy"] == "manual"
    assert policy["write_mode"] == "sandbox"

    gen = client.post("/api/strategies/generate", headers=auth_header, json={"kind": "pricing"})
    assert gen.status_code == 200, gen.text
    strategies = gen.json()["strategies"]
    assert strategies
    assert all(s["status"] == "pending_review" for s in strategies)
    if any(s["risk_level"] == "low" for s in strategies):
        assert all(s["status"] != "executed" for s in strategies if s["risk_level"] == "low")


def test_policy_validation(client, auth_header):
    too_high = _policy(client, auth_header, price_change_human_threshold_pct=20)
    assert too_high.status_code == 400
    zero = _policy(client, auth_header, price_change_human_threshold_pct=0)
    assert zero.status_code == 400
    max_risk = _policy(client, auth_header, auto_execute_max_risk="medium")
    assert max_risk.status_code == 400
    bad_rollback = _policy(client, auth_header, rollback_policy="always")
    assert bad_rollback.status_code == 400

    ok = _policy(
        client,
        auth_header,
        auto_execute_enabled=True,
        holiday_requires_review=True,
        price_change_human_threshold_pct=10,
        rollback_policy="auto_on_write_failure",
    )
    assert ok.status_code == 200, ok.text
    policy = ok.json()["execution_policy"]
    assert policy["auto_execute_enabled"] is True
    assert policy["price_change_human_threshold_pct"] == 10
    assert policy["rollback_policy"] == "auto_on_write_failure"
    assert ok.json()["workflow"]["auto_execute_on_low_risk"] is True

    off = _policy(client, auth_header, auto_execute_enabled=False, price_change_human_threshold_pct=15)
    assert off.status_code == 200
    assert off.json()["execution_policy"]["auto_execute_enabled"] is False


def test_may_auto_execute_hard_rules():
    policy = ExecutionPolicy(auto_execute_enabled=True, auto_execute_max_risk="low")
    low_ok = may_auto_execute(
        kind="pricing",
        risk_level="low",
        policy=policy,
        strategy_payload={"expected_impact": {"price_change_pct": 4.2}, "date_from": "2026-11-16", "date_to": "2026-11-20"},
    )
    assert low_ok.allowed

    disabled = may_auto_execute(
        kind="pricing",
        risk_level="low",
        policy=default_execution_policy(),
        strategy_payload={"expected_impact": {"price_change_pct": 4.2}, "date_from": "2026-11-16", "date_to": "2026-11-20"},
    )
    assert not disabled.allowed

    medium = may_auto_execute(
        kind="pricing",
        risk_level="medium",
        policy=policy,
        strategy_payload={"expected_impact": {"price_change_pct": 8}, "date_from": "2026-11-16", "date_to": "2026-11-20"},
    )
    assert not medium.allowed

    steep = may_auto_execute(
        kind="pricing",
        risk_level="low",
        policy=policy,
        strategy_payload={"expected_impact": {"price_change_pct": 15}, "date_from": "2026-11-16", "date_to": "2026-11-20"},
    )
    assert not steep.allowed
    assert any("15" in r for r in steep.reasons)

    holiday = may_auto_execute(
        kind="pricing",
        risk_level="low",
        policy=policy,
        strategy_payload={"expected_impact": {"price_change_pct": 3}, "date_from": "2026-10-01", "date_to": "2026-10-03"},
    )
    assert not holiday.allowed
    assert any("国庆" in r for r in holiday.reasons)
    hits = iter_chinese_holidays(date(2026, 10, 1), date(2026, 10, 3))
    assert hits and hits[0]["name"] == "国庆节"

    holiday_off = may_auto_execute(
        kind="pricing",
        risk_level="low",
        policy=ExecutionPolicy(auto_execute_enabled=True, holiday_requires_review=False),
        strategy_payload={"expected_impact": {"price_change_pct": 3}, "date_from": "2026-10-01", "date_to": "2026-10-03"},
    )
    assert holiday_off.allowed

    reply = may_auto_execute(kind="review_reply", risk_level="low", policy=policy)
    assert not reply.allowed

    join = may_auto_execute(
        kind="campaign",
        risk_level="low",
        policy=policy,
        strategy_payload={"campaign_advice": {"recommendation": "join", "recommend_join": True}},
        date_from="2026-11-16",
        date_to="2026-11-20",
    )
    assert not join.allowed


def test_validate_policy_rejects_unsafe_values():
    try:
        validate_execution_policy({"auto_execute_max_risk": "high"})
        assert False, "expected ValueError"
    except ValueError:
        pass
    try:
        validate_execution_policy({"price_change_human_threshold_pct": 16})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_auto_on_executes_low_risk_pricing_and_supports_rollback(client, auth_header):
    enabled = _policy(client, auth_header, auto_execute_enabled=True)
    assert enabled.status_code == 200, enabled.text
    _calm_demo_market()
    start = date.today() + timedelta(days=2)
    # Keep the window off the 2026-2027 official holiday calendar.
    if start.month == 10 or (start.month == 1 and start.day <= 3):
        start = date(start.year, 11, 16)
    end = start + timedelta(days=3)
    gen = client.post(
        "/api/strategies/generate",
        headers=auth_header,
        json={"kind": "pricing", "date_from": start.isoformat(), "date_to": end.isoformat()},
    )
    assert gen.status_code == 200, gen.text
    strategies = gen.json()["strategies"]
    low = [s for s in strategies if s["kind"] == "pricing" and s["risk_level"] == "low"]
    assert low, {s["room_name"]: (s["risk_level"], s.get("expected_impact")) for s in strategies}
    target = low[0]
    assert target["status"] == "executed"
    assert target["auto_executed"] is True
    assert target["reviewer"] == "system:auto_execute"
    assert target["checkpoint_id"]
    assert (target.get("execution_payload") or {}).get("mode") == "sandbox_official"
    assert (target.get("execution_payload") or {}).get("accepted") is True
    assert any(p["node"] == "execution_node" for p in target["path"])

    logs = client.get("/api/audit", headers=auth_header).json()["logs"]
    auto_rows = [r for r in logs if r["actor"] == "system:auto_execute"]
    assert auto_rows
    assert any(r["action"] == "auto_execute" for r in auto_rows)
    auto = next(
        r
        for r in auto_rows
        if r["action"] == "auto_execute" and r["entity_id"] == str(target["workflow_run_id"])
    )
    assert auto["after"]["risk_level"] == "low"
    assert auto["after"]["checkpoint_id"] == target["checkpoint_id"]
    assert auto["after"]["policy_snapshot"]["auto_execute_enabled"] is True
    assert auto["after"]["mode"] == "sandbox_official"

    rolled = client.post(
        f"/api/strategies/{target['id']}/rollback",
        headers=auth_header,
        json={"reason": "验证自动执行后的回滚"},
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["status"] == "rolled_back"
    assert rolled.json()["checkpoint_id"]


def test_auto_on_keeps_medium_high_holiday_join_and_replies_pending(client, auth_header):
    enabled = _policy(client, auth_header, auto_execute_enabled=True)
    assert enabled.status_code == 200
    # Demo hotel default window has rank/inversion signals → not low.
    gen = client.post("/api/strategies/generate", headers=auth_header, json={"kind": "all"})
    assert gen.status_code == 200, gen.text
    rows = gen.json()["strategies"]
    blocked = [s for s in rows if s["risk_level"] in {"medium", "high"}]
    assert blocked
    assert all(s["status"] == "pending_review" for s in blocked)
    joins = [
        s
        for s in rows
        if s["kind"] == "campaign"
        and (
            (s.get("campaign_advice") or {}).get("recommend_join")
            or (s.get("campaign_advice") or {}).get("recommendation") in {"join", "join_limited"}
        )
    ]
    assert all(s["status"] == "pending_review" for s in joins)

    _calm_demo_market()
    holiday = client.post(
        "/api/strategies/generate",
        headers=auth_header,
        json={"kind": "pricing", "date_from": "2026-10-01", "date_to": "2026-10-07"},
    )
    assert holiday.status_code == 200, holiday.text
    for s in holiday.json()["strategies"]:
        assert s["status"] == "pending_review"
        assert s.get("auto_executed") is not True

    reviews = client.get("/api/reviews", headers=auth_header).json()["reviews"]
    target = reviews[0]
    draft = client.post(f"/api/reviews/{target['id']}/draft", headers=auth_header)
    assert draft.status_code == 200, draft.text
    assert draft.json()["reply_status"] == "pending_review"


def test_live_write_without_credentials_is_refused(monkeypatch):
    monkeypatch.delenv("CTRIP_WRITE_ENABLED", raising=False)
    monkeypatch.delenv("CTRIP_WRITE_KEY", raising=False)
    monkeypatch.delenv("CTRIP_API_KEY", raising=False)
    from app.config import clear_settings_cache

    clear_settings_cache()
    receipt = OfficialWriteAdapter().apply_price(
        {"recommended_price": 688, "checkpoint_id": "ckpt-test"},
        write_mode="live",
    )
    assert receipt["accepted"] is False
    assert receipt["mode"] == "live"
    assert receipt["checkpoint_id"] == "ckpt-test"
    assert "拒绝" in receipt["message"] or "缺少" in receipt["message"]

    sandbox = OfficialWriteAdapter().apply_campaign({"campaign": "promo"}, write_mode="sandbox")
    assert sandbox["accepted"] is True
    assert sandbox["mode"] == "sandbox_official"
    assert "真实" in sandbox["message"] or "沙盒" in sandbox["message"]
