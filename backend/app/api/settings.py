from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.gateway.router import get_gateway, read_overseas_enabled
from app.models import Hotel, SystemSetting, User
from app.schemas import SettingsUpdate
from app.services.channels import list_public_channels
from app.services.pms import list_public_pms_connections
from app.services.permissions import public_matrix_table, public_role_matrix, require_permission
from app.services.risk_policy import load_execution_policy, save_execution_policy, validate_execution_policy
from app.services.onboarding import hotel_public_onboarding
from app.services.tenancy import public_hotel, users_for_hotel
from app.services.workflow import engine_info, write_audit

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(get_current_user)])


def _execution_policy_payload(db: Session, hotel_id: int) -> dict:
    return load_execution_policy(db, hotel_id=hotel_id).to_public_dict()


@router.get("")
def get_cfg(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    gw = get_gateway()
    policy = load_execution_policy(db, hotel_id=hotel.id)
    return {
        "hotel": public_hotel(hotel),
        "gateway": gw.describe(db, for_ops=False),
        "workflow": engine_info(db, hotel_id=hotel.id),
        "overseas_enabled": read_overseas_enabled(db, get_settings()),
        "execution_policy": policy.to_public_dict(),
        "channels": list_public_channels(db, hotel.id),
        "pms_connections": list_public_pms_connections(db, hotel.id),
        "permission_matrix": public_matrix_table(),
        "roles": public_role_matrix(users_for_hotel(db, hotel.id)),
        "critical_rule": "pricing_strategy 与 review_reply 即使开启境外模型也不得出境",
        "human_review_rule": (
            "默认所有调价、活动与评价回复必须人审。"
            "仅当酒店开启自动执行时，通过放行规则的低风险动作可跳过等待并执行；"
            "中高风险与评价回复仍必须人审。"
        ),
        **hotel_public_onboarding(db, hotel),
    }


@router.put("")
def update_cfg(
    body: SettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    hotel: Hotel = Depends(current_hotel),
):
    if body.overseas_enabled is not None or body.execution_policy is not None:
        require_permission(user, "update_policy")
    if body.overseas_enabled is not None:
        row = db.get(SystemSetting, "overseas_enabled")
        value = "true" if body.overseas_enabled else "false"
        if row is None:
            row = SystemSetting(key="overseas_enabled", value=value)
            db.add(row)
        else:
            row.value = value
        db.commit()
        write_audit(
            db,
            actor=user.username,
            action="update_settings",
            entity_type="system_setting",
            entity_id="overseas_enabled",
            after={"overseas_enabled": body.overseas_enabled},
            reason="设置页切换境外模型开关；关键动作仍禁止出境",
        )
    if body.execution_policy is not None:
        before = load_execution_policy(db, hotel_id=hotel.id)
        payload = body.execution_policy.model_dump(exclude_unset=True)
        try:
            policy = validate_execution_policy(payload, before)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        save_execution_policy(db, policy, hotel_id=hotel.id)
        write_audit(
            db,
            actor=user.username,
            action="update_execution_policy",
            entity_type="system_setting",
            entity_id="execution_policy",
            before=before.snapshot(),
            after=policy.snapshot(),
            reason="更新酒店执行策略（自动执行 / 节假日人审 / 调价阈值 / 回滚策略）",
        )
    return get_cfg(db, hotel)
