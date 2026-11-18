from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.adapters.ota.official_write import OfficialWriteAdapter
from app.config import clear_settings_cache
from app.database import SessionLocal
from app.models import Review, WriteJob
from app.services.writes import is_retryable


class _ContractHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = None
        if self.path != "/hang":
            body = self._read_body()
        self.server.hits.append({"path": self.path, "key": self.headers.get("X-Api-Key"), "body": body})
        if self.path == "/hang":
            time.sleep(1.5)
            return self._send(200, {"accepted": True, "receipt_id": "too-late"})
        if self.path == "/fail5":
            return self._send(503, {"message": "upstream unavailable"})
        if self.path == "/fail4":
            return self._send(400, {"message": "invalid price payload"})
        if self.headers.get("X-Api-Key") != self.server.expected_key:
            return self._send(401, {"message": "unauthorized"})
        items = []
        for item in (body or {}).get("items") or []:
            items.append({"date": item.get("date") or item.get("date_from"), "status": "accepted", "price": item.get("intended_price")})
        return self._send(
            200,
            {
                "accepted": True,
                "receipt_id": "official-rcpt-1001",
                "request_id": (body or {}).get("request_id"),
                "message": "accepted by local official contract",
                "items": items,
            },
        )


def _start_contract_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ContractHandler)
    server.expected_key = "test-live-key"
    server.hits = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _stop_contract_server(server):
    server.shutdown()
    server.server_close()


