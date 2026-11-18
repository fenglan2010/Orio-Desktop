"""Internal ops-staff roles and API-enforced permissions.

Hotel tenant roles are unchanged. Product design is not a production ops role.
Finance/billing is documented only and is not a login role in this increment.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.auth import get_current_operator
from app.models import User

OPS_ADMIN = "ops_admin"
OPS_CONTRACT = "ops_contract"
OPS_SYSOPS = "ops_sysops"
OPS_OPERATIONS = "ops_operations"
OPS_SUPPORT = "ops_support"
LEGACY_OPERATOR = "operator"

OPS_ROLES = (OPS_ADMIN, OPS_CONTRACT, OPS_SYSOPS, OPS_OPERATIONS, OPS_SUPPORT)
OPS_ROLE_ALIASES = {LEGACY_OPERATOR: OPS_ADMIN}

OPS_ROLE_LABELS = {
    OPS_ADMIN: "管理员",
    OPS_CONTRACT: "合同",
    OPS_SYSOPS: "系统运维",
    OPS_OPERATIONS: "运营",
    OPS_SUPPORT: "客服",
}

# Capability flags enforced by the API. The frontend only hides sections.
OPS_CAPABILITIES = (
    "view_hotels",
    "view_onboarding",
    "initiate_onboarding",
    "review_onboarding",
    "confirm_payment",
    "enable_product",
    "view_channels",
    "view_channel_hosts",
    "install_contracts",
    "request_write_flag",
    "manage_staff",
    "view_audit",
    "view_system",
    "view_write_failures",
)

OPS_CAPABILITY_LABELS = {
    "view_hotels": "查看酒店名单",
    "view_onboarding": "查看商务开通状态",
    "initiate_onboarding": "发起酒店开通与电子合同",
    "review_onboarding": "审核或逐级审批商务合同",
    "confirm_payment": "确认付款",
    "enable_product": "开通酒店产品权限",
    "view_channels": "查看酒店连接状态",
    "view_channel_hosts": "查看官方契约主机与来源",
    "install_contracts": "安装官方契约或配置官方凭证",
    "request_write_flag": "发起或确认线上写入开关（需双人）",
    "manage_staff": "管理内部员工账号与岗位",
    "view_audit": "查看操作审计",
    "view_system": "查看系统、网关与任务状态",
    "view_write_failures": "查看写入失败",
}

# UI sections on #/ops. Hidden when the role lacks the section capability.
OPS_SECTIONS = (
    ("onboarding", "商务开通", "view_onboarding"),
    ("hotels", "酒店状态", "view_channels"),
    ("ota", "官方 OTA 契约", "install_contracts"),
    ("pms", "官方 PMS/CRS 契约", "install_contracts"),
    ("staff", "员工与岗位", "manage_staff"),
    ("audit", "操作审计", "view_audit"),
    ("system", "系统状态", "view_system"),
)

# 管理员 can view hotel/onboarding/audit/system and manage staff, and may
# participate in write-flag dual approval or post-payment enablement.
# They do not install official contracts or handle official API keys day-to-day,
# and they do not initiate commercial onboarding (运营发起).
_ADMIN_CAPS = frozenset(
    {
        "view_hotels",
        "view_onboarding",
        "confirm_payment",
        "enable_product",
        "view_channels",
        "view_channel_hosts",
        "request_write_flag",
        "manage_staff",
        "view_audit",
        "view_system",
        "view_write_failures",
    }
)

_CONTRACT_CAPS = frozenset(
    {
        "view_hotels",
        "view_onboarding",
        "review_onboarding",
        "confirm_payment",
        "enable_product",
        "view_channels",
        "view_channel_hosts",
        "install_contracts",
        "request_write_flag",
        "view_write_failures",
    }
)

_SYSOPS_CAPS = frozenset({"view_hotels", "view_system"})

_OPERATIONS_CAPS = frozenset(
    {
        "view_hotels",
        "view_onboarding",
        "initiate_onboarding",
        "view_channels",
        "view_write_failures",
    }
)

_SUPPORT_CAPS = frozenset(
    {
        "view_hotels",
        "view_onboarding",
        "view_channels",
        "view_write_failures",
    }
)

OPS_ROLE_CAPS = {
    OPS_ADMIN: _ADMIN_CAPS,
    OPS_CONTRACT: _CONTRACT_CAPS,
    OPS_SYSOPS: _SYSOPS_CAPS,
    OPS_OPERATIONS: _OPERATIONS_CAPS,
    OPS_SUPPORT: _SUPPORT_CAPS,
}


def normalize_ops_role(role: str | None) -> str | None:
    value = (role or "").strip()
    if value in OPS_ROLE_ALIASES:
        return OPS_ROLE_ALIASES[value]
    if value in OPS_ROLES:
        return value
    return None


def is_ops_role(role: str | None) -> bool:
    return normalize_ops_role(role) is not None


def ops_role_of(user: User | None) -> str | None:
    if user is None:
        return None
    return normalize_ops_role(getattr(user, "role", None))


def ops_role_label(role_or_user: str | User | None) -> str:
    if isinstance(role_or_user, User):
        role = ops_role_of(role_or_user)
    else:
        role = normalize_ops_role(role_or_user) or (role_or_user or "")
    return OPS_ROLE_LABELS.get(role or "", role or "未知岗位")


def ops_caps_for(role_or_user: str | User | None) -> set[str]:
    if isinstance(role_or_user, User):
        role = ops_role_of(role_or_user)
    else:
        role = normalize_ops_role(role_or_user)
    return set(OPS_ROLE_CAPS.get(role or "", ()))


def has_ops_permission(user: User | None, capability: str) -> bool:
    if user is None or getattr(user, "disabled", False):
        return False
    return capability in ops_caps_for(user)


def ops_permission_flags(role_or_user: str | User | None) -> dict[str, bool]:
    held = ops_caps_for(role_or_user)
    return {name: name in held for name in OPS_CAPABILITIES}


def ops_sections_for(role_or_user: str | User | None) -> list[dict]:
    held = ops_caps_for(role_or_user)
    return [
        {"id": sid, "label": label, "capability": cap}
        for sid, label, cap in OPS_SECTIONS
        if cap in held
    ]


def public_ops_role_matrix() -> dict:
    return {
        "roles": [{"id": role, "label": ops_role_label(role)} for role in OPS_ROLES],
        "rows": [
            {
                "id": cap,
                "label": OPS_CAPABILITY_LABELS[cap],
                "cells": {role: cap in OPS_ROLE_CAPS[role] for role in OPS_ROLES},
            }
            for cap in OPS_CAPABILITIES
        ],
        "notes": [
            "产品设计不是生产运维后台岗位，不会出现在可登录角色中。",
            "财务计费岗预留，本轮不作为可登录岗位开通。",
            "打开官方读取以外的线上写入，须管理员与合同岗双人确认，任何一方不能单独打开。",
        ],
    }


def public_ops_user(user: User) -> dict:
    role = ops_role_of(user) or (user.role or "")
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": role,
        "role_label": ops_role_label(role),
        "disabled": bool(getattr(user, "disabled", False)),
        "disabled_at": user.disabled_at.isoformat() if getattr(user, "disabled_at", None) else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "permissions": ops_permission_flags(role),
        "sections": ops_sections_for(role),
    }


def ops_me_payload(user: User) -> dict:
    payload = public_ops_user(user)
    payload["scope"] = "ops"
    return payload


def deny_ops(user: User | None, capability: str | None = None) -> HTTPException:
    who = ops_role_label(user) if user is not None else "未登录"
    if capability:
        label = OPS_CAPABILITY_LABELS.get(capability, capability)
        detail = f"当前岗位（{who}）没有「{label}」权限"
    else:
        detail = f"当前岗位（{who}）无权进行该操作"
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def require_ops(*capabilities: str, any_of: bool = False):
    """FastAPI dependency. Hotel tokens never reach here (operator guard runs first)."""

    def _dep(user: User = Depends(get_current_operator)) -> User:
        if not capabilities:
            return user
        held = [has_ops_permission(user, cap) for cap in capabilities]
        ok = any(held) if any_of else all(held)
        if not ok:
            raise deny_ops(user, capabilities[0] if len(capabilities) == 1 else None)
        return user

    return _dep


HOST_FIELDS = (
    "contract_host",
    "contract_hosts",
    "contract_source",
    "contract_removable",
    "has_official_credentials",
    "write_flag_enabled",
    "write_flag_pending",
    "read_flag_enabled",
    "live_ready",
    "base_url",
    "headers",
    "auth_header",
    "key_header",
    "contract",
    "path",
    "adapter",
)


def sanitize_ops_connection(payload: dict, user: User | None) -> dict:
    """Drop hosts, sources and credential flags when the role must not see them."""
    data = dict(payload or {})
    if has_ops_permission(user, "view_channel_hosts"):
        return data
    for key in HOST_FIELDS:
        data.pop(key, None)
    return data
