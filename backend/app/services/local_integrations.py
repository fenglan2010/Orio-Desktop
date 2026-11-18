"""Local filesystem bindings for OTA/PMS official contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.ota.official_http import (
    ContractValidationError,
    PLATFORMS,
    _read_json_file,
    _validate_contract,
    is_forbidden_example_path,
    public_contract_status,
)
from app.adapters.pms.official_http import KINDS, public_pms_contract_status
from app.adapters.pms.official_http import (
    _read_json_file as pms_read_json_file,
    _validate_contract as pms_validate_contract,
    inspect_official_pms_contract,
    is_forbidden_example_path as pms_forbidden,
)
from app.models import ChannelConnection, SystemSetting


def ota_contract_path_key(hotel_id: int, platform: str) -> str:
    return f"hotel:{int(hotel_id)}:ota_contract_path:{platform.strip().lower()}"


def pms_contract_path_key(hotel_id: int, kind: str) -> str:
    return f"hotel:{int(hotel_id)}:pms_contract_path:{kind.strip().lower()}"


def pms_api_endpoint_key(hotel_id: int, kind: str) -> str:
    return f"hotel:{int(hotel_id)}:pms_api_endpoint:{kind.strip().lower()}"


def _get_setting(db: Session, key: str) -> str:
    row = db.get(SystemSetting, key)
    return (row.value if row else "") or ""


def get_ota_contract_path(db: Session, hotel_id: int, platform: str) -> str:
    return _get_setting(db, ota_contract_path_key(hotel_id, platform)).strip()


def get_pms_contract_path(db: Session, hotel_id: int, kind: str) -> str:
    return _get_setting(db, pms_contract_path_key(hotel_id, kind)).strip()


def get_pms_api_endpoint(db: Session, hotel_id: int, kind: str) -> str:
    return _get_setting(db, pms_api_endpoint_key(hotel_id, kind)).strip()


def contract_from_local_path(path_raw: str, platform: str) -> dict[str, Any] | None:
    path = Path((path_raw or "").strip())
    if not path.is_file():
        return None
    if is_forbidden_example_path(path):
        return None
    data = _read_json_file(path) or {}
    return _validate_contract(data, platform, path=path)


def pms_contract_from_local_path(path_raw: str, kind: str) -> dict | None:
    path = Path((path_raw or "").strip())
    if not path.is_file():
        return None
    if pms_forbidden(path):
        return None
    data = pms_read_json_file(path) or {}
    return pms_validate_contract(data, kind, path=path)


def save_ota_contract_path(
    db: Session,
    hotel_id: int,
    platform: str,
    path_raw: str | None,
    *,
    account_hint: str | None = None,
) -> dict[str, Any]:
    platform = platform.strip().lower()
    if platform not in PLATFORMS:
        raise ValueError("不支持的 OTA 渠道")
    key = ota_contract_path_key(hotel_id, platform)
    cleaned = (path_raw or "").strip()
    row = (
        db.query(ChannelConnection)
        .filter(ChannelConnection.hotel_id == hotel_id, ChannelConnection.platform == platform)
        .one_or_none()
    )
    if cleaned:
        contract = contract_from_local_path(cleaned, platform)
        if contract is None:
            raise ContractValidationError("契约文件不存在、不是有效官方契约，或是文档示例，不能加载。")
        if account_hint:
            row_account = (account_hint or "").strip()
            if row and row.platform_account_id and row_account and row.platform_account_id != row_account:
                raise ValueError("契约路径须与当前保存的 OTA 账号属于同一渠道连接。")
        setting = db.get(SystemSetting, key)
        if setting is None:
            setting = SystemSetting(key=key, value=cleaned)
            db.add(setting)
        else:
            setting.value = cleaned
        bind_key = f"hotel:{int(hotel_id)}:ota_contract_bound_account:{platform}"
        bind_row = db.get(SystemSetting, bind_key)
        bound = (row.platform_account_id if row else "") or (account_hint or "")
        if bound:
            if bind_row is None:
                bind_row = SystemSetting(key=bind_key, value=bound)
                db.add(bind_row)
            else:
                bind_row.value = bound
    else:
        setting = db.get(SystemSetting, key)
        if setting is not None:
            db.delete(setting)
    db.commit()
    status = public_contract_status(platform, db, hotel_id=hotel_id)
    return {
        "platform": platform,
        "contract_file_path": cleaned,
        "contract_bound_account": _get_setting(db, f"hotel:{int(hotel_id)}:ota_contract_bound_account:{platform}"),
        **status,
    }


def save_pms_local_config(
    db: Session,
    hotel_id: int,
    kind: str,
    *,
    contract_file_path: str | None = None,
    api_endpoint: str | None = None,
) -> dict[str, Any]:
    kind = kind.strip().lower()
    if kind not in KINDS:
        raise ValueError("仅支持 PMS 或 CRS")
    if contract_file_path is not None:
        cleaned = (contract_file_path or "").strip()
        key = pms_contract_path_key(hotel_id, kind)
        if cleaned:
            if pms_contract_from_local_path(cleaned, kind) is None:
                raise ContractValidationError("PMS/CRS 契约文件无效或是文档示例。")
            row = db.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(key=key, value=cleaned)
                db.add(row)
            else:
                row.value = cleaned
        else:
            row = db.get(SystemSetting, key)
            if row is not None:
                db.delete(row)
    if api_endpoint is not None:
        endpoint = (api_endpoint or "").strip()
        key = pms_api_endpoint_key(hotel_id, kind)
        if endpoint:
            if "://" not in endpoint:
                raise ValueError("PMS API 位置须为完整 URL（由你方运维/厂商提供）。")
            row = db.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(key=key, value=endpoint)
                db.add(row)
            else:
                row.value = endpoint
        else:
            row = db.get(SystemSetting, key)
            if row is not None:
                db.delete(row)
    db.commit()
    return {
        "kind": kind,
        "contract_file_path": get_pms_contract_path(db, hotel_id, kind),
        "api_endpoint": get_pms_api_endpoint(db, hotel_id, kind),
        **public_pms_contract_status(kind, db, hotel_id=hotel_id),
    }


def public_local_integrations(db: Session, hotel_id: int) -> dict[str, Any]:
    ota: dict[str, Any] = {}
    for platform in PLATFORMS:
        path = get_ota_contract_path(db, hotel_id, platform)
        bound = _get_setting(db, f"hotel:{int(hotel_id)}:ota_contract_bound_account:{platform}")
        meta = public_contract_status(platform, db, hotel_id=hotel_id)
        ota[platform] = {
            "contract_file_path": path,
            "contract_bound_account": bound,
            "contract_enabled": meta.get("contract_enabled"),
            "contract_source": meta.get("contract_source"),
        }
    pms: dict[str, Any] = {}
    for kind in KINDS:
        pms[kind] = {
            "contract_file_path": get_pms_contract_path(db, hotel_id, kind),
            "api_endpoint": get_pms_api_endpoint(db, hotel_id, kind),
            **public_pms_contract_status(kind, db, hotel_id=hotel_id),
        }
    return {"ota": ota, "pms": pms}