def _login(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _write_contract(tmp_path: Path, base_url: str, path: str = "/hotel/prices", timeout: float = 2.0) -> Path:
    contract = {
        "platform": "ctrip",
        "version": 1,
        "source": "local test contract — not a production Ctrip API",
        "base_url": base_url,
        "auth": "header_api_key",
        "auth_header": "X-Api-Key",
        "timeout_seconds": timeout,
        "actions": {
            "apply_price": {
                "method": "POST",
                "path": path,
                "request_template": {
                    "request_id": "{{request_id}}",
                    "hotel_id": "{{platform_hotel_id}}",
                    "items": "{{items}}",
                },
                "response_mapping": {
                    "accepted_path": "accepted",
                    "receipt_id_path": "receipt_id",
                    "request_id_path": "request_id",
                    "message_path": "message",
                    "items_path": "items",
                    "item_status_path": "status",
                    "item_id_path": "date",
                },
            }
        },
    }
    file_path = tmp_path / "ctrip.json"
    file_path.write_text(json.dumps(contract), encoding="utf-8")
    return file_path


def _enable_live(monkeypatch, contract_path: Path | None = None, *, write_enabled: bool = True):
    if write_enabled:
        monkeypatch.setenv("CTRIP_WRITE_ENABLED", "true")
    else:
        monkeypatch.setenv("CTRIP_WRITE_ENABLED", "false")
    if contract_path:
        monkeypatch.setenv("OIRO_OFFICIAL_CONTRACT_CTRIP", str(contract_path))
    else:
        monkeypatch.delenv("OIRO_OFFICIAL_CONTRACT_CTRIP", raising=False)
        monkeypatch.delenv("OIRO_OFFICIAL_CONTRACT_DIR", raising=False)
    clear_settings_cache()


def _forbid_official_http(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("unexpected official outbound HTTP")

    monkeypatch.setattr("app.adapters.ota.official_http.perform_http_request", boom)


def _pending_pricing(client, headers):
    gen = client.post("/api/strategies/generate", headers=headers, json={"kind": "pricing"})
    assert gen.status_code == 200, gen.text
    rows = [s for s in gen.json()["strategies"] if s["kind"] == "pricing" and s["status"] == "pending_review"]
    assert rows
    return rows[0]


def _connect_ctrip(client, headers, *, key="test-live-key"):
    resp = client.put(
        "/api/channels/ctrip",
        headers=headers,
        json={"account": key, "password": "super-secret-do-not-echo"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "authorized"
    assert body["has_credentials"] is True
    blob = json.dumps(body)
    assert "super-secret-do-not-echo" not in blob
    assert "test-live-key" not in blob
    return body


def test_demo_defaults_stay_sandbox_and_auto_off(client, auth_header):
    settings = client.get("/api/settings", headers=auth_header)
    assert settings.status_code == 200, settings.text
    body = settings.json()
    assert body["execution_policy"]["auto_execute_enabled"] is False
    assert body["execution_policy"]["write_mode"] == "sandbox"
    channels = {c["platform"]: c for c in body["channels"]}
    assert set(channels) == {"ctrip", "meituan", "fliggy"}
    for ch in channels.values():
        assert ch["status"] == "disconnected"
        assert ch["contract_enabled"] is False
        assert "write_flag_enabled" not in ch
        assert "live_ready" not in ch
        assert "contract_host" not in ch
        assert "contract_source" not in ch


def test_credentials_never_appear_in_settings_or_channel_get(client, auth_header):
    secret = "SUPER-SECRET-KEY-XYZ-99"
    saved = client.put(
        "/api/channels/meituan",
        headers=auth_header,
        json={"account": "mt-100", "password": secret},
    )
    assert saved.status_code == 200, saved.text
    settings = client.get("/api/settings", headers=auth_header)
    listed = client.get("/api/channels", headers=auth_header)
    one = client.get("/api/channels/meituan", headers=auth_header)
    blob = settings.text + listed.text + one.text
    assert secret not in blob
    assert "credentials_encrypted" not in blob
    body = one.json()
    assert body["has_credentials"] is True
    assert "platform_hotel_id" not in body
    assert "key" not in body
    assert "secret" not in body
    assert "account" not in body
    assert "password" not in body


def test_live_without_contract_or_authorization_refuses_without_socket(client, auth_header, monkeypatch):
    _enable_live(monkeypatch, None, write_enabled=False)
    _forbid_official_http(monkeypatch)
    settings = client.put(
        "/api/settings",
        headers=auth_header,
        json={"execution_policy": {"write_mode": "live", "auto_execute_enabled": False}},
    )
    assert settings.status_code == 200, settings.text

    strategy = _pending_pricing(client, auth_header)
    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "无契约 live 应拒绝"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "execution_failed"
    jobs = client.get(f"/api/writes?strategy_id={strategy['id']}", headers=auth_header).json()["writes"]
    assert jobs
    job = jobs[0]
    assert job["receipt_mode"] in {"live", "refused"}
    assert job["refuse_reason"] in {"missing_credentials", "channel_unauthorized", "write_disabled", "missing_contract"}
    assert job["retryable"] is False
    retry = client.post(f"/api/writes/{job['id']}/retry", headers=auth_header)
    assert retry.status_code in {400, 409}

    # Authorized credentials but still no contract: also refuse, still no socket.
    _connect_ctrip(client, auth_header)
    _enable_live(monkeypatch, None, write_enabled=True)
    strategy2 = _pending_pricing(client, auth_header)
    approved2 = client.post(
        f"/api/strategies/{strategy2['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "有凭证无契约"},
    )
    assert approved2.status_code == 200, approved2.text
    job2 = client.get(f"/api/writes?strategy_id={strategy2['id']}", headers=auth_header).json()["writes"][0]
    assert job2["refuse_reason"] == "missing_contract"
    assert job2["retryable"] is False
    assert "未发起外部请求" in (job2["last_error"] or job2["receipt"]["message"])

    client.put("/api/settings", headers=auth_header, json={"execution_policy": {"write_mode": "sandbox"}})


def test_authorized_local_contract_sends_http_and_reconciles(client, auth_header, monkeypatch, tmp_path):
    server = _start_contract_server()
    try:
        contract = _write_contract(tmp_path, f"http://127.0.0.1:{server.server_address[1]}")
        _enable_live(monkeypatch, contract, write_enabled=True)
        _connect_ctrip(client, auth_header)
        client.put(
            "/api/settings",
            headers=auth_header,
            json={"execution_policy": {"write_mode": "live", "auto_execute_enabled": False, "rollback_policy": "manual"}},
        )
        strategy = _pending_pricing(client, auth_header)
        approved = client.post(
            f"/api/strategies/{strategy['id']}/review",
            headers=auth_header,
            json={"decision": "approve", "comment": "本地官方契约 live 写入"},
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "executed"
        assert body["execution_payload"]["mode"] == "live"
        assert body["execution_payload"]["accepted"] is True
        assert body["execution_payload"]["receipt_id"] == "official-rcpt-1001"
        jobs = client.get(f"/api/writes?strategy_id={strategy['id']}", headers=auth_header).json()["writes"]
        assert jobs
        job = jobs[0]
        assert job["status"] in {"accepted", "reconciled"}
        assert job["receipt_mode"] == "live"
        assert job["platform"] == "ctrip"
        assert job["receipt_id"] == "official-rcpt-1001"
        assert job["reconcile_ok"] is True
        assert server.hits, "expected a real HTTP call to the local official contract server"
        assert server.hits[0]["path"] == "/hotel/prices"
        assert server.hits[0]["key"] == "test-live-key"
        # Hotel no longer enters a platform hotel ID; ops/env may still supply it internally.
        assert "super-secret-do-not-echo" not in str(server.hits[0]["body"])
    finally:
        _stop_contract_server(server)
        client.put("/api/settings", headers=auth_header, json={"execution_policy": {"write_mode": "sandbox"}})
        monkeypatch.delenv("CTRIP_WRITE_ENABLED", raising=False)
        monkeypatch.delenv("OIRO_OFFICIAL_CONTRACT_CTRIP", raising=False)
        clear_settings_cache()


def test_http_5xx_is_retryable_and_4xx_is_not(client, auth_header, monkeypatch, tmp_path):
    server = _start_contract_server()
    try:
        _connect_ctrip(client, auth_header)
        client.put(
            "/api/settings",
            headers=auth_header,
            json={"execution_policy": {"write_mode": "live", "auto_execute_enabled": False, "rollback_policy": "manual"}},
        )

        fail5 = _write_contract(tmp_path, f"http://127.0.0.1:{server.server_address[1]}", path="/fail5")
        _enable_live(monkeypatch, fail5, write_enabled=True)
        s5 = _pending_pricing(client, auth_header)
        a5 = client.post(f"/api/strategies/{s5['id']}/review", headers=auth_header, json={"decision": "approve", "comment": "5xx"})
        assert a5.status_code == 200, a5.text
        job5 = client.get(f"/api/writes?strategy_id={s5['id']}", headers=auth_header).json()["writes"][0]
        assert job5["status"] == "failed"
        assert job5["retryable"] is True
        assert job5["refuse_reason"] in {"", None}
        assert "503" in (job5["last_error"] or "")

        fail4 = _write_contract(tmp_path, f"http://127.0.0.1:{server.server_address[1]}", path="/fail4")
        _enable_live(monkeypatch, fail4, write_enabled=True)
        s4 = _pending_pricing(client, auth_header)
        a4 = client.post(f"/api/strategies/{s4['id']}/review", headers=auth_header, json={"decision": "approve", "comment": "4xx"})
        assert a4.status_code == 200, a4.text
        job4 = client.get(f"/api/writes?strategy_id={s4['id']}", headers=auth_header).json()["writes"][0]
        assert job4["status"] == "failed"
        assert job4["refuse_reason"] == "http_4xx"
        assert job4["retryable"] is False
        retry = client.post(f"/api/writes/{job4['id']}/retry", headers=auth_header)
        assert retry.status_code in {400, 409}

        hang = _write_contract(tmp_path, f"http://127.0.0.1:{server.server_address[1]}", path="/hang", timeout=0.3)
        _enable_live(monkeypatch, hang, write_enabled=True)
        st = _pending_pricing(client, auth_header)
        at = client.post(f"/api/strategies/{st['id']}/review", headers=auth_header, json={"decision": "approve", "comment": "timeout"})
        assert at.status_code == 200, at.text
        jobt = client.get(f"/api/writes?strategy_id={st['id']}", headers=auth_header).json()["writes"][0]
        assert jobt["status"] == "timeout"
        assert jobt["retryable"] is True
    finally:
        _stop_contract_server(server)
        client.put("/api/settings", headers=auth_header, json={"execution_policy": {"write_mode": "sandbox"}})
        monkeypatch.delenv("CTRIP_WRITE_ENABLED", raising=False)
        monkeypatch.delenv("OIRO_OFFICIAL_CONTRACT_CTRIP", raising=False)
        clear_settings_cache()


def test_review_reply_is_not_auto_posted_even_when_live_ready(client, auth_header, monkeypatch, tmp_path):
    server = _start_contract_server()
    try:
        contract = _write_contract(tmp_path, f"http://127.0.0.1:{server.server_address[1]}")
        _enable_live(monkeypatch, contract, write_enabled=True)
        _connect_ctrip(client, auth_header)
        client.put(
            "/api/settings",
            headers=auth_header,
            json={"execution_policy": {"write_mode": "live", "auto_execute_enabled": False}},
        )
        reviews = client.get("/api/reviews", headers=auth_header).json()["reviews"]
        target = reviews[0]
        draft = client.post(f"/api/reviews/{target['id']}/draft", headers=auth_header)
        assert draft.status_code == 200, draft.text
        approved = client.post(
            f"/api/reviews/{target['id']}/review",
            headers=auth_header,
            json={"decision": "approve", "comment": "评价回复不得出站"},
        )
        assert approved.status_code == 200, approved.text
        db = SessionLocal()
        try:
            review = db.get(Review, target["id"])
            jobs = db.query(WriteJob).filter(WriteJob.review_id == target["id"]).all()
        finally:
            db.close()
        assert jobs, "expected a write job for the review reply"
        job = jobs[0]
        listed = client.get(f"/api/writes/{job.id}", headers=auth_header).json()
        assert listed["kind"] == "review_reply"
        assert listed["refuse_reason"] == "review_reply_not_auto_posted"
        assert listed["retryable"] is False
        assert listed["receipt"]["mode"] in {"live", "refused"}
        assert not any(hit["path"] not in {"/hotel/prices", "/fail4", "/fail5", "/hang"} and "review" in (hit["path"] or "") for hit in server.hits)
        # The local official server must not have received a review-reply POST.
        assert all(hit["path"] != "/hotel/prices" or hit["body"] is None or "reply" not in json.dumps(hit["body"]) for hit in server.hits)
        assert not server.hits or all("review_reply" not in json.dumps(hit.get("body") or {}) for hit in server.hits)
    finally:
        _stop_contract_server(server)
        client.put("/api/settings", headers=auth_header, json={"execution_policy": {"write_mode": "sandbox"}})
        monkeypatch.delenv("CTRIP_WRITE_ENABLED", raising=False)
        monkeypatch.delenv("OIRO_OFFICIAL_CONTRACT_CTRIP", raising=False)
        clear_settings_cache()


def test_adapter_live_without_context_still_refuses_without_socket(monkeypatch):
    monkeypatch.delenv("CTRIP_WRITE_ENABLED", raising=False)
    monkeypatch.delenv("CTRIP_WRITE_KEY", raising=False)
    monkeypatch.delenv("OIRO_OFFICIAL_CONTRACT_CTRIP", raising=False)
    clear_settings_cache()
    _forbid_official_http(monkeypatch)
    receipt = OfficialWriteAdapter().apply_price({"recommended_price": 688, "checkpoint_id": "ckpt-inc3"}, write_mode="live")
    assert receipt["accepted"] is False
    assert receipt["refuse_reason"] == "missing_credentials"
    sandbox = OfficialWriteAdapter().apply_price({"recommended_price": 688}, write_mode="sandbox")
    assert sandbox["mode"] == "sandbox_official"
    assert sandbox["accepted"] is True
