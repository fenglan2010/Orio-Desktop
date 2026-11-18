"""Hotel PMS/CRS reserved hook: durable status, encrypted credentials, public views, master-data pull."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.ota.official_http import OfficialHttpError, contract_is_configured
from app.adapters.pms.official_http import (
    KINDS,
    KIND_LABELS,
    OfficialPmsHttpClient,
    action_family,
    kind_label,
    kind_read_enabled,
    kind_write_enabled,
    load_official_pms_contract,
    public_action_labels,
    public_pms_contract_status,
    reserved_pull_actions,
    set_kind_flag,
)
from app.models import DailyMetric, PmsConnection, PmsStagingRecord, RoomType
from app.services.channels import (
    decrypt_credentials,
    encrypt_credentials,
    has_usable_credentials,
    sanitize_hotel_error,
)

STATUS_DISCONNECTED = "disconnected"
STATUS_CREDENTIALS_MISSING = "credentials_missing"
STATUS_AUTHORIZED = "authorized"
STATUS_ERROR = "error"

HOTEL_FORBIDDEN_FIELDS = (
    "key", "secret", "token", "session", "password", "account", "username",
    "credentials_encrypted", "write_key", "write_secret", "api_key", "api_secret",
    "official_api_key", "official_api_secret", "headers", "auth_header", "key_header",
    "request_template", "contract", "base_url", "contract_host", "contract_hosts",
    "contract_source", "contract_removable", "write_flag_enabled", "read_flag_enabled",
    "live_ready", "adapter", "adapters", "auth", "contract_path", "source_path",
    "path", "has_server_credentials", "has_official_credentials", "write_flag_source",
    "read_flag_source",
)

HOTEL_CONNECT_HINT = (
    "这是官方 PMS/CRS API 预留接入，不是内置的轻量 PMS，也不包含房态工作台。"
    "酒店只需授权本店 PMS/CRS 账号和密码（如需授权）；官方契约和接口由运维端配置。"
    "未启用正式契约时不会出站，也不会把演示数据改写成虚构的厂商回包。"
)

OPS_CRED_SETTING_PREFIX = "official_pms_creds_"


def _now() -> datetime:
    return datetime.utcnow()


def official_creds_setting_key(kind: str, hotel_id: int) -> str:
    return f"hotel:{int(hotel_id)}:{OPS_CRED_SETTING_PREFIX}{(kind or '').lower()}"


def load_official_api_credentials(db: Session | None, hotel_id: int | None, kind: str) -> dict[str, str]:
    if db is None or hotel_id is None:
        return {}
    from app.models import SystemSetting

    row = db.get(SystemSetting, official_creds_setting_key(kind, hotel_id))
    if row is None or not row.value:
        return {}
    return decrypt_credentials(row.value)


def save_official_api_credentials(db: Session, hotel_id: int, kind: str, payload: dict) -> dict[str, str]:
    from app.models import SystemSetting

    stored = load_official_api_credentials(db, hotel_id, kind)
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
    key = official_creds_setting_key(kind, hotel_id)
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
        row.updated_at = _now()
    db.commit()
    return stored


def _get_or_create(db: Session, hotel_id: int, kind: str) -> PmsConnection:
    kind = kind.lower()
    row = (
        db.query(PmsConnection)
        .filter(PmsConnection.hotel_id == hotel_id, PmsConnection.kind == kind)
        .one_or_none()
    )
    if row is None:
        row = PmsConnection(hotel_id=hotel_id, kind=kind, status=STATUS_DISCONNECTED)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def merged_credentials(row: PmsConnection | None, kind: str, db: Session | None = None, hotel_id: int | None = None) -> dict[str, str]:
    stored = decrypt_credentials(row.credentials_encrypted if row else "")
    official = load_official_api_credentials(db, hotel_id if hotel_id is not None else (row.hotel_id if row else None), kind)
    merged = {k: v for k, v in official.items() if v}
    hotel_overlay = {k: v for k, v in stored.items() if v}
    if not (hotel_overlay.get("key") or hotel_overlay.get("token")):
        hotel_overlay.pop("key", None)
        hotel_overlay.pop("token", None)
    merged.update(hotel_overlay)
    if stored.get("account") and not merged.get("platform_account_id"):
        merged["platform_account_id"] = stored["account"]
    return merged


def strip_hotel_pms_fields(body: dict) -> dict:
    for forbidden in HOTEL_FORBIDDEN_FIELDS:
        body.pop(forbidden, None)
    return body


@dataclass
class PmsPullContext:
    kind: str
    hotel_id: int | None
    status: str
    authorized: bool
    has_credentials: bool
    read_enabled: bool
    write_enabled: bool
    contract: dict | None
    credentials: dict[str, str]
    pull_actions: list[str]
    refuse_reason: str | None = None
    refuse_message: str | None = None

    @property
    def live_ready(self) -> bool:
        return (
            not self.refuse_reason
            and self.authorized
            and self.has_credentials
            and self.read_enabled
            and bool(self.pull_actions)
        )


def resolve_pms_pull_context(db: Session | None, hotel_id: int | None, kind: str) -> PmsPullContext:
    kind = (kind or "pms").lower()
    row = None
    if db is not None and hotel_id is not None:
        row = (
            db.query(PmsConnection)
            .filter(PmsConnection.hotel_id == hotel_id, PmsConnection.kind == kind)
            .one_or_none()
        )
    creds = merged_credentials(row, kind, db, hotel_id)
    contract = load_official_pms_contract(kind, db, hotel_id=hotel_id)
    read_enabled = kind_read_enabled(kind, db, hotel_id)
    write_enabled = kind_write_enabled(kind, db, hotel_id)
    has_creds = has_usable_credentials(creds)
    authorized = bool(row and row.status == STATUS_AUTHORIZED and has_creds)
    if not authorized and has_creds and row is None:
        authorized = True
        status = STATUS_AUTHORIZED
    else:
        status = row.status if row else (STATUS_AUTHORIZED if authorized else STATUS_DISCONNECTED)
    pulls = reserved_pull_actions(contract)

    refuse_reason = None
    refuse_message = None
    if not contract_is_configured(contract) or not pulls:
        refuse_reason = "not_configured"
        refuse_message = "未启用带采集动作的官方 PMS/CRS 契约，跳过拉取，未发起外部请求。"
    elif not has_creds:
        refuse_reason = "missing_credentials"
        refuse_message = "缺少可用凭证，已拒绝出站，未发起外部请求。"
    elif not authorized:
        refuse_reason = "unauthorized"
        refuse_message = "该 PMS/CRS 连接尚未授权，已拒绝出站，未发起外部请求。"
    elif not read_enabled:
        refuse_reason = "read_disabled"
        refuse_message = "运维端尚未打开该 PMS/CRS 读取开关，已跳过拉取，未发起外部请求。"

    return PmsPullContext(
        kind=kind,
        hotel_id=hotel_id,
        status=status,
        authorized=authorized,
        has_credentials=has_creds,
        read_enabled=read_enabled,
        write_enabled=write_enabled,
        contract=contract,
        credentials=creds,
        pull_actions=pulls,
        refuse_reason=refuse_reason,
        refuse_message=refuse_message,
    )


def public_pms_connection(db: Session, hotel_id: int, kind: str) -> dict[str, Any]:
    """Hotel-facing PMS/CRS status. Never includes hosts, sources, secrets, or switch names."""
    kind = kind.lower()
    row = (
        db.query(PmsConnection)
        .filter(PmsConnection.hotel_id == hotel_id, PmsConnection.kind == kind)
        .one_or_none()
    )
    contract = load_official_pms_contract(kind, db, hotel_id=hotel_id)
    stored = decrypt_credentials(row.credentials_encrypted if row else "")
    configured = contract_is_configured(contract)
    actions = list((contract or {}).get("actions") or {}) if configured else []
    labels = public_action_labels(actions)
    status = row.status if row else STATUS_DISCONNECTED
    from app.services.local_integrations import get_pms_api_endpoint, get_pms_contract_path

    body = {
        "kind": kind,
        "label": kind_label(kind, contract) if configured else KIND_LABELS.get(kind, kind),
        "status": status,
        "authorized_at": row.authorized_at.isoformat() if row and row.authorized_at else None,
        "last_verified": row.last_verified.isoformat() if row and row.last_verified else None,
        "has_credentials": has_usable_credentials(stored),
        "contract_enabled": configured,
        "contract_file_path": get_pms_contract_path(db, hotel_id, kind),
        "api_endpoint": get_pms_api_endpoint(db, hotel_id, kind),
        "open_actions": labels,
        "supported_actions": labels,
        "last_error": sanitize_hotel_error((row.last_error if row else "") or ""),
        "connect_hint": HOTEL_CONNECT_HINT,
        "hook_note": "官方 API 预留接入；不包含可单独售卖的轻量 PMS。未启用正式契约时视为未连接。",
    }
    return strip_hotel_pms_fields(body)


def ops_public_pms_connection(db: Session, hotel_id: int, kind: str) -> dict[str, Any]:
    """Operator view: may include contract host/source and flags, never secrets."""
    kind = kind.lower()
    row = (
        db.query(PmsConnection)
        .filter(PmsConnection.hotel_id == hotel_id, PmsConnection.kind == kind)
        .one_or_none()
    )
    contract = load_official_pms_contract(kind, db, hotel_id=hotel_id)
    stored = decrypt_credentials(row.credentials_encrypted if row else "")
    official = load_official_api_credentials(db, hotel_id, kind)
    creds = merged_credentials(row, kind, db, hotel_id)
    read_flag = kind_read_enabled(kind, db, hotel_id)
    write_flag = kind_write_enabled(kind, db, hotel_id)
    configured = contract_is_configured(contract)
    contract_meta = public_pms_contract_status(kind, db, hotel_id=hotel_id)
    action_ids = contract_meta.get("supported_actions") or []
    labels = contract_meta.get("action_labels") or public_action_labels(action_ids)
    status = row.status if row else STATUS_DISCONNECTED
    ctx = resolve_pms_pull_context(db, hotel_id, kind)
    body = {
        "hotel_id": hotel_id,
        "kind": kind,
        "label": contract_meta.get("contract_label") or KIND_LABELS.get(kind, kind),
        "status": status,
        "authorized_at": row.authorized_at.isoformat() if row and row.authorized_at else None,
        "last_verified": row.last_verified.isoformat() if row and row.last_verified else None,
        "has_hotel_credentials": has_usable_credentials(stored),
        "has_official_credentials": has_usable_credentials(official),
        "contract_enabled": configured,
        "contract_configured": configured,
        "contract_source": contract_meta["contract_source"],
        "contract_removable": contract_meta["contract_removable"],
        "contract_host": contract_meta["contract_host"],
        "contract_hosts": contract_meta["contract_hosts"],
        "supported_actions": action_ids,
        "action_labels": labels,
        "open_actions": labels,
        "read_flag_enabled": read_flag,
        "write_flag_enabled": write_flag,
        "live_ready": ctx.live_ready,
        "last_error": sanitize_hotel_error((row.last_error if row else "") or ""),
        "connect_hint": (
            "官方 PMS/CRS 契约、接口地址和读写开关在运维端配置；酒店只需授权本店账号和密码。"
            "安装契约本身不会发起出站请求。示例契约不会自动加载，本仓库不编造生产 URL，也不内置轻量 PMS。"
        ),
    }
    for forbidden in (
        "key", "secret", "token", "session", "password", "account", "username",
        "credentials_encrypted", "write_key", "write_secret", "api_key", "api_secret",
        "contract", "headers", "auth_header", "key_header", "request_template", "base_url",
    ):
        body.pop(forbidden, None)
    return body


def list_public_pms_connections(db: Session, hotel_id: int) -> list[dict[str, Any]]:
    return [public_pms_connection(db, hotel_id, kind) for kind in KINDS]


def list_ops_pms_connections(db: Session, hotel_id: int) -> list[dict[str, Any]]:
    return [ops_public_pms_connection(db, hotel_id, kind) for kind in KINDS]


def save_pms_connection(db: Session, hotel_id: int, kind: str, payload: dict) -> PmsConnection:
    kind = kind.lower()
    if kind not in KINDS:
        raise ValueError("仅支持 PMS 或 CRS 官方接口预留接入")
    row = _get_or_create(db, hotel_id, kind)
    stored = decrypt_credentials(row.credentials_encrypted)
    account = payload.get("account") if "account" in payload else payload.get("username") if "username" in payload else None
    if account is not None:
        value = str(account).strip()
        if value:
            stored["account"] = value
        else:
            stored.pop("account", None)
    if "password" in payload:
        if payload["password"] is not None and str(payload["password"]) != "":
            stored["password"] = str(payload["password"])
        elif payload["password"] is not None and str(payload["password"]) == "":
            stored.pop("password", None)
    # Hotel side must not persist official API keys through this path; ignore key/secret if sent.
    row.credentials_encrypted = encrypt_credentials({k: v for k, v in stored.items() if k in {"account", "password", "username"}})
    if has_usable_credentials(stored):
        row.status = STATUS_AUTHORIZED if row.status == STATUS_ERROR else (row.status if row.status == STATUS_AUTHORIZED else STATUS_AUTHORIZED)
        if row.authorized_at is None:
            row.authorized_at = _now()
        row.last_error = ""
    else:
        row.status = STATUS_CREDENTIALS_MISSING
        row.last_error = "还没有可用的账号和密码。"
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def disconnect_pms_connection(db: Session, hotel_id: int, kind: str) -> PmsConnection:
    row = _get_or_create(db, hotel_id, kind)
    row.credentials_encrypted = ""
    row.status = STATUS_DISCONNECTED
    row.authorized_at = None
    row.last_verified = None
    row.last_error = ""
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def mark_pms_verified(db: Session, hotel_id: int, kind: str, error: str | None = None) -> PmsConnection:
    row = _get_or_create(db, hotel_id, kind)
    row.last_verified = _now()
    row.updated_at = _now()
    if error:
        row.status = STATUS_ERROR
        row.last_error = error
    else:
        row.status = STATUS_AUTHORIZED
        if row.authorized_at is None:
            row.authorized_at = _now()
        row.last_error = ""
    db.commit()
    db.refresh(row)
    return row


def verify_pms_connection(db: Session, hotel_id: int, kind: str) -> dict[str, Any]:
    """Check authorization. Opens a socket only when a contract verify/read path and switches allow it."""
    kind = kind.lower()
    row = _get_or_create(db, hotel_id, kind)
    ctx = resolve_pms_pull_context(db, hotel_id, kind)
    stored = decrypt_credentials(row.credentials_encrypted)
    hotel_has = has_usable_credentials(stored)
    if not hotel_has and not ctx.has_credentials:
        row.status = STATUS_CREDENTIALS_MISSING
        row.last_error = "还没有可用的账号和密码，无法校验。"
        row.updated_at = _now()
        db.commit()
        return public_pms_connection(db, hotel_id, kind)

    contract = ctx.contract
    verify_action = None
    if contract_is_configured(contract):
        actions = contract.get("actions") or {}
        if "verify" in actions and contract_is_configured(contract, "verify"):
            verify_action = "verify"
    if not contract_is_configured(contract) or not verify_action:
        if hotel_has or ctx.has_credentials:
            row.status = STATUS_AUTHORIZED
            if row.authorized_at is None:
                row.authorized_at = _now()
            row.last_error = (
                ""
                if contract_is_configured(contract)
                else "账号已保存，但运维端尚未启用官方 PMS/CRS 契约，无法向官方接口做连通性校验。本次未发起外部请求。"
            )
        row.last_verified = _now()
        row.updated_at = _now()
        db.commit()
        return public_pms_connection(db, hotel_id, kind)

    if not ctx.read_enabled and not ctx.write_enabled:
        row.status = STATUS_AUTHORIZED
        if row.authorized_at is None:
            row.authorized_at = _now()
        row.last_verified = _now()
        row.last_error = "官方契约已启用，但运维端尚未打开读取或写入开关，本次未发起外部请求。"
        row.updated_at = _now()
        db.commit()
        return public_pms_connection(db, hotel_id, kind)

    client = OfficialPmsHttpClient(kind, contract, ctx.credentials)
    try:
        client.send_read(verify_action, {"hotel_id": hotel_id})
        mark_pms_verified(db, hotel_id, kind)
    except OfficialHttpError as exc:
        mark_pms_verified(db, hotel_id, kind, error=exc.message)
    return public_pms_connection(db, hotel_id, kind)


def set_pms_read_flag(db: Session, hotel_id: int, kind: str, enabled: bool) -> bool:
    return set_kind_flag(db, hotel_id, kind, enabled, read=True)


def set_pms_write_flag(db: Session, hotel_id: int, kind: str, enabled: bool) -> bool:
    return set_kind_flag(db, hotel_id, kind, enabled, read=False)


def _window() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=14), today + timedelta(days=14)


def _parse_item_date(item: dict) -> date | None:
    for key in ("date", "stay_date", "occupancy_date", "business_date", "arrival_date"):
        raw = item.get(key)
        if not raw:
            continue
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
    return None


def _item_room_code(item: dict) -> str:
    for key in ("room_type_code", "room_code", "code", "room_type", "room"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _item_int(item: dict, *keys: str) -> int | None:
    for key in keys:
        if key not in item or item.get(key) in (None, ""):
            continue
        try:
            return int(item.get(key))
        except (TypeError, ValueError):
            continue
    return None


def _item_float(item: dict, *keys: str) -> float | None:
    for key in keys:
        if key not in item or item.get(key) in (None, ""):
            continue
        try:
            return float(item.get(key))
        except (TypeError, ValueError):
            continue
    return None


def map_pull_items(db: Session, hotel_id: int, kind: str, family: str, items: list[dict]) -> dict[str, Any]:
    """Map official items onto existing room types / DailyMetric.

    Writes DailyMetric rows with platform=pms|crs so demo OTA (ctrip etc.) rows
    are not overwritten. Never invents room types or vendor payloads.
    """
    rooms = {row.code: row for row in db.query(RoomType).filter(RoomType.hotel_id == hotel_id).all()}
    mapped = 0
    skipped = 0
    notes: list[str] = []
    if family == "room_types":
        matched = []
        for item in items:
            if not isinstance(item, dict):
                skipped += 1
                continue
            code = _item_room_code(item)
            if code and code in rooms:
                matched.append(code)
                mapped += 1
            else:
                skipped += 1
        notes.append("房型仅对照已有房型编码，不自动新建房型，也不改写房型主数据。")
        return {"mapped": mapped, "skipped": skipped, "matched_room_codes": matched, "notes": notes}

    if family not in {FAMILY_INVENTORY := "inventory", "reservations"}:
        return {"mapped": 0, "skipped": len(items), "notes": ["该动作只写入暂存，不映射到经营指标。"]}

    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        day = _parse_item_date(item)
        code = _item_room_code(item)
        room = rooms.get(code) if code else None
        if day is None or room is None:
            skipped += 1
            continue
        available = _item_int(item, "available_rooms", "availability", "available", "inventory")
        sold = _item_int(item, "sold_rooms", "sold", "reserved", "bookings")
        occupancy = _item_float(item, "occupancy", "occupancy_rate")
        if available is None and sold is None and occupancy is None:
            skipped += 1
            continue
        inventory = room.inventory or 0
        if occupancy is None and available is not None and inventory:
            occupancy = max(0.0, min(1.0, 1.0 - (available / inventory)))
        elif occupancy is None and sold is not None and inventory:
            occupancy = max(0.0, min(1.0, sold / inventory))
        row = (
            db.query(DailyMetric)
            .filter(
                DailyMetric.hotel_id == hotel_id,
                DailyMetric.room_type_id == room.id,
                DailyMetric.date == day,
                DailyMetric.platform == kind,
            )
            .one_or_none()
        )
        if row is None:
            row = DailyMetric(
                hotel_id=hotel_id,
                room_type_id=room.id,
                date=day,
                platform=kind,
                is_future=day > date.today(),
            )
            db.add(row)
        if available is not None:
            row.available_rooms = available
        if sold is not None:
            row.sold_rooms = sold
            row.bookings = sold
        if occupancy is not None:
            row.occupancy = occupancy
        mapped += 1
    if mapped:
        db.commit()
    notes.append("指标写入 platform=pms/crs 的独立行，不会覆盖演示用的 OTA 指标。")
    return {"mapped": mapped, "skipped": skipped, "notes": notes}


def _safe_items(items: list) -> list[dict]:
    cleaned: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        row = {}
        for key, value in list(item.items())[:24]:
            if key.lower() in {"password", "secret", "token", "key", "authorization"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                row[str(key)] = value if not isinstance(value, str) else value[:200]
            elif isinstance(value, (int, float)):
                row[str(key)] = value
        cleaned.append(row)
        if len(cleaned) >= 200:
            break
    return cleaned


def pull_kind(db: Session, hotel_id: int, kind: str) -> dict[str, Any]:
    """Attempt reserved official pulls for one kind. Never invents a URL."""
    ctx = resolve_pms_pull_context(db, hotel_id, kind)
    if not ctx.live_ready:
        status = "not_configured" if ctx.refuse_reason in {None, "not_configured"} else "skipped"
        if ctx.refuse_reason == "not_configured" or not contract_is_configured(ctx.contract):
            status = "not_configured"
        return {
            "kind": kind,
            "status": status,
            "reason": ctx.refuse_reason or "not_configured",
            "message": ctx.refuse_message or "未配置官方 PMS/CRS 契约，已跳过。",
            "official_http": False,
            "calls": [],
            "persisted": False,
        }

    start, end = _window()
    client = OfficialPmsHttpClient(kind, ctx.contract, ctx.credentials)
    calls: list[dict[str, Any]] = []
    failed = False
    error = ""
    staged = 0
    mapped_total = 0
    for action in ctx.pull_actions:
        family = action_family(action) or ""
        payload = {
            "hotel_id": hotel_id,
            "platform_hotel_id": (ctx.credentials or {}).get("platform_hotel_id") or "",
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }
        try:
            receipt = client.send_read(action, payload)
        except OfficialHttpError as exc:
            failed = True
            error = exc.message
            calls.append(
                {
                    "action": action,
                    "family": family,
                    "ok": False,
                    "retryable": bool(exc.retryable),
                    "http_status": exc.status_code,
                    "message": exc.message,
                }
            )
            db.add(
                PmsStagingRecord(
                    hotel_id=hotel_id,
                    kind=kind,
                    action=action,
                    family=family,
                    status="failed",
                    item_count=0,
                    mapped_count=0,
                    payload={"error": exc.message, "http_status": exc.status_code},
                    mapping_result=None,
                )
            )
            db.commit()
            break
        items = _safe_items(receipt.get("items") or [])
        mapping = map_pull_items(db, hotel_id, kind, family, items)
        record_status = "mapped" if mapping.get("mapped") else "staged"
        db.add(
            PmsStagingRecord(
                hotel_id=hotel_id,
                kind=kind,
                action=action,
                family=family,
                status=record_status,
                item_count=len(items),
                mapped_count=int(mapping.get("mapped") or 0),
                payload={"items": items, "host": receipt.get("host") or "", "http_status": receipt.get("http_status")},
                mapping_result=mapping,
            )
        )
        db.commit()
        staged += 1
        mapped_total += int(mapping.get("mapped") or 0)
        calls.append(
            {
                "action": action,
                "family": family,
                "ok": True,
                "http_status": receipt.get("http_status"),
                "item_count": len(items),
                "mapped": mapping.get("mapped") or 0,
                "persisted": record_status,
                "message": receipt.get("message") or "",
            }
        )
    return {
        "kind": kind,
        "status": "failed" if failed else "succeeded",
        "reason": "official_error" if failed else "",
        "message": error or "已按官方契约拉取预留主数据动作。",
        "official_http": True,
        "calls": calls,
        "persisted": staged > 0,
        "staged": staged,
        "mapped": mapped_total,
        "error": error,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
    }


def run_pms_ingest(db: Session, hotel_id: int) -> dict[str, Any]:
    """Reserved PMS/CRS pull on the ingest cadence. Skip quietly when not live-ready."""
    kinds = []
    for kind in KINDS:
        kinds.append(pull_kind(db, hotel_id, kind))
    statuses = {row["status"] for row in kinds}
    official = any(row.get("official_http") for row in kinds)
    if statuses <= {"not_configured"}:
        status = "not_configured"
        message = "未启用官方 PMS/CRS 契约，已跳过拉取。"
    elif statuses <= {"not_configured", "skipped"}:
        status = "skipped"
        message = "官方 PMS/CRS 契约未达到可拉取条件，已跳过，未发起外部请求。"
    elif "failed" in statuses and "succeeded" in statuses:
        status = "partial"
        message = "部分官方 PMS/CRS 拉取失败。"
    elif "failed" in statuses:
        status = "failed"
        message = next((row.get("message") for row in kinds if row["status"] == "failed"), "官方 PMS/CRS 拉取失败。")
    else:
        status = "succeeded"
        message = "官方 PMS/CRS 预留拉取已完成。"
    return {
        "status": status,
        "official_http": official,
        "message": message,
        "kinds": kinds,
    }
