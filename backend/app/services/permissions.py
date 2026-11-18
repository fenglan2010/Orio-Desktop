"""Fixed hotel-side role matrix. Backend is the source of truth."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.auth import get_current_user
from app.models import User

ROLE_ADMIN = "admin"
ROLE_REVENUE_MANAGER = "revenue_manager"
ROLE_APPROVER = "approver"
ROLE_ANALYST = "analyst"
ROLE_OPERATOR = "operator"

ROLES = (ROLE_ADMIN, ROLE_REVENUE_MANAGER, ROLE_APPROVER, ROLE_ANALYST)

ROLE_LABELS = {
    ROLE_ADMIN: "酒店管理员",
    ROLE_REVENUE_MANAGER: "收益经理",
    ROLE_APPROVER: "调价审批人",
    ROLE_ANALYST: "运营观察",
    ROLE_OPERATOR: "运维管理员",
}

# Capability flags used by APIs and the frontend.
CAPABILITIES = (
    "view_l1",
    "view_l2",
    "view_l3",
    "view_l4",
    "view_l5",
    "view_chat",
    "view_audit",
    "view_writes",
    "view_channels",
    "generate",
    "approve",
    "execute",
    "rollback",
    "retry_writes",
    "reconcile_writes",
    "update_policy",
    "update_constraints",
    "manage_channels",
    "manage_users",
    "diagnose",
)

CAPABILITY_LABELS = {
    "view_l1": "查看 L1 实时监控",
    "view_l2": "查看 L2 多维分析",
    "view_l3": "查看 L3 执行中心",
    "view_l4": "查看 L4 自治管理",
    "view_l5": "查看 L5 系统进化预览",
    "view_chat": "使用决策对话",
    "view_audit": "查看审计日志",
    "view_writes": "查看写入监控",
    "view_channels": "查看渠道连接状态",
    "generate": "生成策略或回复草稿",
    "approve": "审批通过或拒绝",
    "execute": "执行写入",
    "rollback": "回滚已执行方案",
    "retry_writes": "重试写入",
    "reconcile_writes": "对账写入",
    "update_policy": "修改执行策略",
    "update_constraints": "修改价格约束",
    "manage_channels": "连接或断开渠道",
    "manage_users": "管理酒店账号",
    "diagnose": "诊断预警",
}

# Rows shown on the Settings matrix (stable order, hotel-facing wording).
MATRIX_ROWS = (
    ("view_l1", "查看 L1 实时监控"),
    ("view_l2", "查看 L2 多维分析"),
    ("view_l3", "查看 L3 执行中心"),
    ("view_l4", "查看 L4 自治管理"),
    ("view_l5", "查看 L5 预览"),
    ("generate", "生成策略 / 回复草稿（进入人审队列）"),
    ("approve", "审批通过 / 拒绝"),
    ("execute", "执行写入"),
    ("rollback", "回滚"),
    ("view_writes", "查看写入监控"),
    ("retry_writes", "重试 / 对账写入"),
    ("update_policy", "修改执行策略（自动执行、写入模式等）"),
    ("update_constraints", "修改价格约束"),
    ("view_channels", "查看渠道连接"),
    ("manage_channels", "保存或断开渠道凭证"),
    ("manage_users", "管理账号"),
    ("view_audit", "查看审计日志"),
    ("view_chat", "决策对话与预警诊断"),
)

_ALL = frozenset(CAPABILITIES)

_MANAGER = frozenset(CAPABILITIES) - {
    "update_policy",
    "manage_channels",
    "manage_users",
}

_APPROVER = frozenset(
    {
        "view_l1",
        "view_l2",
        "view_l3",
        "view_chat",
        "view_audit",
        "view_writes",
        "generate",
        "approve",
        "diagnose",
    }
)

_ANALYST = frozenset(
    {
        "view_l1",
        "view_l2",
        "view_chat",
        "diagnose",
    }
)

ROLE_CAPS = {
    ROLE_ADMIN: _ALL,
    ROLE_REVENUE_MANAGER: _MANAGER,
    ROLE_APPROVER: _APPROVER,
    ROLE_ANALYST: _ANALYST,
}

PAGE_CAPS = {
    "L1": "view_l1",
    "L2": "view_l2",
    "L3": "view_l3",
    "L4": "view_l4",
    "L5": "view_l5",
}


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get((role or "").strip(), role or "未知角色")


def caps_for(role: str | None) -> set[str]:
    return set(ROLE_CAPS.get((role or "").strip(), ()))


def has_permission(user: User | None, capability: str) -> bool:
    if user is None or getattr(user, "disabled", False):
        return False
    return capability in caps_for(user.role)


def permission_flags(role: str | None) -> dict[str, bool]:
    held = caps_for(role)
    return {name: name in held for name in CAPABILITIES}


def pages_for(role: str | None) -> list[str]:
    held = caps_for(role)
    return [page for page, cap in PAGE_CAPS.items() if cap in held]


def deny(user: User | None, capability: str) -> HTTPException:
    label = CAPABILITY_LABELS.get(capability, capability)
    who = role_label(user.role) if user is not None else "未登录"
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"当前角色（{who}）没有「{label}」权限",
    )


def require_permission(user: User, capability: str) -> User:
    if not has_permission(user, capability):
        raise deny(user, capability)
    return user


def require(capability: str):
    def _dep(user: User = Depends(get_current_user)) -> User:
        return require_permission(user, capability)

    return _dep


def public_role_matrix(users: list[User] | None = None) -> list[dict]:
    grouped: dict[str, list[dict]] = {role: [] for role in ROLES}
    for user in users or []:
        if user.role in grouped:
            grouped[user.role].append(
                {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "disabled": bool(getattr(user, "disabled", False)),
                }
            )
    rows = []
    for role in ROLES:
        flags = permission_flags(role)
        rows.append(
            {
                "id": role,
                "label": role_label(role),
                "pages": pages_for(role),
                "permissions": flags,
                "users": grouped.get(role, []),
            }
        )
    return rows


def public_matrix_table() -> dict:
    return {
        "roles": [{"id": role, "label": role_label(role)} for role in ROLES],
        "rows": [
            {
                "id": cap,
                "label": label,
                "cells": {role: cap in ROLE_CAPS[role] for role in ROLES},
            }
            for cap, label in MATRIX_ROWS
        ],
    }


def public_user(user: User, *, role: str | None = None) -> dict:
    role = role or user.role
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": role,
        "role_label": role_label(role),
        "disabled": bool(getattr(user, "disabled", False)),
        "disabled_at": user.disabled_at.isoformat() if getattr(user, "disabled_at", None) else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "pages": pages_for(role),
        "permissions": permission_flags(role),
    }


def me_payload(user: User, hotel, *, role: str | None = None) -> dict:
    role = role or user.role
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": role,
        "role_label": role_label(role),
        "disabled": bool(getattr(user, "disabled", False)),
        "hotel_id": hotel.id,
        "hotel_name": hotel.name,
        "city": hotel.city,
        "address": getattr(hotel, "address", "") or "",
        "positioning": getattr(hotel, "positioning", "") or "",
        "platform": hotel.platform,
        "tenant_key": getattr(hotel, "tenant_key", "") or "",
        "pages": pages_for(role),
        "permissions": permission_flags(role),
    }
