"""Contract-driven official HTTP write client.

This module never invents Ctrip / Meituan / Fliggy production URLs. Outbound
HTTP is sent only when a caller supplies a configured official contract
(base URL + auth scheme + path + request/response mapping) together with
credentials and a write-enabled flag. Default demo installs no production
contracts. Tests should point the contract at a local HTTP server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import httpx

from app.adapters.ota.official_write import (
    MODE_LIVE,
    OUTCOME_ACCEPTED,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_REFUSED,
    OUTCOME_TIMEOUT,
    build_write_receipt,
    new_request_id,
)
from app.config import get_settings

PLATFORMS = ("ctrip", "meituan", "fliggy")
DEFAULT_TIMEOUT = 8.0
SUCCESS_HTTP = {200, 201, 202}

# Reasons that must never open a socket and must not be retried as transient.
REFUSE_NO_SOCKET = frozenset(
    {
        "missing_credentials",
        "missing_contract",
        "channel_unauthorized",
        "write_disabled",
        "review_reply_not_auto_posted",
        "action_not_in_contract",
        "live_api_not_connected",
        "http_4xx",
    }
)


def platform_env_prefix(platform: str) -> str:
    return (platform or "ctrip").strip().lower()


def _settings_flag(settings, name: str, default=False):
    return getattr(settings, name, default)


WRITE_TRUE = {"1", "true", "yes", "on"}
WRITE_FALSE = {"0", "false", "no", "off"}

ACTION_LABELS = {
    "apply_price": "调价",
    "update_price": "调价",
    "update_rates": "调价",
    "rates_increment": "调价",
    "rate_update": "调价",
    "campaign": "活动",
    "apply_campaign": "活动",
    "enroll_campaign": "活动",
    "signup_campaign": "活动",
    "verify": "连通校验",
    "review_reply": "评价回复",
    "fetch_metrics": "采集",
    "ingest": "采集",
}


def action_label(name: str) -> str:
    key = (name or "").strip()
    return ACTION_LABELS.get(key, key)


def public_action_labels(actions) -> list[str]:
    labels: list[str] = []
    for name in actions or []:
        label = action_label(str(name))
        if label and label not in labels:
            labels.append(label)
    return labels


def write_flag_setting_key(platform: str, hotel_id: int) -> str:
    return f"hotel:{int(hotel_id)}:{platform_env_prefix(platform)}_write_enabled"


def platform_write_enabled(platform: str, db=None, hotel_id: int | None = None) -> bool:
    platform = platform_env_prefix(platform)
    settings = get_settings()
    enabled = bool(_settings_flag(settings, f"{platform}_write_enabled", False))
    raw = os.environ.get(f"{platform.upper()}_WRITE_ENABLED", "").strip().lower()
    if raw in WRITE_TRUE:
        return True
    if raw in WRITE_FALSE:
        return False
    if enabled:
        return True
    if db is not None and hotel_id is not None:
        from app.models import SystemSetting

        row = db.get(SystemSetting, write_flag_setting_key(platform, hotel_id))
        if row and str(row.value or "").strip().lower() in WRITE_TRUE:
            return True
    return False


def set_platform_write_enabled(db, hotel_id: int, platform: str, enabled: bool) -> bool:
    from datetime import datetime
    from app.models import SystemSetting

    key = write_flag_setting_key(platform, hotel_id)
    value = "true" if enabled else "false"
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = datetime.utcnow()
    db.commit()
    return enabled


def env_credentials(platform: str) -> dict[str, str]:
    platform = platform_env_prefix(platform)
    settings = get_settings()
    key = (
        str(_settings_flag(settings, f"{platform}_write_key", "") or "")
        or os.environ.get(f"{platform.upper()}_WRITE_KEY", "")
        or os.environ.get(f"{platform.upper()}_API_KEY", "")
    ).strip()
    secret = (
        str(_settings_flag(settings, f"{platform}_write_secret", "") or "")
        or os.environ.get(f"{platform.upper()}_WRITE_SECRET", "")
        or os.environ.get(f"{platform.upper()}_API_SECRET", "")
    ).strip()
    hotel_id = (
        str(_settings_flag(settings, f"{platform}_hotel_id", "") or "")
        or os.environ.get(f"{platform.upper()}_HOTEL_ID", "")
    ).strip()
    session = (
        str(_settings_flag(settings, f"{platform}_session", "") or "")
        or os.environ.get(f"{platform.upper()}_SESSION", "")
    ).strip()
    creds: dict[str, str] = {}
    if key:
        creds["key"] = key
    if secret:
        creds["secret"] = secret
    if hotel_id:
        creds["platform_hotel_id"] = hotel_id
    if session:
        creds["session"] = session
    return creds


def _read_json_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _base_url(contract: dict | None, action: str | None = None) -> str:
    if not contract:
        return ""
    if action:
        spec = (contract.get("actions") or {}).get(action) or {}
        url = str(spec.get("base_url") or "").strip()
        if url:
            return url.rstrip("/")
    return str(contract.get("base_url") or "").strip().rstrip("/")


def contract_is_configured(contract: dict | None, action: str | None = None) -> bool:
    if not isinstance(contract, dict):
        return False
    actions = contract.get("actions") or {}
    if not isinstance(actions, dict) or not actions:
        return False
    if action:
        if action not in actions:
            return False
        return bool(_base_url(contract, action) and (actions[action] or {}).get("path"))
    return any(
        _base_url(contract, name) and (spec or {}).get("path")
        for name, spec in actions.items()
        if isinstance(spec, dict)
    )



class ContractValidationError(ValueError):
    """Raised when a submitted official contract is not acceptable."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.errors = list(errors or [message])


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
SETTING_KEY_PREFIX = "official_contract_"
SHIPPED_EXAMPLE_DIR_PARTS = ("docs", "official-contracts")


