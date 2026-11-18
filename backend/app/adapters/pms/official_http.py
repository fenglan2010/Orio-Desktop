"""Contract-driven official PMS/CRS HTTP client (reserved hook).

This module never invents Opera / Cloudbeds / Mews / Ctrip / Meituan or any
other production PMS/CRS URLs. Outbound HTTP is sent only when a caller
supplies a configured, non-example official contract together with credentials
and an enabled read or write switch. Default demo installs no production
contracts and leaves the read switch off. Shipped examples are never auto-loaded.
"""

from __future__ import annotations

import json
import os
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
    action_label,
    contract_hosts,
    contract_is_configured,
    is_forbidden_example_path,
    public_action_labels as ota_public_action_labels,
    validate_official_contract,
)
from app.config import get_settings

KINDS = ("pms", "crs")
KIND_LABELS = {"pms": "PMS", "crs": "CRS"}
SETTING_KEY_PREFIX = "official_pms_contract_"

# Canonical families for reserved master-data pulls.
FAMILY_INVENTORY = "inventory"
FAMILY_ROOM_TYPES = "room_types"
FAMILY_RESERVATIONS = "reservations"

RESERVED_PULL_FAMILIES = {
    "pull_inventory": FAMILY_INVENTORY,
    "fetch_inventory": FAMILY_INVENTORY,
    "inventory": FAMILY_INVENTORY,
    "availability": FAMILY_INVENTORY,
    "pull_availability": FAMILY_INVENTORY,
    "fetch_availability": FAMILY_INVENTORY,
    "sync_inventory": FAMILY_INVENTORY,
    "pull_room_types": FAMILY_ROOM_TYPES,
    "fetch_room_types": FAMILY_ROOM_TYPES,
    "room_types": FAMILY_ROOM_TYPES,
    "rooms": FAMILY_ROOM_TYPES,
    "sync_room_types": FAMILY_ROOM_TYPES,
    "pull_reservations": FAMILY_RESERVATIONS,
    "fetch_reservations": FAMILY_RESERVATIONS,
    "reservations": FAMILY_RESERVATIONS,
    "otb": FAMILY_RESERVATIONS,
    "pull_otb": FAMILY_RESERVATIONS,
    "fetch_otb": FAMILY_RESERVATIONS,
    "on_the_books": FAMILY_RESERVATIONS,
    "sync_reservations": FAMILY_RESERVATIONS,
}

PMS_ACTION_LABELS = {
    FAMILY_INVENTORY: "库存/房态",
    FAMILY_ROOM_TYPES: "房型",
    FAMILY_RESERVATIONS: "在手订单",
    "verify": "连通校验",
    "pull_inventory": "库存/房态",
    "fetch_inventory": "库存/房态",
    "inventory": "库存/房态",
    "availability": "库存/房态",
    "pull_availability": "库存/房态",
    "fetch_availability": "库存/房态",
    "sync_inventory": "库存/房态",
    "pull_room_types": "房型",
    "fetch_room_types": "房型",
    "room_types": "房型",
    "rooms": "房型",
    "sync_room_types": "房型",
    "pull_reservations": "在手订单",
    "fetch_reservations": "在手订单",
    "reservations": "在手订单",
    "otb": "在手订单",
    "pull_otb": "在手订单",
    "fetch_otb": "在手订单",
    "on_the_books": "在手订单",
    "sync_reservations": "在手订单",
}


def normalize_kind(kind: str | None) -> str:
    value = (kind or "").strip().lower()
    if value not in KINDS:
        raise ValueError("仅支持 PMS 或 CRS 官方接口预留接入")
    return value


def kind_label(kind: str, contract: dict | None = None) -> str:
    if isinstance(contract, dict):
        label = str(contract.get("label") or "").strip()
        if label:
            return label
    return KIND_LABELS.get((kind or "").strip().lower(), kind or "PMS/CRS")


