"""Contract-driven official e-sign HTTP client (reserved hook).

This module never invents a production e-sign or payment-provider URL.
Outbound HTTP is sent only when a non-example official contract is enabled,
credentials are present, and an explicit enable switch is on. Demo default:
vendor disabled, no outbound. Shipped examples are never auto-loaded.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.adapters.ota.official_http import (
    OfficialHttpClient,
    OfficialHttpError,
    WRITE_FALSE,
    WRITE_TRUE,
    _read_json_file,
    _settings_flag,
    _validate_contract,
    contract_hosts,
    contract_is_configured,
    is_forbidden_example_path,
    public_action_labels,
    validate_official_contract,
)
from app.config import get_settings

PLATFORM = "esign"
SETTING_KEY_PREFIX = "official_esign_contract"
ACTION_LABELS = {
    "create_agreement": "发起签署",
    "initiate_signing": "发起签署",
    "query_agreement": "查询签署状态",
    "query_status": "查询签署状态",
}


def platform_name() -> str:
    return PLATFORM


def setting_key_for(hotel_id: int | None = None) -> str:
    if hotel_id is None:
        return SETTING_KEY_PREFIX
    return f"hotel:{int(hotel_id)}:{SETTING_KEY_PREFIX}"


def enable_flag_setting_key(hotel_id: int) -> str:
    return f"hotel:{int(hotel_id)}:esign_enabled"


def official_creds_setting_key(hotel_id: int) -> str:
    return f"hotel:{int(hotel_id)}:official_api_creds_esign"


def _explicit_contract_path() -> str:
    settings = get_settings()
    return (
        str(_settings_flag(settings, "oiro_official_esign_contract", "") or "")
        or os.environ.get("OIRO_OFFICIAL_ESIGN_CONTRACT", "")
    ).strip()


def _contract_directory() -> str:
    settings = get_settings()
    return (
        str(_settings_flag(settings, "oiro_official_esign_contract_dir", "") or "")
        or os.environ.get("OIRO_OFFICIAL_ESIGN_CONTRACT_DIR", "")
    ).strip()


def esign_globally_enabled() -> bool | None:
    settings = get_settings()
    raw = os.environ.get("ESIGN_ENABLED", "").strip().lower()
    if raw in WRITE_TRUE:
        return True
    if raw in WRITE_FALSE:
        return False
    if bool(_settings_flag(settings, "esign_enabled", False)):
        return True
    return None


def esign_enabled(db=None, hotel_id: int | None = None) -> bool:
    forced = esign_globally_enabled()
    if forced is True:
        return True
    if forced is False:
        return False
    if db is not None and hotel_id is not None:
        from app.models import SystemSetting

        row = db.get(SystemSetting, enable_flag_setting_key(hotel_id))
        if row and str(row.value or "").strip().lower() in WRITE_TRUE:
            return True
    return False


def set_esign_enabled(db, hotel_id: int, enabled: bool) -> bool:
    from app.models import SystemSetting

    key = enable_flag_setting_key(hotel_id)
    value = "true" if enabled else "false"
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = datetime.utcnow()
    db.commit()
    return enabled


def inspect_official_esign_contract(db=None, hotel_id: int | None = None) -> dict[str, Any]:
    """Resolve the active official e-sign contract. Never loads shipped examples."""
    if hotel_id is None and db is not None:
        from app.services.tenancy import session_hotel_id

        hotel_id = session_hotel_id(db)

    path_raw = _explicit_contract_path()
    if path_raw:
        path = Path(path_raw)
        if path.is_file() and not is_forbidden_example_path(path):
            accepted = _validate_contract(_read_json_file(path) or {}, PLATFORM, path=path)
            if accepted:
                return {"source": "env_file", "path": str(path), "contract": accepted, "removable": False}

    directory = _contract_directory()
    if directory:
        tenant = ""
        if db is not None and hotel_id is not None:
            from app.models import Hotel

            hotel = db.get(Hotel, int(hotel_id))
            tenant = (hotel.tenant_key if hotel else "") or ""
        candidates = []
        if tenant:
            candidates.append(Path(directory) / tenant / "esign.json")
        if hotel_id is not None:
            candidates.append(Path(directory) / str(int(hotel_id)) / "esign.json")
        candidates.append(Path(directory) / "esign.json")
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file() and not is_forbidden_example_path(candidate):
                accepted = _validate_contract(_read_json_file(candidate) or {}, PLATFORM, path=candidate)
                if accepted:
                    return {"source": "env_file", "path": str(candidate), "contract": accepted, "removable": False}

    if db is not None and hotel_id is not None:
        from app.models import SystemSetting

        row = db.get(SystemSetting, setting_key_for(hotel_id))
        if row and (row.value or "").strip():
            try:
                data = json.loads(row.value)
            except json.JSONDecodeError:
                data = None
            accepted = _validate_contract(data or {}, PLATFORM)
            if accepted:
                return {"source": "settings", "path": None, "contract": accepted, "removable": True}

    return {"source": "none", "path": None, "contract": None, "removable": False}


def public_esign_contract_status(db=None, hotel_id: int | None = None) -> dict[str, Any]:
    info = inspect_official_esign_contract(db, hotel_id=hotel_id)
    contract = info.get("contract")
    configured = contract_is_configured(contract)
    hosts = contract_hosts(contract) if configured else []
    actions = list((contract or {}).get("actions") or {}) if configured else []
    labels = [ACTION_LABELS.get(name, name) for name in actions]
    labels = [label for i, label in enumerate(labels) if label and label not in labels[:i]]
    return {
        "contract_configured": configured,
        "contract_source": info["source"] if configured else "none",
        "contract_removable": bool(info.get("removable")) if configured else False,
        "contract_host": hosts[0] if hosts else "",
        "contract_hosts": hosts,
        "supported_actions": actions,
        "action_labels": labels or public_action_labels(actions),
        "contract_enabled": configured,
        "esign_enabled": esign_enabled(db, hotel_id=hotel_id),
        "platform": PLATFORM,
    }


def load_official_esign_contract(db=None, hotel_id: int | None = None) -> dict | None:
    """Load a configured official e-sign contract. Never falls back to shipped examples."""
    return inspect_official_esign_contract(db, hotel_id=hotel_id).get("contract")


def validate_esign_contract(data: Any) -> dict:
    """Schema-check an e-sign contract. Never opens a socket."""
    return validate_official_contract(data, PLATFORM)


def load_official_esign_credentials(db=None, hotel_id: int | None = None) -> dict[str, str]:
    if db is None or hotel_id is None:
        return {}
    from app.models import SystemSetting
    from app.services.channels import decrypt_credentials

    row = db.get(SystemSetting, official_creds_setting_key(hotel_id))
    if row is None or not row.value:
        return {}
    return decrypt_credentials(row.value)


def save_official_esign_credentials(db, hotel_id: int, payload: dict) -> dict[str, str]:
    from app.models import SystemSetting
    from app.services.channels import encrypt_credentials

    stored = load_official_esign_credentials(db, hotel_id)
    mapping = {
        "key": payload.get("key") or payload.get("api_key") or payload.get("write_key"),
        "secret": payload.get("secret") or payload.get("api_secret") or payload.get("write_secret"),
        "token": payload.get("token"),
        "session": payload.get("session"),
    }
    for field, value in mapping.items():
        if field in payload and payload[field] is not None and str(payload[field]) == "":
            stored.pop(field, None)
        elif value is not None and str(value).strip():
            stored[field] = str(value).strip()
    key = official_creds_setting_key(hotel_id)
    raw = encrypt_credentials(stored)
    row = db.get(SystemSetting, key)
    if not raw:
        if row is not None:
            db.delete(row)
            db.commit()
        return {}
    if row is None:
        db.add(SystemSetting(key=key, value=raw))
    else:
        row.value = raw
        row.updated_at = datetime.utcnow()
    db.commit()
    return stored


def outbound_ready(db=None, hotel_id: int | None = None) -> tuple[bool, str]:
    """Whether e-sign outbound HTTP is allowed. Never opens a socket itself."""
    contract = load_official_esign_contract(db, hotel_id=hotel_id)
    if not contract_is_configured(contract):
        return False, "missing_contract"
    if not esign_enabled(db, hotel_id=hotel_id):
        return False, "esign_disabled"
    creds = load_official_esign_credentials(db, hotel_id)
    if not any(creds.get(field) for field in ("key", "secret", "token", "session")):
        return False, "missing_credentials"
    return True, "ready"


def signing_action(contract: dict | None) -> str | None:
    if not isinstance(contract, dict):
        return None
    actions = contract.get("actions") or {}
    for name in ("create_agreement", "initiate_signing"):
        if name in actions and contract_is_configured(contract, name):
            return name
    for name in actions:
        if contract_is_configured(contract, name):
            return name
    return None


def send_esign_action(db, hotel_id: int, action: str | None = None, payload: dict | None = None) -> dict[str, Any]:
    ready, reason = outbound_ready(db, hotel_id)
    if not ready:
        raise OfficialHttpError(
            "电子合同供应商尚未就绪，已拒绝出站请求。",
            retryable=False,
            outcome="refused",
            status_code=None,
            body={"reason": reason},
        )
    contract = load_official_esign_contract(db, hotel_id=hotel_id)
    creds = load_official_esign_credentials(db, hotel_id)
    chosen = action or signing_action(contract)
    if not chosen:
        raise OfficialHttpError("官方电子合同契约未包含可用动作，已拒绝出站请求。", retryable=False, outcome="refused")
    client = OfficialHttpClient(PLATFORM, contract, creds)
    return client.send_write(chosen, payload or {}, items=[], request_id=None)
