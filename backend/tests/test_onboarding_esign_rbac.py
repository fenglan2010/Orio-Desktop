from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.auth import hash_password
from app.config import PROJECT_ROOT, clear_settings_cache
from app.database import SessionLocal
from app.models import Hotel, HotelMembership, User
from app.services.onboarding import STATUS_PAYMENT_CONFIRMED, STATUS_PRODUCT_ENABLED
from app.services.tenancy import ensure_membership


EXAMPLES_DIR = PROJECT_ROOT / "docs" / "official-contracts" / "esign"
FORBIDDEN = (
    "contract_source",
    "contract_host",
    "contract_hosts",
    "contract_removable",
    "write_flag_enabled",
    "read_flag_enabled",
    "live_ready",
    "base_url",
    "headers",
    "auth_header",
    "key",
    "secret",
    "contract",
    "path",
    "adapter",
)


class _SignHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        self.server.hits.append({"path": self.path, "key": self.headers.get("X-Api-Key"), "body": body})
        self._reply({"accepted": True, "message": "ok", "items": []})

    def do_GET(self):
        self.server.hits.append({"path": self.path, "key": self.headers.get("X-Api-Key")})
        self._reply({"status": "signing"})


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SignHandler)
    server.hits = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _stop_server(server):
    server.shutdown()
    server.server_close()