def action_family(name: str) -> str | None:
    return RESERVED_PULL_FAMILIES.get((name or "").strip().lower())


def pms_action_label(name: str) -> str:
    key = (name or "").strip()
    if key in PMS_ACTION_LABELS:
        return PMS_ACTION_LABELS[key]
    family = action_family(key)
    if family and family in PMS_ACTION_LABELS:
        return PMS_ACTION_LABELS[family]
    return action_label(key)


def public_action_labels(actions) -> list[str]:
    labels: list[str] = []
    for name in actions or []:
        label = pms_action_label(str(name))
        if label and label not in labels:
            labels.append(label)
    if labels:
        return labels
    return ota_public_action_labels(actions)


def setting_key_for(kind: str, hotel_id: int | None = None) -> str:
    base = f"{SETTING_KEY_PREFIX}{normalize_kind(kind)}"
    if hotel_id is None:
        return base
    return f"hotel:{int(hotel_id)}:{base}"


def read_flag_setting_key(kind: str, hotel_id: int) -> str:
    return f"hotel:{int(hotel_id)}:pms_read_enabled_{normalize_kind(kind)}"


def write_flag_setting_key(kind: str, hotel_id: int) -> str:
    return f"hotel:{int(hotel_id)}:pms_write_enabled_{normalize_kind(kind)}"


def _flag_from_settings_or_env(kind: str, suffix: str) -> bool | None:
    kind = (kind or "").strip().lower()
    settings = get_settings()
    attr = f"{kind}_{suffix}"
    raw = os.environ.get(f"{kind.upper()}_{suffix.upper()}", "").strip().lower()
    if raw in WRITE_TRUE:
        return True
    if raw in WRITE_FALSE:
        return False
    if bool(_settings_flag(settings, attr, False)):
        return True
    return None


def kind_read_enabled(kind: str, db=None, hotel_id: int | None = None) -> bool:
    forced = _flag_from_settings_or_env(kind, "read_enabled")
    if forced is True:
        return True
    if forced is False:
        return False
    if db is not None and hotel_id is not None:
        from app.models import SystemSetting

        row = db.get(SystemSetting, read_flag_setting_key(kind, hotel_id))
        if row and str(row.value or "").strip().lower() in WRITE_TRUE:
            return True
    return False


def kind_write_enabled(kind: str, db=None, hotel_id: int | None = None) -> bool:
    forced = _flag_from_settings_or_env(kind, "write_enabled")
    if forced is True:
        return True
    if forced is False:
        return False
    if db is not None and hotel_id is not None:
        from app.models import SystemSetting

        row = db.get(SystemSetting, write_flag_setting_key(kind, hotel_id))
        if row and str(row.value or "").strip().lower() in WRITE_TRUE:
            return True
    return False


def set_kind_flag(db, hotel_id: int, kind: str, enabled: bool, *, read: bool = True) -> bool:
    from datetime import datetime
    from app.models import SystemSetting

    key = read_flag_setting_key(kind, hotel_id) if read else write_flag_setting_key(kind, hotel_id)
    value = "true" if enabled else "false"
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = datetime.utcnow()
    db.commit()
    return enabled


def reserved_pull_actions(contract: dict | None) -> list[str]:
    if not contract or not isinstance(contract, dict):
        return []
    actions = contract.get("actions") or {}
    if not isinstance(actions, dict):
        return []
    names: list[str] = []
    for name, spec in actions.items():
        action = str(name)
        if action_family(action) is None:
            continue
        if contract_is_configured(contract, action):
            names.append(action)
    return names


def _explicit_contract_path(kind: str) -> str:
    kind = (kind or "").strip().lower()
    settings = get_settings()
    return (
        str(_settings_flag(settings, f"oiro_official_{kind}_contract", "") or "")
        or os.environ.get(f"OIRO_OFFICIAL_{kind.upper()}_CONTRACT", "")
    ).strip()


