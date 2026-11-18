"""First-run hotel setup for official packaged desktop installs."""

from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy.orm import Session

from app.auth import create_token, hash_password
from app.models import Hotel, HotelMembership, RoomType, User
from app.services.room_catalog import create_room_type, validate_first_run_room_types
from app.services.channels import save_channel_connection
from app.services.distribution import install_activated, is_packaged_distribution, mark_initial_setup_complete, read_install_state
from app.services.local_integrations import save_ota_contract_path, save_pms_local_config
from app.services.local_secrets import save_model_keys
from app.services.onboarding import (
    DEMO_TENANT_KEYS,
    STATUS_PRODUCT_ENABLED,
    _slug_tenant_key,
    _unique_tenant_key,
    enable_packaged_local_hotel,
)
from app.services.permissions import ROLE_ADMIN
from app.services.risk_policy import default_execution_policy
from app.services.tenancy import ensure_membership, migrate_legacy_settings
from app.seed import seed_settings


def demo_seed_allowed() -> bool:
    raw = (os.environ.get("OIRO_DEMO_SEED") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if is_packaged_distribution():
        return False
    return True


def _clean_username(value: str) -> str:
    name = (value or "").strip()
    if len(name) < 3 or len(name) > 32 or not all(ch.isalnum() or ch in "._-" for ch in name):
        raise ValueError("用户名需为 3–32 位字母、数字或 ._-")
    return name


def _clean_password(value: str) -> str:
    if not value or len(value) < 6:
        raise ValueError("密码至少 6 位")
    return value


def _has_real_hotel(db: Session) -> bool:
    hotel = (
        db.query(Hotel)
        .join(HotelMembership, HotelMembership.hotel_id == Hotel.id)
        .filter(
            HotelMembership.role == ROLE_ADMIN,
            ~Hotel.tenant_key.in_(tuple(DEMO_TENANT_KEYS)),
        )
        .order_by(Hotel.id.asc())
        .first()
    )
    if hotel is None:
        return False
    room_count = db.query(RoomType).filter(RoomType.hotel_id == hotel.id).count()
    return room_count > 0


def initial_setup_complete(db: Session) -> bool:
    if not is_packaged_distribution():
        return True
    if not install_activated():
        return False
    if _has_real_hotel(db):
        state = read_install_state()
        if not state.get("initial_setup_complete"):
            mark_initial_setup_complete()
        return True
    state = read_install_state()
    return bool(state.get("initial_setup_complete")) and _has_real_hotel(db)


def initial_setup_required(db: Session) -> bool:
    if not is_packaged_distribution() or not install_activated():
        return False
    return not initial_setup_complete(db)


def initial_setup_status(db: Session) -> dict[str, Any]:
    required = initial_setup_required(db)
    return {
        "initial_setup_required": required,
        "initial_setup_complete": not required,
    }


def _apply_optional_connections(db: Session, hotel_id: int, body: dict[str, Any]) -> None:
    model = body.get("model_keys")
    if isinstance(model, dict) and model.get("vendors"):
        save_model_keys(model)

    for item in body.get("ota") or []:
        if not isinstance(item, dict):
            continue
        platform = (item.get("platform") or "").strip().lower()
        if platform not in {"ctrip", "meituan", "fliggy"}:
            continue
        account = (item.get("account") or "").strip()
        password = item.get("password")
        if account or password:
            payload: dict[str, Any] = {}
            if account:
                payload["account"] = account
            if password is not None:
                payload["password"] = password
            save_channel_connection(db, hotel_id, platform, payload)
        contract_path = (item.get("contract_file_path") or "").strip()
        if contract_path:
            save_ota_contract_path(
                db,
                hotel_id,
                platform,
                contract_path,
                account_hint=account or None,
            )

    for item in body.get("pms") or []:
        if not isinstance(item, dict):
            continue
        kind = (item.get("kind") or "").strip().lower()
        if kind not in {"pms", "crs"}:
            continue
        endpoint = (item.get("api_endpoint") or "").strip()
        contract_path = (item.get("contract_file_path") or "").strip()
        if endpoint or contract_path:
            save_pms_local_config(
                db,
                hotel_id,
                kind,
                contract_file_path=contract_path or None,
                api_endpoint=endpoint or None,
            )


def complete_initial_setup(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    if not is_packaged_distribution():
        raise ValueError("源码模式无需本机首次开通，请直接登录。")
    if not install_activated():
        raise ValueError("请先完成安装密钥激活。")
    if not initial_setup_required(db):
        raise ValueError("本机已完成酒店开通，请使用管理员账号登录。")
    if demo_seed_allowed():
        raise ValueError("演示种子模式下请使用已有演示账号登录。")

    hotel_name = (body.get("hotel_name") or "").strip()
    if len(hotel_name) < 2:
        raise ValueError("请填写酒店名称（至少 2 个字符）。")
    city = (body.get("city") or "").strip()
    address = (body.get("address") or "").strip()
    positioning = (body.get("positioning") or "").strip()
    platform = (body.get("platform") or "ctrip").strip().lower()
    if platform not in {"ctrip", "meituan", "fliggy"}:
        platform = "ctrip"

    try:
        room_specs = validate_first_run_room_types(body.get("room_types") or [])
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    username = _clean_username(body.get("admin_username") or "")
    password = _clean_password(body.get("admin_password") or "")
    display_name = (body.get("admin_display_name") or "").strip() or "酒店管理员"

    if db.query(User).filter(User.username == username).one_or_none() is not None:
        raise ValueError("该用户名已被占用，请换一个。")

    requested_key = (body.get("tenant_key") or "").strip() or _slug_tenant_key(hotel_name)
    if requested_key in DEMO_TENANT_KEYS:
        requested_key = f"local_{re.sub(r'[^a-z0-9_]', '', requested_key)}"[:48]

    hotel = Hotel(
        name=hotel_name,
        city=city or "未填写",
        address=address,
        positioning=positioning,
        tenant_key=_unique_tenant_key(db, requested_key),
        platform=platform,
    )
    db.add(hotel)
    db.flush()

    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        role=ROLE_ADMIN,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(hotel)

    ensure_membership(db, user, hotel, role=ROLE_ADMIN)
    enable_packaged_local_hotel(db, hotel, actor=user.username)
    migrate_legacy_settings(db, hotel.id)
    seed_settings(db)

    policy = default_execution_policy()
    from app.services.tenancy import policy_setting_key
    from app.models import SystemSetting
    import json

    db.merge(
        SystemSetting(
            key=policy_setting_key(hotel.id),
            value=json.dumps(policy.snapshot(), ensure_ascii=False),
        )
    )
    db.commit()

    for spec in room_specs:
        create_room_type(db, hotel, spec, actor=user.username)

    _apply_optional_connections(db, hotel.id, body)
    mark_initial_setup_complete()

    token = create_token(user, hotel.id)
    return {
        **initial_setup_status(db),
        "access_token": token,
        "token_type": "bearer",
        "display_name": user.display_name,
        "role": ROLE_ADMIN,
        "hotel_id": hotel.id,
        "hotel_name": hotel.name,
        "city": hotel.city or "",
        "product_enabled": True,
        "product_status": "已开通",
        "onboarding_status": STATUS_PRODUCT_ENABLED,
        "onboarding_status_label": "已开通",
        "message": "酒店与本机管理员已创建，可进入工作台。",
    }