def _login(client, username="admin", password="admin123"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _ops_login(client, username="ops", password="ops123"):
    resp = client.post("/api/ops/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}, resp.json()


def _hotel_id(client, auth_header):
    me = client.get("/api/auth/me", headers=auth_header)
    assert me.status_code == 200, me.text
    return me.json()["hotel_id"]


def _valid_esign_contract(base_url: str) -> dict:
    return {
        "platform": "esign",
        "kind": "esign",
        "version": 1,
        "base_url": base_url,
        "auth": "header_api_key",
        "auth_header": "X-Api-Key",
        "timeout_seconds": 2,
        "headers": {"X-Internal-Token": "must-never-leak-secret"},
        "actions": {
            "create_agreement": {
                "method": "POST",
                "path": "/agreements",
                "request_template": {"request_id": "{{request_id}}", "title": "{{payload.title}}"},
                "response_mapping": {"message_path": "message"},
            }
        },
    }


def _create_hotel_user(hotel_id: int, username: str, password: str = "hotel123"):
    db = SessionLocal()
    try:
        hotel = db.get(Hotel, hotel_id)
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name="待开通店管理员",
            role="admin",
        )
        db.add(user)
        db.flush()
        ensure_membership(db, user, hotel, role="admin")
        db.commit()
    finally:
        db.close()


def test_ops_admin_login_still_works_and_maps_to_admin(client):
    headers, body = _ops_login(client, "ops", "ops123")
    assert body["role"] == "ops_admin"
    assert body["role_label"] == "管理员"
    assert body["permissions"]["manage_staff"] is True
    assert body["permissions"]["install_contracts"] is False
    me = client.get("/api/ops/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "ops_admin"


def test_demo_hotels_remain_enabled_with_safe_defaults(client, auth_header):
    hangzhou = client.get("/api/auth/me", headers=auth_header)
    assert hangzhou.status_code == 200, hangzhou.text
    assert hangzhou.json()["product_enabled"] is True
    assert hangzhou.json()["product_status"] == "已开通"
    settings = client.get("/api/settings", headers=auth_header).json()
    assert settings["product_enabled"] is True
    assert settings["execution_policy"]["auto_execute_enabled"] is False
    assert settings["execution_policy"]["write_mode"] == "sandbox"
    dash = client.get("/api/dashboard/summary", headers=auth_header)
    assert dash.status_code == 200, dash.text

    chengdu_auth = _login(client, "admin_b", "admin123")
    me_b = client.get("/api/auth/me", headers=chengdu_auth)
    assert me_b.status_code == 200
    assert me_b.json()["product_enabled"] is True
    assert client.get("/api/dashboard/summary", headers=chengdu_auth).status_code == 200
    settings_b = client.get("/api/settings", headers=chengdu_auth).json()
    assert settings_b["execution_policy"]["auto_execute_enabled"] is False
    assert settings_b["execution_policy"]["write_mode"] == "sandbox"


def test_shipped_esign_examples_are_rejected_and_not_auto_loaded(client, auth_header, monkeypatch):
    ops, _ = _ops_login(client, "ops_contract", "contract123")
    hotel_id = _hotel_id(client, auth_header)
    example_path = EXAMPLES_DIR / "esign.partner.example.json"
    assert example_path.is_file()
    example = json.loads(example_path.read_text())
    assert example.get("example") is True
    assert example.get("base_url") == ""

    monkeypatch.setenv("OIRO_OFFICIAL_ESIGN_CONTRACT", str(example_path))
    clear_settings_cache()
    status = client.get(f"/api/ops/hotels/{hotel_id}/esign", headers=ops)
    assert status.status_code == 200, status.text
    assert status.json()["contract_enabled"] is False
    monkeypatch.delenv("OIRO_OFFICIAL_ESIGN_CONTRACT", raising=False)
    clear_settings_cache()

    rejected = client.post(
        f"/api/ops/hotels/{hotel_id}/esign/contract/validate",
        headers=ops,
        json=example,
    )
    assert rejected.status_code == 400
    assert "示例" in rejected.json()["detail"]
    installed = client.put(
        f"/api/ops/hotels/{hotel_id}/esign/contract",
        headers=ops,
        json=example,
    )
    assert installed.status_code == 400


def test_hotel_jwt_forbidden_on_ops_and_esign_install(client, auth_header):
    hotel_id = _hotel_id(client, auth_header)
    contract = _valid_esign_contract("http://127.0.0.1:9")
    assert client.post("/api/esign/contract/validate", headers=auth_header, json=contract).status_code == 403
    assert client.put("/api/esign/contract", headers=auth_header, json=contract).status_code == 403
    assert client.delete("/api/esign/contract", headers=auth_header).status_code == 403
    assert client.get("/api/ops/hotels", headers=auth_header).status_code == 403
    assert client.put(
        f"/api/ops/hotels/{hotel_id}/esign/contract",
        headers=auth_header,
        json=contract,
    ).status_code in {401, 403}
    assert client.post("/api/ops/onboarding/initiate", headers=auth_header, json={"name": "越权店"}).status_code in {401, 403}


def test_role_matrix_enforced_on_ops_apis(client, auth_header):
    hotel_id = _hotel_id(client, auth_header)
    admin, admin_body = _ops_login(client, "ops", "ops123")
    contract, _ = _ops_login(client, "ops_contract", "contract123")
    operations, _ = _ops_login(client, "ops_operations", "operations123")
    support, _ = _ops_login(client, "ops_support", "support123")
    sysops, _ = _ops_login(client, "ops_sysops", "sysops123")
    assert admin_body["permissions"]["install_contracts"] is False

    payload = _valid_esign_contract("http://127.0.0.1:9")
    ota = {"platform": "ctrip", "base_url": "http://127.0.0.1:9", "auth": "header_api_key", "actions": {"apply_price": {"method": "POST", "path": "/p", "auth": "header_api_key"}}}

    # Admin cannot install official contracts or official keys.
    assert client.put(f"/api/ops/hotels/{hotel_id}/esign/contract", headers=admin, json=payload).status_code == 403
    assert client.put(f"/api/ops/hotels/{hotel_id}/channels/ctrip/contract", headers=admin, json=ota).status_code == 403
    assert client.put(
        f"/api/ops/hotels/{hotel_id}/channels/ctrip/official-credentials",
        headers=admin,
        json={"key": "should-not-store"},
    ).status_code == 403

    # Operations and support cannot install contracts or manage staff.
    for headers in (operations, support, sysops):
        assert client.put(f"/api/ops/hotels/{hotel_id}/esign/contract", headers=headers, json=payload).status_code == 403
        assert client.put(f"/api/ops/hotels/{hotel_id}/pms/pms/contract", headers=headers, json={"platform": "pms", "base_url": "http://127.0.0.1:9", "auth": "header_api_key", "actions": {"pull_inventory": {"method": "GET", "path": "/i"}}}).status_code == 403
        assert client.get("/api/ops/staff", headers=headers).status_code == 403

    created = client.post(
        "/api/ops/staff",
        headers=contract,
        json={"username": "ops_temp", "password": "temp123", "display_name": "临时候选", "role": "ops_support"},
    )
    assert created.status_code == 403

    # Admin can create and assign ops users.
    created = client.post(
        "/api/ops/staff",
        headers=admin,
        json={"username": "ops_temp", "password": "temp1234", "display_name": "临时客服", "role": "ops_support"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "ops_support"
    assert "temp1234" not in created.text
    updated = client.put(
        f"/api/ops/staff/{created.json()['id']}",
        headers=admin,
        json={"role": "ops_operations", "disabled": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["role"] == "ops_operations"

    # Contract can install a local e-sign contract; secrets are not echoed.
    installed = client.put(f"/api/ops/hotels/{hotel_id}/esign/contract", headers=contract, json=payload)
    assert installed.status_code == 200, installed.text
    assert installed.json()["contract_enabled"] is True
    blob = json.dumps(installed.json())
    assert "must-never-leak-secret" not in blob
    assert "X-Internal-Token" not in blob
    creds = client.put(
        f"/api/ops/hotels/{hotel_id}/esign/official-credentials",
        headers=contract,
        json={"key": "partner-key", "secret": "super-secret"},
    )
    assert creds.status_code == 200, creds.text
    assert creds.json()["has_official_credentials"] is True
    assert "super-secret" not in json.dumps(creds.json())

    # Operations/support see hotel status without hosts or secrets.
    listed = client.get("/api/ops/hotels", headers=support)
    assert listed.status_code == 200, listed.text
    row = next(h for h in listed.json()["hotels"] if h["id"] == hotel_id)
    assert row["product_enabled"] is True
    for key in FORBIDDEN:
        assert key not in row
    channels = client.get(f"/api/ops/hotels/{hotel_id}/channels", headers=operations)
    assert channels.status_code == 200, channels.text
    for ch in channels.json()["channels"]:
        for key in ("contract_host", "contract_hosts", "has_official_credentials", "base_url"):
            assert key not in ch

    # Support cannot initiate; operations can (separate fixture hotel).
    assert client.post("/api/ops/onboarding/initiate", headers=support, json={"name": "客服越权店"}).status_code == 403

    # Contract cannot flip live write alone; admin request leaves it pending until the other confirms.
    first = client.put(
        f"/api/ops/hotels/{hotel_id}/channels/ctrip/write-flag",
        headers=contract,
        json={"enabled": True},
    )
    assert first.status_code == 200, first.text
    assert first.json().get("write_flag_applied") is False
    assert first.json().get("write_flag_enabled") is not True
    second = client.put(
        f"/api/ops/hotels/{hotel_id}/channels/ctrip/write-flag",
        headers=admin,
        json={"enabled": True},
    )
    assert second.status_code == 200, second.text
    assert second.json().get("write_flag_applied") is True
    assert second.json().get("write_flag_enabled") is True
    # Turn it back off with the same dual confirmation so demo defaults stay safe.
    client.put(f"/api/ops/hotels/{hotel_id}/channels/ctrip/write-flag", headers=admin, json={"enabled": False})
    off = client.put(f"/api/ops/hotels/{hotel_id}/channels/ctrip/write-flag", headers=contract, json={"enabled": False})
    assert off.json().get("write_flag_enabled") is False

    sys = client.get("/api/ops/system", headers=sysops)
    assert sys.status_code == 200, sys.text
    assert "password" not in sys.text.lower()
    assert client.get("/api/ops/system", headers=support).status_code == 403
    assert client.get("/api/ops/audit", headers=admin).status_code == 200
    assert client.get("/api/ops/audit", headers=contract).status_code == 403


def test_product_gate_blocks_until_payment_confirmed_then_enabled(client, auth_header, monkeypatch):
    operations, _ = _ops_login(client, "ops_operations", "operations123")
    contract, _ = _ops_login(client, "ops_contract", "contract123")
    admin, _ = _ops_login(client, "ops", "ops123")
    hits = {"count": 0}

    def boom(*_args, **_kwargs):
        hits["count"] += 1
        raise AssertionError("outbound HTTP must not run without a real enabled contract")

    from app.adapters.ota import official_http as official_http_mod

    real_http = official_http_mod.perform_http_request
    official_http_mod.perform_http_request = boom

    created = client.post(
        "/api/ops/onboarding/initiate",
        headers=operations,
        json={"name": "苏州待开通测试酒店", "city": "苏州", "tenant_key": "prospect_suzhou", "approval_required": 2},
    )
    assert created.status_code == 200, created.text
    hotel_id = created.json()["hotel"]["id"]
    assert created.json()["onboarding_status"] == "initiated"
    assert created.json()["product_enabled"] is False
    # Admin cannot skip straight to product rights.
    skipped = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/enable-product", headers=admin)
    assert skipped.status_code == 400

    # Signing without a vendor must refuse and must not open a socket.
    refused = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/start-signing", headers=operations)
    assert refused.status_code == 400, refused.text
    waiting = client.get(f"/api/ops/hotels/{hotel_id}/onboarding", headers=operations)
    assert waiting.status_code == 200
    assert waiting.json()["vendor_state"] in {"waiting_vendor", "not_configured", "disabled"}
    assert waiting.json()["onboarding_status"] == "initiated"
    assert hits["count"] == 0

    _create_hotel_user(hotel_id, "admin_suzhou", "hotel123")
    hotel_auth = _login(client, "admin_suzhou", "hotel123")
    me = client.get("/api/auth/me", headers=hotel_auth)
    assert me.status_code == 200, me.text
    assert me.json()["product_enabled"] is False
    assert me.json()["product_status"] == "尚未开通"
    settings = client.get("/api/settings", headers=hotel_auth)
    assert settings.status_code == 200, settings.text
    assert settings.json()["product_enabled"] is False
    blocked = client.get("/api/dashboard/summary", headers=hotel_auth)
    assert blocked.status_code == 403
    assert "尚未开通" in blocked.json()["detail"]

    # Demo hotels still work while the prospect is gated.
    assert client.get("/api/dashboard/summary", headers=auth_header).status_code == 200

    official_http_mod.perform_http_request = real_http
    server = _start_server()
    try:
        contract_json = _valid_esign_contract(f"http://127.0.0.1:{server.server_address[1]}")
        installed = client.put(f"/api/ops/hotels/{hotel_id}/esign/contract", headers=contract, json=contract_json)
        assert installed.status_code == 200, installed.text
        creds = client.put(
            f"/api/ops/hotels/{hotel_id}/esign/official-credentials",
            headers=contract,
            json={"key": "partner-key", "secret": "super-secret"},
        )
        assert creds.status_code == 200, creds.text
        assert "super-secret" not in creds.text
        flag = client.put(f"/api/ops/hotels/{hotel_id}/esign/enable-flag", headers=contract, json={"enabled": True})
        assert flag.status_code == 200, flag.text

        started = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/start-signing", headers=operations)
        assert started.status_code == 200, started.text
        assert started.json()["onboarding_status"] == "signing"
        assert server.hits, "expected outbound only after a real non-example contract was enabled"

        submitted = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/submit-approval", headers=operations)
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["onboarding_status"] == "pending_approval"
        first = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/approve", headers=contract)
        assert first.status_code == 200
        assert first.json()["onboarding_status"] == "pending_approval"
        second = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/approve", headers=contract)
        assert second.status_code == 200, second.text
        assert second.json()["onboarding_status"] == "contract_effective"

        pending = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/mark-payment-pending", headers=contract)
        assert pending.json()["onboarding_status"] == "payment_pending"
        # Still gated before payment_confirmed / product_enabled.
        assert client.get("/api/dashboard/summary", headers=hotel_auth).status_code == 403
        confirmed = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/confirm-payment", headers=admin)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["onboarding_status"] == STATUS_PAYMENT_CONFIRMED
        # Payment confirmed is not yet the terminal enabled state.
        assert client.get("/api/dashboard/summary", headers=hotel_auth).status_code == 403
        enabled = client.post(f"/api/ops/hotels/{hotel_id}/onboarding/enable-product", headers=admin)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["onboarding_status"] == STATUS_PRODUCT_ENABLED
        assert enabled.json()["product_enabled"] is True
        allowed = client.get("/api/dashboard/summary", headers=hotel_auth)
        assert allowed.status_code == 200, allowed.text
        me_on = client.get("/api/auth/me", headers=hotel_auth).json()
        assert me_on["product_status"] == "已开通"
    finally:
        _stop_server(server)
        client.put(f"/api/ops/hotels/{hotel_id}/esign/enable-flag", headers=contract, json={"enabled": False})