def setting_key_for(platform: str, hotel_id: int | None = None) -> str:
    base = f"{SETTING_KEY_PREFIX}{platform_env_prefix(platform)}"
    if hotel_id is None:
        return base
    return f"hotel:{int(hotel_id)}:{base}"


def is_local_hostname(host: str | None) -> bool:
    value = (host or "").strip().lower().rstrip(".")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value in LOCAL_HOSTS


def is_forbidden_example_path(path: Path | str | None) -> bool:
    """True when a file is a shipped documentation example and must not be loaded."""
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path)
    name = resolved.name.lower()
    if name.endswith(".example.json") or ".example." in name:
        return True
    parts = [part.lower() for part in resolved.parts]
    for index in range(len(parts) - 1):
        if parts[index] == "docs" and parts[index + 1] == "official-contracts":
            return True
    return False


def _check_base_url(url: str, *, label: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ContractValidationError(f"{label}缺少 base_url")
    parsed = urlparse(raw)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise ContractValidationError(f"{label}的 base_url 必须是 http(s) 地址")
    if parsed.username or parsed.password:
        raise ContractValidationError(f"{label}的 base_url 不能包含用户名或密码")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ContractValidationError(f"{label}的 base_url 缺少主机名")
    if parsed.scheme == "http" and not is_local_hostname(host):
        raise ContractValidationError("官方契约必须使用 https；仅本机测试（localhost / 127.0.0.1）允许 http")
    return raw.rstrip("/")


def contract_hosts(contract: dict | None) -> list[str]:
    hosts: list[str] = []
    if not isinstance(contract, dict):
        return hosts
    urls: list[str] = []
    if contract.get("base_url"):
        urls.append(str(contract.get("base_url")))
    for spec in (contract.get("actions") or {}).values():
        if isinstance(spec, dict) and spec.get("base_url"):
            urls.append(str(spec.get("base_url")))
    for url in urls:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").strip()
        if not host:
            continue
        port = parsed.port
        default = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
        label = host if (port is None or default) else f"{host}:{port}"
        if label not in hosts:
            hosts.append(label)
    return hosts


def validate_official_contract(data: Any, platform: str) -> dict:
    """Schema-check a contract. Never opens a socket. Raises ContractValidationError."""
    platform = platform_env_prefix(platform)
    if data is None:
        raise ContractValidationError("契约不能为空")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            raise ContractValidationError("契约不是有效的 JSON") from None
    if not isinstance(data, dict):
        raise ContractValidationError("契约必须是 JSON 对象")
    if data.get("example") is True:
        raise ContractValidationError("这是文档示例契约，不能作为生产契约安装。请使用合作方提供的正式契约。")

    declared = str(data.get("platform") or "").strip().lower()
    if not declared:
        raise ContractValidationError("契约必须声明 platform")
    if declared != platform:
        raise ContractValidationError(f"契约 platform（{declared}）与当前渠道（{platform}）不一致")

    actions = data.get("actions")
    if not isinstance(actions, dict) or not actions:
        raise ContractValidationError("契约至少需要一个写入动作（actions）")

    errors: list[str] = []
    complete = 0
    for name, spec in actions.items():
        label = f"动作 {name}"
        if not isinstance(spec, dict):
            errors.append(f"{label}必须是对象")
            continue
        method = str(spec.get("method") or "").strip().upper()
        if not method:
            errors.append(f"{label}缺少 method")
        elif method not in ALLOWED_METHODS:
            errors.append(f"{label}的 method 不受支持")
        path = str(spec.get("path") or "").strip()
        if not path:
            errors.append(f"{label}缺少 path")
        elif "://" in path:
            errors.append(f"{label}的 path 不能是完整 URL，请把主机放在 base_url")
        auth = str(spec.get("auth") or data.get("auth") or "").strip()
        if not auth:
            errors.append(f"{label}缺少 auth 鉴权方案（可写在动作上或契约根上）")
        try:
            _check_base_url(spec.get("base_url") or data.get("base_url") or "", label=label)
        except ContractValidationError as exc:
            errors.extend(exc.errors)
            continue
        if method and path and auth:
            complete += 1
    if not str(data.get("auth") or "").strip() and not any(
        isinstance(spec, dict) and str(spec.get("auth") or "").strip() for spec in actions.values()
    ):
        errors.append("契约必须指定 auth 鉴权方案")
    if complete == 0 and not errors:
        errors.append("至少需要一个包含 method、path 和可用 base_url 的写入动作")
    if errors:
        raise ContractValidationError(errors[0], errors)
    return data


def _validate_contract(data: dict, platform: str, *, path: Path | str | None = None) -> dict | None:
    if not isinstance(data, dict):
        return None
    if is_forbidden_example_path(path):
        return None
    try:
        return validate_official_contract(data, platform)
    except ContractValidationError:
        return None


def _explicit_contract_path(platform: str) -> str:
    settings = get_settings()
    return (
        str(_settings_flag(settings, f"oiro_official_contract_{platform}", "") or "")
        or os.environ.get(f"OIRO_OFFICIAL_CONTRACT_{platform.upper()}", "")
    ).strip()


def _contract_directory() -> str:
    settings = get_settings()
    return (
        str(_settings_flag(settings, "oiro_official_contract_dir", "") or "")
        or os.environ.get("OIRO_OFFICIAL_CONTRACT_DIR", "")
    ).strip()


def inspect_official_contract(platform: str, db=None, hotel_id: int | None = None) -> dict[str, Any]:
    """Resolve the active official contract and its source. Never loads shipped examples."""
    platform = platform_env_prefix(platform)
    if hotel_id is None:
        from app.services.tenancy import session_hotel_id

        hotel_id = session_hotel_id(db)

    path_raw = _explicit_contract_path(platform)
    if path_raw:
        path = Path(path_raw)
        if path.is_file() and not is_forbidden_example_path(path):
            accepted = _validate_contract(_read_json_file(path) or {}, platform, path=path)
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
            candidates.append(Path(directory) / tenant / f"{platform}.json")
        if hotel_id is not None:
            candidates.append(Path(directory) / str(int(hotel_id)) / f"{platform}.json")
        # Process-level fallback used by single-hotel tests; prefer tenant-scoped files above.
        candidates.append(Path(directory) / f"{platform}.json")
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file() and not is_forbidden_example_path(candidate):
                accepted = _validate_contract(_read_json_file(candidate) or {}, platform, path=candidate)
                if accepted:
                    return {"source": "env_file", "path": str(candidate), "contract": accepted, "removable": False}

    if db is not None and hotel_id is not None:
        from app.models import SystemSetting
        from app.services.local_integrations import get_ota_contract_path

        local_path = get_ota_contract_path(db, hotel_id, platform)
        if local_path:
            path = Path(local_path)
            if path.is_file() and not is_forbidden_example_path(path):
                accepted = _validate_contract(_read_json_file(path) or {}, platform, path=path)
                if accepted:
                    return {
                        "source": "local_file",
                        "path": str(path),
                        "contract": accepted,
                        "removable": True,
                    }

        row = db.get(SystemSetting, setting_key_for(platform, hotel_id))
        if row and (row.value or "").strip():
            try:
                data = json.loads(row.value)
            except json.JSONDecodeError:
                data = None
            accepted = _validate_contract(data or {}, platform)
            if accepted:
                return {"source": "settings", "path": None, "contract": accepted, "removable": True}

    return {"source": "none", "path": None, "contract": None, "removable": False}


def public_contract_status(platform: str, db=None, hotel_id: int | None = None) -> dict[str, Any]:
    info = inspect_official_contract(platform, db, hotel_id=hotel_id)
    contract = info.get("contract")
    configured = contract_is_configured(contract)
    hosts = contract_hosts(contract) if configured else []
    return {
        "contract_configured": configured,
        "contract_source": info["source"] if configured else "none",
        "contract_removable": bool(info.get("removable")) if configured else False,
        "contract_host": hosts[0] if hosts else "",
        "contract_hosts": hosts,
        "supported_actions": list((contract or {}).get("actions") or {}) if configured else [],
        "action_labels": public_action_labels(list((contract or {}).get("actions") or {}) if configured else []),
        "contract_enabled": configured,
    }


def load_official_contract(platform: str, db=None, hotel_id: int | None = None) -> dict | None:
    """Load a configured official contract. Never falls back to shipped examples."""
    return inspect_official_contract(platform, db, hotel_id=hotel_id).get("contract")


def _dig(data: Any, path: str | None) -> Any:
    if not path:
        return None
    current = data
    for part in str(path).split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _render(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{{") and stripped.endswith("}}") and stripped.count("{{") == 1:
            key = stripped[2:-2].strip()
            return context.get(key, "")
        rendered = value
        for key, item in context.items():
            token = "{{" + key + "}}"
            if token in rendered and not isinstance(item, (dict, list)):
                rendered = rendered.replace(token, "" if item is None else str(item))
        return rendered
    if isinstance(value, list):
        return [_render(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, context) for key, item in value.items()}
    return value


def _action_spec(contract: dict, action: str) -> dict:
    return dict((contract.get("actions") or {}).get(action) or {})


def _build_headers(contract: dict, spec: dict, credentials: dict, body: bytes) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    for source in (contract.get("headers"), spec.get("headers")):
        if isinstance(source, dict):
            headers.update({str(k): str(v) for k, v in source.items()})
    scheme = str(spec.get("auth") or contract.get("auth") or "header_api_key").strip().lower()
    key = (
        credentials.get("key")
        or credentials.get("token")
        or credentials.get("account")
        or credentials.get("username")
        or credentials.get("platform_account_id")
        or ""
    )
    secret = credentials.get("secret") or credentials.get("password") or ""
    header_name = str(spec.get("auth_header") or contract.get("auth_header") or "").strip()
    if scheme in {"none", "off"}:
        return headers
    if scheme == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif scheme == "basic":
        import base64

        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    elif scheme == "hmac_sha256":
        import hashlib
        import hmac

        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers[header_name or "X-Signature"] = digest
        if key:
            headers[str(spec.get("key_header") or contract.get("key_header") or "X-Api-Key")] = key
    else:
        # header_api_key and any unknown scheme: send the key in a header.
        # Partner-specific signers (TOP / MWS, etc.) belong in the contract
        # as explicit auth + headers, not as invented platform defaults.
        headers[header_name or "X-Api-Key"] = key
    return headers


def _map_items(intended: list[dict], official_items: Any, mapping: dict) -> list[dict] | None:
    if not isinstance(official_items, list):
        return None
    by_id: dict[str, dict] = {}
    id_path = mapping.get("item_id_path") or "date"
    status_path = mapping.get("item_status_path") or "status"
    error_path = mapping.get("item_error_path") or "error"
    accepted_values = {str(v).lower() for v in (mapping.get("item_accepted_values") or ["accepted", "success", "ok", "true", "0000"])}
    failed_values = {str(v).lower() for v in (mapping.get("item_failed_values") or ["failed", "error", "rejected", "false"])}
    for raw in official_items:
        if not isinstance(raw, dict):
            continue
        ident = raw.get(id_path) or raw.get("date") or raw.get("id")
        status = str(_dig(raw, status_path) if "." in str(status_path) else raw.get(status_path) or "").strip().lower()
        accepted = status in accepted_values or raw.get("accepted") is True
        if status in failed_values:
            accepted = False
        by_id[str(ident)] = {
            "accepted": accepted,
            "status": "accepted" if accepted else (status or "failed"),
            "error": raw.get(error_path) or raw.get("message") or ("" if accepted else "官方回执：该行未接受"),
            "actual_price": raw.get("actual_price") or raw.get("price") or raw.get("list_price"),
            "official": raw,
        }
    mapped: list[dict] = []
    for item in intended:
        ident = str(item.get("date") or item.get("date_from") or item.get("review_id") or item.get("campaign_name") or "")
        official = by_id.get(ident) or (next(iter(by_id.values()), None) if len(intended) == 1 and len(by_id) == 1 else None)
        if official:
            row = {**item, "accepted": official["accepted"], "status": official["status"]}
            if official.get("error"):
                row["error"] = official["error"]
            if official.get("actual_price") is not None:
                row["actual_price"] = official["actual_price"]
            mapped.append(row)
        else:
            mapped.append({**item, "accepted": False, "status": "missing", "error": "官方回执中缺少该行项目"})
    return mapped



def perform_http_request(method: str, url: str, *, headers: dict, content: bytes, timeout: float) -> httpx.Response:
    """Single outbound hook so tests can assert no socket without touching TestClient."""
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        return client.request(method, url, headers=headers, content=content)


class OfficialHttpError(Exception):
    def __init__(self, message: str, *, retryable: bool, outcome: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.outcome = outcome
        self.status_code = status_code
        self.body = body


class OfficialHttpClient:
    """Send one official write described by a contract. No default production hosts."""

    def __init__(self, platform: str, contract: dict, credentials: dict):
        self.platform = platform_env_prefix(platform)
        self.contract = contract
        self.credentials = dict(credentials or {})

    def can_send(self, action: str) -> bool:
        return contract_is_configured(self.contract, action)

    def send_write(
        self,
        action: str,
        payload: dict,
        items: list[dict],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.can_send(action):
            raise OfficialHttpError("官方契约未包含该写入动作，已拒绝出站请求。", retryable=False, outcome=OUTCOME_REFUSED)

        spec = _action_spec(self.contract, action)
        base = _base_url(self.contract, action)
        path = str(spec.get("path") or "").strip()
        if not base or not path:
            raise OfficialHttpError("官方契约缺少 base URL 或路径，已拒绝出站请求。", retryable=False, outcome=OUTCOME_REFUSED)
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OfficialHttpError("官方契约的 base URL 无效，已拒绝出站请求。", retryable=False, outcome=OUTCOME_REFUSED)

        request_id = request_id or new_request_id()
        context = {
            "request_id": request_id,
            "platform": self.platform,
            "action": action,
            "platform_hotel_id": self.credentials.get("platform_hotel_id") or payload.get("platform_hotel_id") or "",
            "platform_account_id": self.credentials.get("platform_account_id") or payload.get("platform_account_id") or "",
            "checkpoint_id": payload.get("checkpoint_id") or "",
            "items": items,
            "payload": payload,
        }
        template = spec.get("request_template")
        if not isinstance(template, dict):
            template = {"request_id": "{{request_id}}", "hotel_id": "{{platform_hotel_id}}", "items": "{{items}}"}
        body_obj = _render(template, context)
        body_bytes = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        headers = _build_headers(self.contract, spec, self.credentials, body_bytes)
        method = str(spec.get("method") or "POST").upper()
        timeout = float(spec.get("timeout_seconds") or self.contract.get("timeout_seconds") or DEFAULT_TIMEOUT)
        url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))

        try:
            response = perform_http_request(method, url, headers=headers, content=body_bytes, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise OfficialHttpError("官方接口写入超时，未收到确认。", retryable=True, outcome=OUTCOME_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise OfficialHttpError(f"官方接口网络错误：{exc.__class__.__name__}", retryable=True, outcome=OUTCOME_FAILED) from exc

        return self._receipt_from_response(
            action=action,
            payload=payload,
            items=items,
            request_id=request_id,
            spec=spec,
            response=response,
        )

    def _receipt_from_response(
        self,
        *,
        action: str,
        payload: dict,
        items: list[dict],
        request_id: str,
        spec: dict,
        response: httpx.Response,
    ) -> dict[str, Any]:
        mapping = spec.get("response_mapping") if isinstance(spec.get("response_mapping"), dict) else {}
        success_http = set(mapping.get("success_http") or list(SUCCESS_HTTP))
        status_code = response.status_code
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}

        if status_code >= 500:
            raise OfficialHttpError(
                f"官方接口返回 HTTP {status_code}，写入失败，可按瞬时错误重试。",
                retryable=True,
                outcome=OUTCOME_FAILED,
                status_code=status_code,
                body=body,
            )
        if status_code >= 400 or status_code not in success_http:
            message = ""
            if isinstance(body, dict):
                message = str(_dig(body, mapping.get("message_path") or "message") or body.get("error") or "")
            raise OfficialHttpError(
                message or f"官方接口拒绝写入（HTTP {status_code}），不会作为瞬时错误重试。",
                retryable=False,
                outcome=OUTCOME_REFUSED,
                status_code=status_code,
                body=body,
            )

        accepted_path = mapping.get("accepted_path") or "accepted"
        official_accepted = _dig(body, accepted_path) if isinstance(body, dict) else None
        accepted_values = mapping.get("accepted_values") or [True, "true", "success", "0000", "ok"]
        if official_accepted is None:
            overall_accepted = True
        else:
            overall_accepted = official_accepted in accepted_values or str(official_accepted).lower() in {str(v).lower() for v in accepted_values}

        official_items = _dig(body, mapping.get("items_path") or "items") if isinstance(body, dict) else None
        receipt_items = _map_items(items, official_items, mapping)
        if receipt_items is None:
            receipt_items = []
            for item in items:
                row = {**item, "accepted": overall_accepted, "status": "accepted" if overall_accepted else "failed"}
                if overall_accepted and item.get("intended_price") is not None:
                    row["actual_price"] = item.get("intended_price")
                if not overall_accepted:
                    row["error"] = "官方回执未接受该写入"
                receipt_items.append(row)

        accepted_count = sum(1 for item in receipt_items if item.get("accepted"))
        if receipt_items and accepted_count == len(receipt_items):
            outcome = OUTCOME_ACCEPTED
            accepted = True
        elif accepted_count > 0:
            outcome = OUTCOME_PARTIAL
            accepted = False
        elif overall_accepted and not receipt_items:
            outcome = OUTCOME_ACCEPTED
            accepted = True
        else:
            outcome = OUTCOME_FAILED
            accepted = False

        receipt_id = None
        message = ""
        echoed_request = request_id
        if isinstance(body, dict):
            receipt_id = _dig(body, mapping.get("receipt_id_path") or "receipt_id") or body.get("id")
            message = str(_dig(body, mapping.get("message_path") or "message") or "")
            echoed = _dig(body, mapping.get("request_id_path") or "request_id")
            if echoed:
                echoed_request = str(echoed)
        if receipt_id is not None:
            receipt_id = str(receipt_id)

        if not message:
            if outcome == OUTCOME_ACCEPTED:
                message = "官方接口已接受写入，已记录真实回执。"
            elif outcome == OUTCOME_PARTIAL:
                message = "官方接口部分接受写入，请核对未成功的行项目。"
            else:
                message = "官方接口未接受写入。"

        extra = {"http_status": status_code, "official_response": body if isinstance(body, dict) else {"raw": str(body)}}
        if outcome == OUTCOME_PARTIAL:
            extra["partial"] = True
        return build_write_receipt(
            platform=self.platform,
            mode=MODE_LIVE,
            accepted=accepted,
            message=message,
            payload=payload,
            checkpoint_id=(payload or {}).get("checkpoint_id") or "",
            action=action,
            items=receipt_items,
            outcome=outcome,
            request_id=str(echoed_request),
            receipt_id=receipt_id,
            extra=extra,
        )

    def send_read(self, action: str, payload: dict | None = None) -> dict[str, Any]:
        """Send a contract-defined read/ingest action. Never invents a URL."""
        if not self.can_send(action):
            raise OfficialHttpError("官方契约未包含该采集动作，已拒绝出站请求。", retryable=False, outcome=OUTCOME_REFUSED)

        payload = dict(payload or {})
        spec = _action_spec(self.contract, action)
        base = _base_url(self.contract, action)
        path = str(spec.get("path") or "").strip()
        if not base or not path:
            raise OfficialHttpError("官方契约缺少 base URL 或路径，已拒绝出站请求。", retryable=False, outcome=OUTCOME_REFUSED)
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OfficialHttpError("官方契约的 base URL 无效，已拒绝出站请求。", retryable=False, outcome=OUTCOME_REFUSED)

        request_id = new_request_id()
        context = {
            "request_id": request_id,
            "platform": self.platform,
            "action": action,
            "platform_hotel_id": self.credentials.get("platform_hotel_id") or payload.get("platform_hotel_id") or "",
            "platform_account_id": self.credentials.get("platform_account_id") or payload.get("platform_account_id") or "",
            "date_from": payload.get("date_from") or "",
            "date_to": payload.get("date_to") or "",
            "hotel_id": payload.get("hotel_id") or "",
            "payload": payload,
        }
        template = spec.get("request_template")
        if not isinstance(template, dict):
            template = {
                "request_id": "{{request_id}}",
                "hotel_id": "{{platform_hotel_id}}",
                "date_from": "{{date_from}}",
                "date_to": "{{date_to}}",
            }
        body_obj = _render(template, context)
        method = str(spec.get("method") or "GET").upper()
        timeout = float(spec.get("timeout_seconds") or self.contract.get("timeout_seconds") or DEFAULT_TIMEOUT)
        url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        if method in {"GET", "HEAD"} and isinstance(body_obj, dict):
            query = urlencode(
                {
                    str(key): value
                    for key, value in body_obj.items()
                    if value not in (None, "") and not isinstance(value, (dict, list))
                }
            )
            if query:
                url = url + ("&" if "?" in url else "?") + query
            body_bytes = b""
        else:
            body_bytes = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        headers = _build_headers(self.contract, spec, self.credentials, body_bytes)

        try:
            response = perform_http_request(method, url, headers=headers, content=body_bytes, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise OfficialHttpError("官方接口采集超时，未收到确认。", retryable=True, outcome=OUTCOME_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise OfficialHttpError(f"官方接口网络错误：{exc.__class__.__name__}", retryable=True, outcome=OUTCOME_FAILED) from exc

        status_code = response.status_code
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = {"raw": (response.text or "")[:2000]}

        mapping = spec.get("response_mapping") if isinstance(spec.get("response_mapping"), dict) else {}
        success_http = set(mapping.get("success_http") or list(SUCCESS_HTTP))
        if status_code >= 500:
            raise OfficialHttpError(
                f"官方接口返回 HTTP {status_code}，采集失败，可按瞬时错误重试。",
                retryable=True,
                outcome=OUTCOME_FAILED,
                status_code=status_code,
            )
        if status_code >= 400 or status_code not in success_http:
            message = ""
            if isinstance(body, dict):
                message = str(_dig(body, mapping.get("message_path") or "message") or body.get("error") or "")
            raise OfficialHttpError(
                message or f"官方接口拒绝采集（HTTP {status_code}），不会作为瞬时错误重试。",
                retryable=False,
                outcome=OUTCOME_REFUSED,
                status_code=status_code,
            )

        items = []
        if isinstance(body, dict):
            official_items = _dig(body, mapping.get("items_path") or mapping.get("metrics_path") or "items")
            if isinstance(official_items, list):
                items = [item for item in official_items if isinstance(item, dict)]
        host = parsed.hostname or ""
        message = ""
        if isinstance(body, dict):
            message = str(_dig(body, mapping.get("message_path") or "message") or "")
        return {
            "ok": True,
            "action": action,
            "method": method,
            "host": host,
            "http_status": status_code,
            "items": items,
            "item_count": len(items),
            "message": message or "官方采集接口已返回。",
            "request_id": request_id,
        }

