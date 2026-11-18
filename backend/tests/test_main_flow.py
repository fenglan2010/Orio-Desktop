from __future__ import annotations


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["workflow_engine"] in {"langgraph", "equivalent_state_machine"}


def test_login_rejects_bad_password(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_dashboard_and_story_without_keys(client, auth_header):
    summary = client.get("/api/dashboard/summary", headers=auth_header)
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert "西湖" in data["hotel"]["name"]
    assert data["data_source"] == "ctrip_simulated"
    assert data["kpis"]["revenue"] > 0
    assert data["open_alerts"] >= 3

    series = client.get("/api/dashboard/timeseries", headers=auth_header)
    assert series.status_code == 200
    points = series.json()["series"]
    assert len(points) >= 20  # ~14 hist + 14 future, hotel-level collapsed

    rooms = client.get("/api/dashboard/rooms", headers=auth_header)
    names = {r["name"] for r in rooms.json()["rooms"]}
    assert {"豪华大床房", "商务双床房", "家庭房"} <= names

    alerts = client.get("/api/alerts", headers=auth_header)
    rows = alerts.json()["alerts"]
    types = {a["alert_type"] for a in rows}
    open_types = {a["alert_type"] for a in rows if a["status"] == "open"}
    assert {"rank_drop", "price_inversion"} <= types
    assert {"rank_drop", "price_inversion"} <= open_types
    if data["kpis"]["delta"].get("conversion_rate", 0) >= 0:
        assert "conversion_drop" not in open_types

    # Diagnose one alert — works with the mock model (no keys).
    alert_id = alerts.json()["alerts"][0]["id"]
    diag = client.post(f"/api/alerts/{alert_id}/diagnose", headers=auth_header)
    assert diag.status_code == 200, diag.text
    assert diag.json()["diagnosis"]
    assert diag.json()["diagnosis"]["classification"] in {"structural", "short_term"}


def test_generate_strategy_and_human_review_without_keys(client, auth_header):
    gen = client.post("/api/strategies/generate", headers=auth_header, json={})
    assert gen.status_code == 200, gen.text
    strategies = gen.json()["strategies"]
    assert len(strategies) >= 4  # 3 rooms + campaign
    pricing = next(s for s in strategies if s["kind"] == "pricing")
    campaign = next(s for s in strategies if s["kind"] == "campaign")
    assert pricing["recommended_price"]
    assert pricing["min_price"] <= pricing["recommended_price"] <= pricing["max_price"]
    assert pricing["rationale"]
    assert 0 < pricing["confidence"] <= 1
    assert pricing["status"] == "pending_review"
    assert campaign["campaign_advice"]
    assert campaign["status"] == "pending_review"

    approved = client.post(
        f"/api/strategies/{pricing['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "演示通过"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "executed"
    assert approved.json()["execution_payload"]["mode"] == "sandbox_official"

    me = client.get("/api/auth/me", headers=auth_header)
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_chat_and_settings_without_keys(client, auth_header):
    chat = client.post("/api/chat", headers=auth_header, json={"message": "为什么排名掉了"})
    assert chat.status_code == 200, chat.text
    assert len(chat.json()["content"]) > 10
    assert "转化同步走弱" not in chat.json()["content"]
    today_rev = client.get("/api/dashboard/summary", headers=auth_header).json()["kpis"]["today_revenue"]
    money = f"¥{int(round(float(today_rev))):,}"
    asked = client.post("/api/chat", headers=auth_header, json={"message": "今天营收怎么样"})
    assert asked.status_code == 200, asked.text
    assert money in asked.json()["content"]

    settings = client.get("/api/settings", headers=auth_header)
    assert settings.status_code == 200
    body = settings.json()
    assert body["overseas_enabled"] is False
    assert "pricing_strategy" in body["critical_rule"]
    vendors = {a["vendor"] for a in body["gateway"]["adapters"]}
    assert {"deepseek", "qwen", "openai", "gemini", "grok", "mock"} <= vendors
    assert "self_hosted" not in vendors
    assert body["workflow"]["auto_execute_on_low_risk"] is False
