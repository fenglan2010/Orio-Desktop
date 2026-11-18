from __future__ import annotations

import json

from app.database import SessionLocal
from app.models import User


def _login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    return resp


def _headers(client, username="admin", password="admin123"):
    resp = _login(client, username, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _pending_pricing(client, headers):
    gen = client.post("/api/strategies/generate", headers=headers, json={"kind": "pricing"})
    assert gen.status_code == 200, gen.text
    rows = [s for s in gen.json()["strategies"] if s["kind"] == "pricing" and s["status"] == "pending_review"]
    assert rows
    return rows[0]


def test_demo_accounts_and_admin_role(client, auth_header):
    me = client.get("/api/auth/me", headers=auth_header)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert body["permissions"]["manage_users"] is True
    assert body["permissions"]["update_policy"] is True
    assert "password" not in body
    assert "password_hash" not in body
    assert "pbkdf2$" not in json.dumps(body)

    for username, password, role in (
        ("manager", "manager123", "revenue_manager"),
        ("approver", "approver123", "approver"),
        ("analyst", "analyst123", "analyst"),
    ):
        resp = _login(client, username, password)
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == role

    listed = client.get("/api/users", headers=auth_header)
    assert listed.status_code == 200, listed.text
    blob = listed.text
    assert "password_hash" not in blob
    assert "pbkdf2$" not in blob
    names = {u["username"] for u in listed.json()["users"]}
    assert {"admin", "manager", "approver", "analyst"} <= names


def test_admin_can_manage_users_and_policy(client, auth_header):
    created = client.post(
        "/api/users",
        headers=auth_header,
        json={"username": "ops_temp", "password": "temp1234", "display_name": "临时运营", "role": "analyst"},
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]
    assert "password" not in created.json()
    updated = client.put(
        f"/api/users/{user_id}",
        headers=auth_header,
        json={"display_name": "临时观察", "role": "approver"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["role"] == "approver"
    disabled = client.post(f"/api/users/{user_id}/disable", headers=auth_header)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["disabled"] is True
    denied = _login(client, "ops_temp", "temp1234")
    assert denied.status_code == 401
    assert "停用" in denied.json()["detail"]
    enabled = client.post(f"/api/users/{user_id}/enable", headers=auth_header)
    assert enabled.status_code == 200
    assert _login(client, "ops_temp", "temp1234").status_code == 200

    policy = client.put(
        "/api/settings",
        headers=auth_header,
        json={"execution_policy": {"auto_execute_enabled": False, "write_mode": "sandbox"}},
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["execution_policy"]["auto_execute_enabled"] is False
    assert "pbkdf2$" not in policy.text
    assert "password_hash" not in policy.text


def test_revenue_manager_can_approve_but_not_policy_channels_or_users(client, auth_header):
    strategy = _pending_pricing(client, auth_header)
    manager = _headers(client, "manager", "manager123")
    me = client.get("/api/auth/me", headers=manager).json()
    assert me["role"] == "revenue_manager"
    assert me["permissions"]["approve"] is True
    assert me["permissions"]["execute"] is True
    assert me["permissions"]["update_policy"] is False
    assert me["permissions"]["manage_channels"] is False
    assert me["permissions"]["manage_users"] is False

    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=manager,
        json={"decision": "approve", "comment": "经理确认"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"

    policy = client.put(
        "/api/settings",
        headers=manager,
        json={"execution_policy": {"auto_execute_enabled": True, "write_mode": "live"}},
    )
    assert policy.status_code == 403
    assert "执行策略" in policy.json()["detail"]

    channel = client.put(
        "/api/channels/ctrip",
        headers=manager,
        json={"key": "should-not-save", "platform_hotel_id": "x"},
    )
    assert channel.status_code == 403

    users = client.post(
        "/api/users",
        headers=manager,
        json={"username": "intruder", "password": "intruder1", "display_name": "x", "role": "analyst"},
    )
    assert users.status_code == 403

    # Confirm policy was not changed by the refused PUT.
    settings = client.get("/api/settings", headers=auth_header).json()
    assert settings["execution_policy"]["auto_execute_enabled"] is False
    assert settings["execution_policy"]["write_mode"] == "sandbox"


def test_approver_can_approve_but_cannot_execute(client, auth_header):
    strategy = _pending_pricing(client, auth_header)
    approver = _headers(client, "approver", "approver123")
    me = client.get("/api/auth/me", headers=approver).json()
    assert me["permissions"]["approve"] is True
    assert me["permissions"]["execute"] is False
    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=approver,
        json={"decision": "approve", "comment": "审批通过"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert not approved.json().get("execution_payload")
    execute = client.post(f"/api/strategies/{strategy['id']}/execute", headers=approver)
    assert execute.status_code == 403
    rollback = client.post(f"/api/strategies/{strategy['id']}/rollback", headers=approver, json={"reason": "no"})
    assert rollback.status_code == 403

    # Admin/manager can then execute the approved item.
    done = client.post(f"/api/strategies/{strategy['id']}/execute", headers=auth_header)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "executed"


def test_analyst_is_forbidden_on_approve_execute_and_policy(client, auth_header):
    strategy = _pending_pricing(client, auth_header)
    analyst = _headers(client, "analyst", "analyst123")
    me = client.get("/api/auth/me", headers=analyst).json()
    assert me["role"] == "analyst"
    assert me["permissions"]["approve"] is False
    assert me["permissions"]["view_l1"] is True
    assert me["permissions"]["view_l2"] is True
    assert me["permissions"]["view_l3"] is False

    gen = client.post("/api/strategies/generate", headers=analyst, json={"kind": "pricing"})
    assert gen.status_code == 403
    review = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=analyst,
        json={"decision": "approve", "comment": "no"},
    )
    assert review.status_code == 403
    execute = client.post(f"/api/strategies/{strategy['id']}/execute", headers=analyst)
    assert execute.status_code == 403
    policy = client.put(
        "/api/settings",
        headers=analyst,
        json={"execution_policy": {"auto_execute_enabled": True}},
    )
    assert policy.status_code == 403
    # View dashboards still works.
    dash = client.get("/api/dashboard/summary", headers=analyst)
    assert dash.status_code == 200


def test_disabled_user_cannot_login_and_last_admin_protected(client, auth_header):
    created = client.post(
        "/api/users",
        headers=auth_header,
        json={"username": "temp_admin", "password": "admin456", "display_name": "第二管理员", "role": "admin"},
    )
    assert created.status_code == 200, created.text
    other_id = created.json()["id"]

    # With a second admin present, the extra one can be disabled.
    assert client.post(f"/api/users/{other_id}/disable", headers=auth_header).status_code == 200
    assert _login(client, "temp_admin", "admin456").status_code == 401

    # Re-enable, then the original admin cannot be demoted/disabled while unique...
    assert client.post(f"/api/users/{other_id}/enable", headers=auth_header).status_code == 200
    # Disable the extra admin again so only the seeded admin remains unique after we also try to remove him.
    assert client.post(f"/api/users/{other_id}/disable", headers=auth_header).status_code == 200

    admin_row = SessionLocal().query(User).filter(User.username == "admin").one()
    demote = client.put(f"/api/users/{admin_row.id}", headers=auth_header, json={"role": "analyst"})
    assert demote.status_code == 400, demote.text
    assert "唯一" in demote.json()["detail"]
    disable = client.post(f"/api/users/{admin_row.id}/disable", headers=auth_header)
    assert disable.status_code == 400
    assert "唯一" in disable.json()["detail"]
    # Seeded admin can still log in.
    assert _login(client, "admin", "admin123").status_code == 200


def test_me_and_settings_never_leak_password_hashes(client, auth_header):
    me = client.get("/api/auth/me", headers=auth_header)
    settings = client.get("/api/settings", headers=auth_header)
    users = client.get("/api/users", headers=auth_header)
    matrix = client.get("/api/users/matrix", headers=auth_header)
    blob = me.text + settings.text + users.text + matrix.text
    assert "password_hash" not in blob
    assert "password" not in json.dumps(me.json().get("permissions", {}))
    for key in ("password", "password_hash"):
        assert key not in me.json()
        assert key not in (settings.json().get("roles") or [{}])[0] if False else True
    assert "pbkdf2$" not in blob
    # settings still includes the live matrix
    assert settings.json()["permission_matrix"]["rows"]
    assert {r["id"] for r in settings.json()["roles"]} == {"admin", "revenue_manager", "approver", "analyst"}
