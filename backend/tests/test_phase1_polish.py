from __future__ import annotations


def test_conversion_drop_closed_when_rate_rose(client, auth_header):
    ts = client.get("/api/dashboard/timeseries", headers=auth_header)
    assert ts.status_code == 200, ts.text
    conv = ts.json()["conversion"]
    last7 = conv["last7"]["conversion_rate"]
    prev7 = conv["prev7"]["conversion_rate"]
    dropped = (prev7 - last7) * 100 >= 0.6
    rows = client.get("/api/alerts", headers=auth_header).json()["alerts"]
    open_conv = [a for a in rows if a["alert_type"] == "conversion_drop" and a["status"] == "open"]
    if dropped:
        assert open_conv
    else:
        assert not open_conv


def test_date_diagnosis_does_not_claim_conversion_drop_when_absent(client, auth_header):
    summary = client.get("/api/dashboard/summary", headers=auth_header).json()
    as_of = summary["as_of"]
    diag = client.get(f"/api/diagnosis?date={as_of}", headers=auth_header)
    assert diag.status_code == 200, diag.text
    body = diag.json()
    flags = (body.get("signals") or {}).get("flags") or body.get("flags") or []
    narrative = body.get("narrative") or ""
    not_causes = " ".join(body.get("not_causes") or [])
    if "conversion_drop" not in flags:
        assert "排名与转化同步下滑" not in narrative
        assert "排名与转化同步下滑" not in not_causes
        assert "并未同步走弱" in narrative or "转化率并未" in narrative


def test_room_constraints_update_and_validation(client, auth_header):
    rooms = client.get("/api/dashboard/rooms", headers=auth_header).json()["rooms"]
    room = next(r for r in rooms if r["name"] == "豪华大床房")
    bad = client.put(
        f"/api/dashboard/rooms/{room['id']}/constraints",
        headers=auth_header,
        json={"min_price": 900, "max_price": 500},
    )
    assert bad.status_code == 400
    zero = client.put(
        f"/api/dashboard/rooms/{room['id']}/constraints",
        headers=auth_header,
        json={"min_price": 0, "max_price": 800},
    )
    assert zero.status_code == 400
    missing = client.put(
        "/api/dashboard/rooms/99999/constraints",
        headers=auth_header,
        json={"min_price": 400, "max_price": 900},
    )
    assert missing.status_code == 404

    ok = client.put(
        f"/api/dashboard/rooms/{room['id']}/constraints",
        headers=auth_header,
        json={"min_price": 520, "max_price": 980},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["min_price"] == 520
    assert ok.json()["max_price"] == 980
    fresh = client.get("/api/dashboard/rooms", headers=auth_header).json()["rooms"]
    saved = next(r for r in fresh if r["id"] == room["id"])
    assert saved["min_price"] == 520
    assert saved["max_price"] == 980

    audit = client.get("/api/audit", headers=auth_header)
    assert audit.status_code == 200, audit.text
    payload = audit.json()
    items = payload.get("items") or payload.get("logs") or payload.get("entries") or payload
    if isinstance(items, dict):
        items = items.get("items") or items.get("logs") or []
    actions = {row.get("action") for row in items}
    assert "update_price_constraints" in actions


def test_chat_works_when_strategies_pending(client, auth_header):
    gen = client.post("/api/strategies/generate", headers=auth_header, json={"kind": "all"})
    assert gen.status_code == 200, gen.text
    assert gen.json()["strategies"]
    today_rev = client.get("/api/dashboard/summary", headers=auth_header).json()["kpis"]["today_revenue"]
    money = f"¥{int(round(float(today_rev))):,}"
    asked = client.post("/api/chat", headers=auth_header, json={"message": "今天营收怎么样"})
    assert asked.status_code == 200, asked.text
    assert money in asked.json()["content"]
    pending = client.post("/api/chat", headers=auth_header, json={"message": "现在有哪些待审策略"})
    assert pending.status_code == 200, pending.text
    assert "待人审" in pending.json()["content"] or "策略" in pending.json()["content"]
