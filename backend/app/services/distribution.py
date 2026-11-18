"""Packaged vs from-source distribution and install-key gate."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any

DISTRIBUTION_SOURCE = "source"
DISTRIBUTION_PACKAGED = "packaged"

INSTALL_KEY_PATTERN = re.compile(r"^OIRO-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
def _local_dir() -> Path:
    from app.config import LOCAL_DIR

    return LOCAL_DIR


def _install_state_file() -> Path:
    return _local_dir() / "install.state.json"

# Public pepper for fingerprinting user-supplied install keys (not a secret license server).
INSTALL_PEPPER = "oiro-local-desktop-install-v1"


def distribution_mode() -> str:
    raw = (os.environ.get("OIRO_DISTRIBUTION") or DISTRIBUTION_SOURCE).strip().lower()
    if raw in {DISTRIBUTION_PACKAGED, "desktop", "official"}:
        return DISTRIBUTION_PACKAGED
    return DISTRIBUTION_SOURCE


def is_packaged_distribution() -> bool:
    return distribution_mode() == DISTRIBUTION_PACKAGED


def normalize_install_key(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "")


def install_key_fingerprint(key: str) -> str:
    normalized = normalize_install_key(key)
    digest = hmac.new(INSTALL_PEPPER.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def validate_install_key_format(key: str) -> bool:
    normalized = normalize_install_key(key)
    if not normalized:
        return False
    # Dev / CI override for reproducible tests — never enable in official packaged builds.
    accept = (os.environ.get("OIRO_INSTALL_KEY_ACCEPT") or "").strip()
    if accept and hmac.compare_digest(normalized, accept.upper()):
        return True
    return bool(INSTALL_KEY_PATTERN.match(normalized))


def _read_state() -> dict[str, Any]:
    path = _install_state_file()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(payload: dict[str, Any]) -> None:
    directory = _local_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _install_state_file().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def install_activated() -> bool:
    if not is_packaged_distribution():
        return True
    override = (os.environ.get("OIRO_INSTALL_KEY") or "").strip()
    if override and validate_install_key_format(override):
        return True
    state = _read_state()
    return bool(state.get("activated")) and bool(state.get("fingerprint"))


def install_status_payload() -> dict[str, Any]:
    mode = distribution_mode()
    activated = install_activated()
    return {
        "distribution": mode,
        "packaged": mode == DISTRIBUTION_PACKAGED,
        "install_required": mode == DISTRIBUTION_PACKAGED,
        "activated": activated,
        "blocked": mode == DISTRIBUTION_PACKAGED and not activated,
    }


def activate_install_key(key: str) -> dict[str, Any]:
    if not is_packaged_distribution():
        return {
            **install_status_payload(),
            "message": "源码/开发模式无需安装密钥。",
        }
    if not validate_install_key_format(key):
        raise ValueError("安装密钥格式无效。正式包密钥形如 OIRO-XXXX-XXXX-XXXX。")
    normalized = normalize_install_key(key)
    state = {
        "activated": True,
        "fingerprint": install_key_fingerprint(normalized),
        "mode": DISTRIBUTION_PACKAGED,
    }
    _write_state(state)
    return {
        **install_status_payload(),
        "message": "安装密钥已验证，本机已解锁。",
    }


def reset_install_state_for_tests() -> None:
    path = _install_state_file()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def ensure_data_dirs() -> None:
    from app.config import DATA_DIR, LOCAL_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)