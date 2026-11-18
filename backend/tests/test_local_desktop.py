from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import clear_settings_cache
from app.gateway.router import reset_gateway
from app.services.distribution import reset_install_state_for_tests
from app.services.local_secrets import _secrets_file, public_model_keys_payload, save_model_keys


def _login(client, username="admin", password="admin123"):
    resp = client.post("/api/auth/login", json={"username": username, password: password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _minimal_ota_contract(platform: str, base_url: str) -> dict:
    return {
        "platform": platform,
        "base_url": base_url,
        "auth": "header_api_key",
        "actions": {
            "apply_price": {
                "method": "POST",
                "path": "/apply",
                "auth": "header_api_key",
            }
        },
    }


def test_source_mode_works_without_install_key(client, monkeypatch):
    monkeypatch.setenv("OIRO_DISTRIBUTION", "source")
    reset_install_state_for_tests()
    clear_settings_cache()
    assert client.get("/api/install/status").json()["activated"] is True
    assert client.get("/api/health").status_code == 200


def _first_run_payload(prefix: str = "pack"):
    return {
        "hotel_name": f"测试酒店 {prefix}",
        "city": "杭州",
        "address": "测试路 1 号",
        "positioning": "精品商务",
        "admin_username": f"adm_{prefix}",
        "admin_password": "hotel-pass-9",
        "room_types": [
            {
                "code": "DK",
                "name": "大床房",
                "inventory": 12,
                "base_price": 428,
                "location": "主楼3层",
            }
        ],
    }


def test_packaged_fresh_has_no_demo_login(packaged_client):
    packaged_client.post("/api/install/activate", json={"install_key": "OIRO-TEST-0000-0000"})
    bad = packaged_client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert bad.status_code == 401


def test_packaged_first_run_then_login(packaged_client):
    packaged_client.post("/api/install/activate", json={"install_key": "OIRO-TEST-0000-0000"})
    st = packaged_client.get("/api/install/status").json()
    assert st["initial_setup_required"] is True
    assert packaged_client.get("/api/settings").status_code == 503
    done = packaged_client.post("/api/first-run/complete", json=_first_run_payload("a1"))
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["initial_setup_complete"] is True
    assert body["hotel_name"].startswith("测试酒店")
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    rooms = packaged_client.get("/api/room-types", headers=headers).json()["room_types"]
    assert len(rooms) == 1
    assert rooms[0]["location"] == "主楼3层"
    assert rooms[0]["code"] == "DK"
    dash = packaged_client.get("/api/dashboard/rooms", headers=headers).json()["rooms"]
    assert dash[0]["name"] == "大床房"
    login = packaged_client.post("/api/auth/login", json={"username": "adm_a1", "password": "hotel-pass-9"})
    assert login.status_code == 200, login.text
    demo = packaged_client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert demo.status_code == 401


def test_packaged_mode_blocks_until_install_key(client, monkeypatch):
    monkeypatch.setenv("OIRO_DISTRIBUTION", "packaged")
    monkeypatch.delenv("OIRO_INSTALL_KEY", raising=False)
    monkeypatch.setenv("OIRO_INSTALL_KEY_ACCEPT", "OIRO-TEST-0000-0000")
    reset_install_state_for_tests()
    clear_settings_cache()
    st = client.get("/api/install/status").json()
    assert st["packaged"] is True
    assert st["blocked"] is True
    assert client.get("/api/settings").status_code == 503
    act = client.post("/api/install/activate", json={"install_key": "OIRO-TEST-0000-0000"})
    assert act.status_code == 200, act.text
    assert client.get("/api/health").status_code == 200


def test_ota_contract_path_bound_to_account(client, auth_header, tmp_path):
    hotel = client.get("/api/auth/me", headers=auth_header).json()
    hotel_id = hotel["hotel_id"]
    contract_path = tmp_path / "ctrip-prod.json"
    contract_path.write_text(
        json.dumps(_minimal_ota_contract("ctrip", "https://example-hotel-partner.test"), ensure_ascii=False),
        encoding="utf-8",
    )
    client.put(
        "/api/channels/ctrip",
        headers=auth_header,
        json={"account": "hangzhou-ota-001", "password": "demo-pass"},
    )
    res = client.put(
        "/api/local-config/ota/ctrip/contract-path",
        headers=auth_header,
        json={"contract_file_path": str(contract_path), "account": "hangzhou-ota-001"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["contract_file_path"] == str(contract_path)
    assert body["contract_enabled"] is True
    cfg = client.get("/api/local-config", headers=auth_header).json()
    assert cfg["integrations"]["ota"]["ctrip"]["contract_file_path"] == str(contract_path)
    assert cfg["integrations"]["ota"]["ctrip"]["contract_bound_account"]


def test_pms_api_endpoint_stored(client, auth_header):
    res = client.put(
        "/api/local-config/pms/pms",
        headers=auth_header,
        json={
            "api_endpoint": "https://pms.example-partner.test/api/v1",
            "contract_file_path": "",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["api_endpoint"] == "https://pms.example-partner.test/api/v1"
    listed = client.get("/api/pms", headers=auth_header).json()
    pms = next(row for row in listed["connections"] if row["kind"] == "pms")
    assert pms["api_endpoint"] == "https://pms.example-partner.test/api/v1"


def test_model_keys_saved_locally_not_in_repo(client, auth_header, tmp_path, monkeypatch):
    monkeypatch.setenv("OIRO_DATA_DIR", str(tmp_path / "data"))
    reset_gateway()
    clear_settings_cache()
    secret_path = _secrets_file()
    if secret_path.exists():
        secret_path.unlink()
    save_model_keys({"vendors": {"deepseek": {"deepseek_api_key": "local-only-secret"}}})
    masked = public_model_keys_payload()
    assert masked["vendors"]["deepseek"]["configured"] is True
    assert "local-only-secret" not in json.dumps(masked)
    assert secret_path.is_file()
    raw = secret_path.read_text(encoding="utf-8")
    assert "local-only-secret" not in raw
    api_res = client.put(
        "/api/local-config/model-keys",
        headers=auth_header,
        json={"vendors": {"deepseek": {"deepseek_api_key": "another-local-key"}}},
    )
    assert api_res.status_code == 200, api_res.text
    assert "another-local-key" not in api_res.text