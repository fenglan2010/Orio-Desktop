"""Phase III increment 1: real multi-hotel tenant isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Hotel, HotelMembership, SystemSetting, User


def _login(client: TestClient, username: str, password: str = "admin123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _headers(client: TestClient, username: str = "admin", password: str = "admin123") -> dict:
    resp = _login(client, username, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _hotels():
    db = SessionLocal()
    try:
        hotel_a = db.query(Hotel).filter(Hotel.tenant_key == "demo").one()
        hotel_b = db.query(Hotel).filter(Hotel.tenant_key == "demo_chengdu").one()
        return hotel_a.id, hotel_a.name, hotel_b.id, hotel_b.name
    finally:
        db.close()


def test_current_hotel_is_membership_not_hardcoded_demo(client):
    a = _login(client, "admin", "admin123")
    b = _login(client, "admin_b", "admin123")
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert a.json()["hotel_name"] == "杭州西湖精品酒店（演示）"
    assert b.json()["hotel_name"] == "成都春熙商务酒店（演示）"
    assert a.json()["hotel_id"] != b.json()["hotel_id"]
    assert "成都" in (b.json().get("city") or "")

    me_a = client.get("/api/auth/me", headers={"Authorization": f"Bearer {a.json()['access_token']}"})
    me_b = client.get("/api/auth/me", headers={"Authorization": f"Bearer {b.json()['access_token']}"})
    assert me_a.status_code == 200 and me_b.status_code == 200
    assert me_a.json()["tenant_key"] == "demo"
    assert me_b.json()["tenant_key"] == "demo_chengdu"
    assert me_a.json()["hotel_id"] == a.json()["hotel_id"]
    assert me_b.json()["hotel_id"] == b.json()["hotel_id"]

    db = SessionLocal()
    try:
        demo = db.query(Hotel).filter(Hotel.tenant_key == "demo").one()
        assert me_b.json()["hotel_id"] != demo.id
        rows = (
            db.query(User.username, HotelMembership.hotel_id)
            .join(HotelMembership, HotelMembership.user_id == User.id)
            .all()
        )
        by_user: dict[str, set[int]] = {}
        for username, hotel_id in rows:
            by_user.setdefault(username, set()).add(hotel_id)
        assert by_user["admin"] == {demo.id}
        assert by_user["admin_b"] == {me_b.json()["hotel_id"]}
        assert demo.id not in by_user["admin_b"]
    finally:
        db.close()


def test_hotel_a_accounts_keep_phase2_permissions(client, auth_header):
    me = client.get("/api/auth/me", headers=auth_header).json()
    assert me["username"] == "admin"
    assert me["role"] == "admin"
    assert me["permissions"]["manage_users"] is True
    assert me["hotel_name"] == "杭州西湖精品酒店（演示）"

    listed = client.get("/api/users", headers=auth_header)
    assert listed.status_code == 200
    names = {u["username"] for u in listed.json()["users"]}
    assert {"admin", "manager", "approver", "analyst"} <= names
    assert "admin_b" not in names

    for username, can_manage, can_execute, can_approve in (
        ("manager", False, True, True),
        ("approver", False, False, True),
        ("analyst", False, False, False),
    ):
        headers = _headers(client, username, f"{username}123")
        body = client.get("/api/auth/me", headers=headers).json()
        assert body["hotel_name"] == "杭州西湖精品酒店（演示）"
        assert body["permissions"]["manage_users"] is can_manage
        assert body["permissions"]["execute"] is can_execute
        assert body["permissions"]["approve"] is can_approve


def test_hotels_cannot_see_each_others_lists_or_entities(client):
    headers_a = _headers(client, "admin", "admin123")
    headers_b = _headers(client, "admin_b", "admin123")
    hotel_a_id, _, hotel_b_id, _ = _hotels()

    def _get(path, headers):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{path} {resp.status_code} {resp.text}"
        return resp.json()

    alerts_a = _get("/api/alerts", headers_a)["alerts"]
    alerts_b = _get("/api/alerts", headers_b)["alerts"]
    titles_a = {row["title"] for row in alerts_a}
    titles_b = {row["title"] for row in alerts_b}
    assert any("搜索排名" in t or "价格倒挂" in t or "差评" in t for t in titles_a)
    assert any("工作日出租率" in t or "停车场" in t for t in titles_b)
    assert titles_a.isdisjoint(titles_b)
    ids_a = {row["id"] for row in alerts_a}
    ids_b = {row["id"] for row in alerts_b}
    assert ids_a and ids_b and ids_a.isdisjoint(ids_b)
    leaked = client.get(f"/api/alerts/{next(iter(ids_b))}", headers=headers_a)
    assert leaked.status_code == 404

    rooms_a = {r["name"] for r in _get("/api/dashboard/rooms", headers_a)["rooms"]}
    rooms_b = {r["name"] for r in _get("/api/dashboard/rooms", headers_b)["rooms"]}
    assert "豪华大床房" in rooms_a
    assert "高级大床房" in rooms_b
    assert "豪华大床房" not in rooms_b
    assert "高级大床房" not in rooms_a

    summary_a = _get("/api/dashboard/summary", headers_a)
    summary_b = _get("/api/dashboard/summary", headers_b)
    assert summary_a["hotel"]["id"] == hotel_a_id
    assert summary_b["hotel"]["id"] == hotel_b_id
    assert summary_a["hotel"]["name"] != summary_b["hotel"]["name"]

    reviews_a = {r["external_id"] for r in _get("/api/reviews", headers_a)["reviews"]}
    reviews_b = {r["external_id"] for r in _get("/api/reviews", headers_b)["reviews"]}
    assert any("chengdu" not in i for i in reviews_a)
    assert any("chengdu" in i for i in reviews_b)
    assert reviews_a.isdisjoint(reviews_b)

    users_b = {u["username"] for u in _get("/api/users", headers_b)["users"]}
    assert users_b == {"admin_b"}
    users_a = {u["username"] for u in _get("/api/users", headers_a)["users"]}
    assert "admin_b" not in users_a

    db = SessionLocal()
    try:
        admin_a_id = db.query(User).filter(User.username == "admin").one().id
    finally:
        db.close()
    denied = client.put(
        f"/api/users/{admin_a_id}",
        headers=headers_b,
        json={"display_name": "被串改"},
    )
    assert denied.status_code == 404

    spoof = client.get("/api/alerts", headers={**headers_a, "X-Hotel-Id": str(hotel_b_id)})
    assert spoof.status_code == 403


def test_hotel_b_cannot_change_hotel_a_policy_or_users(client):
    headers_a = _headers(client, "admin", "admin123")
    headers_b = _headers(client, "admin_b", "admin123")

    before = client.get("/api/settings", headers=headers_a)
    assert before.status_code == 200
    assert before.json()["execution_policy"]["auto_execute_enabled"] is False
    assert before.json()["execution_policy"]["write_mode"] == "sandbox"

    attacked = client.put(
        "/api/settings",
        headers=headers_b,
        json={"execution_policy": {"auto_execute_enabled": True, "write_mode": "live"}},
    )
    assert attacked.status_code == 200
    after_a = client.get("/api/settings", headers=headers_a).json()
    after_b = client.get("/api/settings", headers=headers_b).json()
    assert after_a["execution_policy"]["auto_execute_enabled"] is False
    assert after_a["execution_policy"]["write_mode"] == "sandbox"
    assert after_b["execution_policy"]["auto_execute_enabled"] is True
    assert after_b["hotel"]["name"] == "成都春熙商务酒店（演示）"

    reset = client.put(
        "/api/settings",
        headers=headers_b,
        json={"execution_policy": {"auto_execute_enabled": False, "write_mode": "sandbox"}},
    )
    assert reset.status_code == 200
    assert reset.json()["execution_policy"]["auto_execute_enabled"] is False
    assert reset.json()["execution_policy"]["write_mode"] == "sandbox"

    db = SessionLocal()
    try:
        hotel_a = db.query(Hotel).filter(Hotel.tenant_key == "demo").one()
        hotel_b = db.query(Hotel).filter(Hotel.tenant_key == "demo_chengdu").one()
        assert db.get(SystemSetting, "execution_policy") is None
        assert db.get(SystemSetting, f"hotel:{hotel_a.id}:official_contract_ctrip") is None
        assert db.get(SystemSetting, f"hotel:{hotel_b.id}:official_contract_ctrip") is None
        for ch in after_a["channels"] + after_b["channels"]:
            assert ch["contract_enabled"] is False
            assert "contract_host" not in ch
    finally:
        db.close()
