"""Persist model vendor keys on disk for local / desktop installs."""

from __future__ import annotations

import json
import os
from copy import copy
from pathlib import Path
from typing import Any

from app.config import Settings, clear_settings_cache, get_settings
from app.gateway.router import reset_gateway
from app.services.channels import encrypt_credentials, decrypt_credentials

def _local_dir():
    import os
    from app.config import PROJECT_ROOT

    return Path(os.environ.get("OIRO_DATA_DIR", str(PROJECT_ROOT / "data"))) / "local"


def _secrets_file() -> Path:
    return _local_dir() / "model-keys.enc.json"

VENDOR_FIELDS = {
    "deepseek": ("deepseek_api_key", "deepseek_base_url", "deepseek_model"),
    "qwen": ("qwen_api_key", "qwen_base_url", "qwen_model"),
    "openai": ("openai_api_key", "openai_base_url", "openai_model"),
    "gemini": ("gemini_api_key", "gemini_base_url", "gemini_model"),
    "grok": ("grok_api_key", "grok_base_url", "grok_model"),
    "self_hosted": ("self_hosted_api_key", "self_hosted_base_url", "self_hosted_model"),
}

MASK = "********"


def _load_blob() -> dict[str, Any]:
    path = _secrets_file()
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        outer = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    token = outer.get("payload")
    if not token:
        return {}
    decrypted = decrypt_credentials(token)
    try:
        return json.loads(decrypted.get("json") or "{}")
    except json.JSONDecodeError:
        return {}


def _save_blob(data: dict[str, Any]) -> None:
    directory = _local_dir()
    directory.mkdir(parents=True, exist_ok=True)
    token = encrypt_credentials({"json": json.dumps(data, ensure_ascii=False)})
    _secrets_file().write_text(json.dumps({"payload": token}, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_local_model_keys_to_environ() -> None:
    """Merge saved vendor keys into process env before Settings is constructed."""
    data = _load_blob()
    for vendor, fields in VENDOR_FIELDS.items():
        block = data.get(vendor) or {}
        if not isinstance(block, dict):
            continue
        for field in fields:
            if field.endswith("_api_key"):
                env_name = field.upper()
                value = str(block.get(field) or "").strip()
                if value:
                    os.environ[env_name] = value
            elif field.endswith("_base_url"):
                env_name = field.upper()
                value = str(block.get(field) or "").strip()
                if value:
                    os.environ[env_name] = value
            elif field.endswith("_model"):
                env_name = field.upper()
                value = str(block.get(field) or "").strip()
                if value:
                    os.environ[env_name] = value


def public_model_keys_payload() -> dict[str, Any]:
    settings = get_settings()
    stored = _load_blob()
    vendors: dict[str, Any] = {}
    for vendor, fields in VENDOR_FIELDS.items():
        block = stored.get(vendor) or {}
        key_field = fields[0]
        vendors[vendor] = {
            "configured": bool((block.get(key_field) or getattr(settings, key_field, "") or "").strip()),
            "masked_key": MASK if (block.get(key_field) or getattr(settings, key_field, "")) else "",
            "base_url": getattr(settings, fields[1], "") if vendor != "self_hosted" else (block.get(fields[1]) or getattr(settings, fields[1], "")),
            "model": getattr(settings, fields[2], "") if vendor != "self_hosted" else (block.get(fields[2]) or getattr(settings, fields[2], "")),
        }
    return {
        "vendors": vendors,
        "storage": "local_disk",
        "path_hint": "model-keys.enc.json",
        "note": "密钥仅保存在本机，不会写入仓库或审计日志。",
    }


def save_model_keys(payload: dict[str, Any]) -> dict[str, Any]:
    current = _load_blob()
    incoming = payload.get("vendors") or payload
    if not isinstance(incoming, dict):
        raise ValueError("无效的模型密钥载荷")
    merged = copy(current)
    for vendor, fields in VENDOR_FIELDS.items():
        block = incoming.get(vendor)
        if block is None:
            continue
        if not isinstance(block, dict):
            continue
        existing = dict(merged.get(vendor) or {})
        for field in fields:
            if field not in block:
                continue
            value = block.get(field)
            if value is None:
                continue
            text = str(value).strip()
            if text == MASK:
                continue
            if not text:
                existing.pop(field, None)
            else:
                existing[field] = text
        if existing:
            merged[vendor] = existing
        else:
            merged.pop(vendor, None)
    _save_blob(merged)
    apply_local_model_keys_to_environ()
    clear_settings_cache()
    reset_gateway()
    return public_model_keys_payload()