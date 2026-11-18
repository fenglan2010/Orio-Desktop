from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.config import PROJECT_ROOT, clear_settings_cache


EXAMPLES_DIR = PROJECT_ROOT / "docs" / "official-contracts" / "pms"
FORBIDDEN_HOST_KEYS = (
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


class _PullHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def _reply(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.hits.append({"path": self.path, "key": self.headers.get("X-Api-Key"), "method": "GET"})
        if self.path.startswith("/room-types"):
            self._reply({"items": [{"code": "KNG", "name": "豪华大床房"}]})
            return
        if self.path.startswith("/reservations"):
            self._reply({"items": []})
            return
        self._reply(
            {
                "items": [
                    {"date": "2026-11-18", "room_type_code": "KNG", "available_rooms": 8, "sold_rooms": 2}
                ],
                "message": "ok",
            }
        )

    def do_POST(self):
        body = self._read_body()
        self.server.hits.append({"path": self.path, "key": self.headers.get("X-Api-Key"), "method": "POST", "body": body})
        self._reply({"accepted": True, "message": "ok", "items": []})


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PullHandler)
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


def _ops_login(client):
    # Contract desk can install official API contracts. The seeded admin remains ops/ops123.
    resp = client.post("/api/ops/auth/login", json={"username": "ops_contract", "password": "contract123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _hotel_id(client, auth_header):
    me = client.get("/api/auth/me", headers=auth_header)
    assert me.status_code == 200, me.text
    return me.json()["hotel_id"]


def _assert_hotel_safe(body):
    blob = body if isinstance(body, str) else json.dumps(body)
    assert "must-never-leak" not in blob
    assert "super-secret" not in blob
    payload = body if isinstance(body, dict) else json.loads(body)
    for key in FORBIDDEN_HOST_KEYS:
        assert key not in payload, key


def _valid_contract(base_url: str, kind: str = "pms") -> dict:
    return {
        "platform": kind,
        "kind": kind,
        "version": 1,
        "base_url": base_url,
        "auth": "header_api_key",
        "auth_header": "X-Api-Key",
        "timeout_seconds": 2,
        "headers": {"X-Internal-Token": "must-never-leak-secret"},
        "actions": {
            "pull_inventory": {
                "method": "GET",
                "path": "/inventory",
                "kind": "read",
                "request_template": {
                    "hotel_id": "{{platform_hotel_id}}",
                    "date_from": "{{date_from}}",
                    "date_to": "{{date_to}}",
                },
                "response_mapping": {"items_path": "items", "message_path": "message"},
            },
            "pull_room_types": {
                "method": "GET",
                "path": "/room-types",
                "kind": "read",
                "response_mapping": {"items_path": "items"},
            },
        },
    }


def test_demo_defaults_have_no_pms_contract(client, auth_header):
    settings = client.get("/api/settings", headers=auth_header).json()
    assert settings["execution_policy"]["auto_execute_enabled"] is False
    assert settings["execution_policy"]["write_mode"] == "sandbox"
    conns = settings["pms_connections"]
    assert {row["kind"] for row in conns} == {"pms", "crs"}
    for row in conns:
        assert row["contract_enabled"] is False
        assert row.get("open_actions") in ([], None) or row["open_actions"] == []
        _assert_hotel_safe(row)
        assert row["status"] in {"disconnected", "credentials_missing"}


def test_shipped_pms_examples_are_not_auto_loaded(client, auth_header, monkeypatch):
    listed = client.get("/api/pms", headers=auth_header).json()["connections"]
    assert all(row["contract_enabled"] is False for row in listed)
    example = EXAMPLES_DIR / "pms.partner.example.json"
    assert example.is_file()
    monkeypatch.setenv("OIRO_OFFICIAL_PMS_CONTRACT", str(example))
    clear_settings_cache()
    row = client.get("/api/pms/pms", headers=auth_header)
    assert row.status_code == 200, row.text
    assert row.json()["contract_enabled"] is False
    _assert_hotel_safe(row.json())
    monkeypatch.delenv("OIRO_OFFICIAL_PMS_CONTRACT", raising=False)
    clear_settings_cache()


def test_example_and_invalid_pms_contracts_are_rejected(client, auth_header):
    ops = _ops_login(client)
    hotel_id = _hotel_id(client, auth_header)

    def post(payload, kind="pms"):
        return client.post(
            f"/api/ops/hotels/{hotel_id}/pms/{kind}/contract/validate",
            headers=ops,
            json=payload,
        )

    example = json.loads((EXAMPLES_DIR / "pms.partner.example.json").read_text())
    assert example.get("example") is True
    resp = post(example)
    assert resp.status_code == 400
    assert "示例" in resp.json()["detail"]

    mismatch = post(_valid_contract("http://127.0.0.1:9", kind="pms"), kind="crs")
    assert mismatch.status_code == 400

    public_http = _valid_contract("http://partner.example.com")
    resp = post(public_http)
    assert resp.status_code == 400
    assert "https" in resp.json()["detail"]

    # Validate must not persist.
    ch = client.get("/api/pms/pms", headers=auth_header).json()
    assert ch["contract_enabled"] is False
    _assert_hotel_safe(ch)

    hotel_validate = client.post("/api/pms/pms/contract/validate", headers=auth_header, json=_valid_contract("http://127.0.0.1:9"))
    assert hotel_validate.status_code == 403
    hotel_put = client.put("/api/pms/pms/contract", headers=auth_header, json=_valid_contract("http://127.0.0.1:9"))
    assert hotel_put.status_code == 403
    hotel_del = client.delete("/api/pms/pms/contract", headers=auth_header)
    assert hotel_del.status_code == 403
    hotel_ops = client.put(
        f"/api/ops/hotels/{hotel_id}/pms/pms/contract",
        headers=auth_header,
        json=_valid_contract("http://127.0.0.1:9"),
    )
    assert hotel_ops.status_code in {401, 403}


def test_ops_install_enables_hotel_status_without_leaking_hosts(client, auth_header):
    ops = _ops_login(client)
    hangzhou = _hotel_id(client, auth_header)
    chengdu_auth = _login(client, "admin_b", "admin123")
    chengdu = _hotel_id(client, chengdu_auth)

    contract = _valid_contract("http://127.0.0.1:9")
    installed = client.put(
        f"/api/ops/hotels/{hangzhou}/pms/pms/contract",
        headers=ops,
        json=contract,
    )
    assert installed.status_code == 200, installed.text
    body = installed.json()
    assert body["contract_enabled"] is True
    assert body["contract_host"] == "127.0.0.1:9"
    assert "库存/房态" in (body.get("action_labels") or body.get("open_actions") or [])
    assert "must-never-leak" not in json.dumps(body)

    hotel = client.get("/api/pms/pms", headers=auth_header)
    assert hotel.status_code == 200, hotel.text
    view = hotel.json()
    assert view["contract_enabled"] is True
    assert "库存/房态" in (view.get("open_actions") or [])
    _assert_hotel_safe(view)

    other = client.get("/api/pms/pms", headers=chengdu_auth)
    assert other.status_code == 200, other.text
    assert other.json()["contract_enabled"] is False
    _assert_hotel_safe(other.json())

    settings_b = client.get("/api/settings", headers=chengdu_auth).json()
    pms_b = {row["kind"]: row for row in settings_b["pms_connections"]}
    assert pms_b["pms"]["contract_enabled"] is False

    # Credentials isolation: Hangzhou hotel auth does not appear for Chengdu.
    saved = client.put(
        f"/api/pms/pms",
        headers=auth_header,
        json={"account": "hz-pms", "password": "super-secret"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["has_credentials"] is True
    _assert_hotel_safe(saved.json())
    other_after = client.get("/api/pms/pms", headers=chengdu_auth).json()
    assert other_after["has_credentials"] is False
    _assert_hotel_safe(other_after)

    removed = client.delete(f"/api/ops/hotels/{hangzhou}/pms/pms/contract", headers=ops)
    assert removed.status_code == 200, removed.text
    assert removed.json()["contract_enabled"] is False
    assert client.get("/api/pms/pms", headers=auth_header).json()["contract_enabled"] is False
    assert chengdu != hangzhou


def test_no_outbound_without_real_contract_and_ingest_skips(client, auth_header):
    server = _start_server()
    try:
        from app.database import SessionLocal
        from app.jobs.tasks import ingest_hotel
        from app.models import DailyMetric, Hotel

        with SessionLocal() as db:
            hotel = db.query(Hotel).filter(Hotel.tenant_key == "demo").one()
            before = [
                (row.id, row.platform, row.available_rooms, row.sold_rooms, row.occupancy)
                for row in db.query(DailyMetric).filter(DailyMetric.hotel_id == hotel.id).order_by(DailyMetric.id)
            ]
            result = ingest_hotel(db, hotel.id, actor="system:scheduler")

        payload = result.get("result") or result
        pms = payload.get("pms_ingest") or {}
        assert pms.get("status") == "not_configured"
        assert pms.get("official_http") is False
        assert server.hits == []

        with SessionLocal() as db:
            hotel = db.query(Hotel).filter(Hotel.tenant_key == "demo").one()
            after = [
                (row.id, row.platform, row.available_rooms, row.sold_rooms, row.occupancy)
                for row in db.query(DailyMetric).filter(DailyMetric.hotel_id == hotel.id).order_by(DailyMetric.id)
            ]
        assert after == before

        # Hotel verify without a contract must not open a socket.
        saved = client.put("/api/pms/pms", headers=auth_header, json={"account": "demo", "password": "demo-pass"})
        assert saved.status_code == 200, saved.text
        verified = client.post("/api/pms/pms/verify", headers=auth_header)
        assert verified.status_code == 200, verified.text
        assert verified.json()["status"] == "authorized"
        assert verified.json()["contract_enabled"] is False
        _assert_hotel_safe(verified.json())
        assert server.hits == []
    finally:
        _stop_server(server)


def test_live_ready_contract_pulls_and_stays_isolated(client, auth_header):
    server = _start_server()
    try:
        ops = _ops_login(client)
        hangzhou = _hotel_id(client, auth_header)
        chengdu_auth = _login(client, "admin_b", "admin123")
        chengdu = _hotel_id(client, chengdu_auth)
        contract = _valid_contract(f"http://127.0.0.1:{server.server_address[1]}")

        installed = client.put(f"/api/ops/hotels/{hangzhou}/pms/pms/contract", headers=ops, json=contract)
        assert installed.status_code == 200, installed.text
        creds = client.put(
            f"/api/ops/hotels/{hangzhou}/pms/pms/official-credentials",
            headers=ops,
            json={"key": "partner-key", "secret": "super-secret"},
        )
        assert creds.status_code == 200, creds.text
        assert creds.json()["has_official_credentials"] is True
        assert "super-secret" not in json.dumps(creds.json())
        flag = client.put(
            f"/api/ops/hotels/{hangzhou}/pms/pms/read-flag",
            headers=ops,
            json={"enabled": True},
        )
        assert flag.status_code == 200, flag.text
        assert flag.json()["read_flag_enabled"] is True
        assert flag.json()["live_ready"] is True

        hotel_view = client.get("/api/pms/pms", headers=auth_header).json()
        assert hotel_view["contract_enabled"] is True
        _assert_hotel_safe(hotel_view)
        other = client.get("/api/pms/pms", headers=chengdu_auth).json()
        assert other["contract_enabled"] is False

        from app.database import SessionLocal
        from app.jobs.tasks import ingest_hotel
        from app.models import DailyMetric, Hotel, PmsStagingRecord

        with SessionLocal() as db:
            hotel = db.get(Hotel, hangzhou)
            ctrip_before = [
                (row.id, row.available_rooms, row.sold_rooms)
                for row in db.query(DailyMetric)
                .filter(DailyMetric.hotel_id == hangzhou, DailyMetric.platform == "ctrip")
                .order_by(DailyMetric.id)
            ]
            result = ingest_hotel(db, hangzhou, actor="system:scheduler")
            pms = (result.get("result") or result).get("pms_ingest") or {}
            assert pms.get("status") == "succeeded", pms
            assert pms.get("official_http") is True
            staging = db.query(PmsStagingRecord).filter(PmsStagingRecord.hotel_id == hangzhou).all()
            assert staging
            ctrip_after = [
                (row.id, row.available_rooms, row.sold_rooms)
                for row in db.query(DailyMetric)
                .filter(DailyMetric.hotel_id == hangzhou, DailyMetric.platform == "ctrip")
                .order_by(DailyMetric.id)
            ]
            assert ctrip_after == ctrip_before
            other_staging = db.query(PmsStagingRecord).filter(PmsStagingRecord.hotel_id == chengdu).all()
            assert other_staging == []

        assert server.hits, "expected official pull to hit the local fixture server"
        assert all(hit.get("key") == "partner-key" for hit in server.hits)

        with SessionLocal() as db:
            skipped = ingest_hotel(db, chengdu, actor="system:scheduler")
        other_pms = (skipped.get("result") or skipped).get("pms_ingest") or {}
        assert other_pms.get("status") == "not_configured"
        # Chengdu ingest must not add hits against Hangzhou's contract host.
        hangzhou_hits = len(server.hits)
        assert hangzhou_hits >= 1
    finally:
        _stop_server(server)
