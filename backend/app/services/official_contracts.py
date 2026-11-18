"""Install, validate, and uninstall official write contracts (settings-stored)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.ota.official_http import (
    ContractValidationError,
    contract_hosts,
    inspect_official_contract,
    public_contract_status,
    setting_key_for,
    validate_official_contract,
)
from app.models import SystemSetting
from app.services.workflow import write_audit


def parse_contract_payload(body: Any, platform: str) -> dict:
    """Accept {contract: {...}}, a raw object, or a JSON string. Never opens a socket."""
    if body is None:
        raise ContractValidationError("请提供官方契约 JSON")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            raise ContractValidationError("契约不是有效的 JSON") from None
    if not isinstance(body, dict):
        raise ContractValidationError("契约必须是 JSON 对象")
    if "contract" in body and not any(key in body for key in ("actions", "base_url", "platform", "auth")):
        inner = body.get("contract")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                raise ContractValidationError("契约不是有效的 JSON") from None
        if not isinstance(inner, dict):
            raise ContractValidationError("契约必须是 JSON 对象")
        body = inner
    return validate_official_contract(body, platform)


def validation_report(body: Any, platform: str) -> dict[str, Any]:
    try:
        contract = parse_contract_payload(body, platform)
    except ContractValidationError as exc:
        return {"valid": False, "errors": exc.errors, "message": exc.message, "contract": None}
    hosts = contract_hosts(contract)
    return {
        "valid": True,
        "errors": [],
        "message": "契约校验通过，尚未保存，也未发起外部请求。",
        "platform": platform,
        "supported_actions": list((contract.get("actions") or {}).keys()),
        "contract_host": hosts[0] if hosts else "",
        "contract_hosts": hosts,
    }


def install_official_contract(db: Session, platform: str, body: Any, *, actor: str, hotel_id: int) -> dict[str, Any]:
    contract = parse_contract_payload(body, platform)
    key = setting_key_for(platform, hotel_id)
    raw = json.dumps(contract, ensure_ascii=False)
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value=raw)
        db.add(row)
    else:
        row.value = raw
        row.updated_at = datetime.utcnow()
    db.commit()
    current = inspect_official_contract(platform, db, hotel_id=hotel_id)
    hosts = contract_hosts(contract)
    write_audit(
        db,
        actor=actor,
        action="contract_install",
        entity_type="official_contract",
        entity_id=platform,
        hotel_id=hotel_id,
        after={
            "platform": platform,
            "stored_in": "settings",
            "active_source": current.get("source"),
            "actions": list((contract.get("actions") or {}).keys()),
            "hosts": hosts,
            "version": contract.get("version"),
        },
        reason="安装官方写入契约（审计不含密钥、请求头或完整契约正文）",
    )
    note = ""
    if current.get("source") == "env_file":
        note = "契约已保存到服务器设置，但当前生效的是服务器文件契约（环境变量或目录），设置中的契约在文件被移除后才会生效。"
    return {
        "installed": True,
        "message": note or "官方契约已安装。本仓库不会编造生产环境 URL，主机名来自你提供的合作方契约。",
        "active_source": current.get("source"),
        "settings_stored": True,
        **public_contract_status(platform, db, hotel_id=hotel_id),
    }


def uninstall_official_contract(db: Session, platform: str, *, actor: str, hotel_id: int) -> dict[str, Any]:
    current = inspect_official_contract(platform, db, hotel_id=hotel_id)
    if current.get("source") == "env_file":
        raise PermissionError("当前官方契约由服务器文件（环境变量或契约目录）管理，不能从运维端卸载，也不会删除该文件。")
    key = setting_key_for(platform, hotel_id)
    row = db.get(SystemSetting, key)
    if current.get("source") != "settings" and row is None:
        raise FileNotFoundError("当前没有可卸载的设置契约。")
    if row is not None:
        db.delete(row)
        db.commit()
    write_audit(
        db,
        actor=actor,
        action="contract_uninstall",
        entity_type="official_contract",
        entity_id=platform,
        after={"platform": platform, "active_source": "none"},
        reason="卸载设置中保存的官方写入契约",
        hotel_id=hotel_id,
    )
    status = public_contract_status(platform, db, hotel_id=hotel_id)
    return {
        "uninstalled": True,
        "message": "已卸载设置中的官方契约。当前没有生效的官方契约。",
        **status,
    }