def _contract_directory() -> str:
    settings = get_settings()
    return (
        str(_settings_flag(settings, "oiro_official_pms_contract_dir", "") or "")
        or os.environ.get("OIRO_OFFICIAL_PMS_CONTRACT_DIR", "")
    ).strip()


def inspect_official_pms_contract(kind: str, db=None, hotel_id: int | None = None) -> dict[str, Any]:
    """Resolve the active official PMS/CRS contract. Never loads shipped examples."""
    kind = normalize_kind(kind)
    if hotel_id is None and db is not None:
        from app.services.tenancy import session_hotel_id

        hotel_id = session_hotel_id(db)

    path_raw = _explicit_contract_path(kind)
    if path_raw:
        path = Path(path_raw)
        if path.is_file() and not is_forbidden_example_path(path):
            accepted = _validate_contract(_read_json_file(path) or {}, kind, path=path)
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
            candidates.append(Path(directory) / tenant / f"{kind}.json")
        if hotel_id is not None:
            candidates.append(Path(directory) / str(int(hotel_id)) / f"{kind}.json")
        candidates.append(Path(directory) / f"{kind}.json")
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file() and not is_forbidden_example_path(candidate):
                accepted = _validate_contract(_read_json_file(candidate) or {}, kind, path=candidate)
                if accepted:
                    return {"source": "env_file", "path": str(candidate), "contract": accepted, "removable": False}

    if db is not None and hotel_id is not None:
        from app.models import SystemSetting
        from app.services.local_integrations import get_pms_contract_path

        local_path = get_pms_contract_path(db, hotel_id, kind)
        if local_path:
            path = Path(local_path)
            if path.is_file() and not is_forbidden_example_path(path):
                accepted = _validate_contract(_read_json_file(path) or {}, kind, path=path)
                if accepted:
                    return {
                        "source": "local_file",
                        "path": str(path),
                        "contract": accepted,
                        "removable": True,
                    }

        row = db.get(SystemSetting, setting_key_for(kind, hotel_id))
        if row and (row.value or "").strip():
            try:
                data = json.loads(row.value)
            except json.JSONDecodeError:
                data = None
            accepted = _validate_contract(data or {}, kind)
            if accepted:
                return {"source": "settings", "path": None, "contract": accepted, "removable": True}

    return {"source": "none", "path": None, "contract": None, "removable": False}


def public_pms_contract_status(kind: str, db=None, hotel_id: int | None = None) -> dict[str, Any]:
    info = inspect_official_pms_contract(kind, db, hotel_id=hotel_id)
    contract = info.get("contract")
    configured = contract_is_configured(contract)
    hosts = contract_hosts(contract) if configured else []
    actions = list((contract or {}).get("actions") or {}) if configured else []
    return {
        "contract_configured": configured,
        "contract_source": info["source"] if configured else "none",
        "contract_removable": bool(info.get("removable")) if configured else False,
        "contract_host": hosts[0] if hosts else "",
        "contract_hosts": hosts,
        "supported_actions": actions,
        "action_labels": public_action_labels(actions),
        "contract_enabled": configured,
        "contract_label": kind_label(kind, contract) if configured else KIND_LABELS.get(kind, kind),
    }


def load_official_pms_contract(kind: str, db=None, hotel_id: int | None = None) -> dict | None:
    """Load a configured official PMS/CRS contract. Never falls back to shipped examples."""
    return inspect_official_pms_contract(kind, db, hotel_id=hotel_id).get("contract")


def validate_pms_contract(data: Any, kind: str) -> dict:
    """Schema-check a PMS/CRS contract. Never opens a socket."""
    kind = normalize_kind(kind)
    return validate_official_contract(data, kind)


class OfficialPmsHttpClient(OfficialHttpClient):
    """Same contract-driven client, labeled for the PMS/CRS reserved hook."""

    def __init__(self, kind: str, contract: dict, credentials: dict):
        super().__init__(normalize_kind(kind), contract, credentials)
