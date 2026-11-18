"""Ops workbench APIs: staff, commercial onboarding, e-sign hook, system, audit."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.esign.official_http import (
    public_esign_contract_status,
    save_official_esign_credentials,
    set_esign_enabled,
)
from app.adapters.ota.official_http import ContractValidationError
from app.auth import get_current_user, hash_password
from app.config import get_settings, is_production_env
from app.database import get_db, is_postgres_url
from app.gateway.router import get_gateway
from app.jobs.runtime import ingest_interval_seconds, job_queue_mode, reconcile_interval_seconds, retry_interval_seconds, safe_broker_label
from app.models import AuditLog, Hotel, HotelMembership, User, WriteJob
from app.services.official_esign_contracts import (
    install_official_esign_contract,
    uninstall_official_esign_contract,
    validation_report as esign_validation_report,
)
from app.services.onboarding import (
    approve_onboarding,
    confirm_payment,
    enable_product,
    initiate_onboarding,
    list_onboarding,
    mark_payment_pending,
    public_onboarding,
    start_signing,
    submit_for_approval,
)
from app.services.ops_permissions import (
    OPS_ADMIN,
    OPS_ROLES,
    has_ops_permission,
    is_ops_role,
    normalize_ops_role,
    ops_role_of,
    public_ops_role_matrix,
    public_ops_user,
    require_ops,
    sanitize_ops_connection,
)
from app.services.tenancy import public_hotel
from app.services.workflow import engine_info, write_audit

router = APIRouter(prefix="/api/ops", tags=["ops-workbench"])


class OfficialCredentialsBody(BaseModel):
    key: str | None = Field(default=None)
    secret: str | None = None
    token: str | None = None
    session: str | None = None


class FlagBody(BaseModel):
    enabled: bool


class InitiateBody(BaseModel):
    hotel_id: int | None = None
    name: str | None = None
    city: str = ""
    address: str = ""
    tenant_key: str | None = None
    approval_required: int | None = None
    notes: str = ""


class StaffCreateBody(BaseModel):
    username: str
    password: str
    display_name: str
    role: str


class StaffUpdateBody(BaseModel):
    display_name: str | None = None
    role: str | None = None
    password: str | None = None
    disabled: bool | None = None


def _hotel(db: Session, hotel_id: int) -> Hotel:
    hotel = db.get(Hotel, hotel_id)
    if hotel is None:
        raise HTTPException(status_code=404, detail="酒店不存在")
    return hotel


def _clean_username(value: str) -> str:
    name = (value or "").strip()
    if len(name) < 3 or len(name) > 32 or not all(ch.isalnum() or ch in "._-" for ch in name):
        raise HTTPException(status_code=400, detail="用户名需为 3–32 位字母、数字或 ._-")
    return name


def _clean_password(value: str) -> str:
    if not value or len(value) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    return value


def _clean_ops_role(value: str) -> str:
    role = normalize_ops_role(value)
    if role is None or role not in OPS_ROLES:
        raise HTTPException(status_code=400, detail="岗位必须是管理员、合同、系统运维、运营或客服")
    return role


def _active_admin_count(db: Session, *, exclude_id: int | None = None) -> int:
    q = db.query(User).filter(User.role.in_((OPS_ADMIN, "operator")), User.disabled.is_(False))
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    return q.count()


def _ops_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or not is_ops_role(user.role):
        raise HTTPException(status_code=404, detail="员工不存在")
    return user


@router.get("/roles")
def list_roles(user: User = Depends(require_ops("manage_staff", "view_hotels", any_of=True))):
    return public_ops_role_matrix()


@router.get("/staff")
def list_staff(db: Session = Depends(get_db), user: User = Depends(require_ops("manage_staff"))):
    rows = db.query(User).order_by(User.id.asc()).all()
    staff = [public_ops_user(row) for row in rows if is_ops_role(row.role)]
    return {"staff": staff, "matrix": public_ops_role_matrix()}


@router.post("/staff")
def create_staff(body: StaffCreateBody, db: Session = Depends(get_db), user: User = Depends(require_ops("manage_staff"))):
    username = _clean_username(body.username)
    password = _clean_password(body.password)
    role = _clean_ops_role(body.role)
    display_name = (body.display_name or "").strip() or username
    if db.query(User).filter(User.username == username).one_or_none() is not None:
        raise HTTPException(status_code=400, detail="用户名已存在")
    row = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        role=role,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    db.query(HotelMembership).filter(HotelMembership.user_id == row.id).delete(synchronize_session=False)
    db.commit()
    write_audit(
        db,
        actor=user.username,
        action="ops_staff_create",
        entity_type="ops_user",
        entity_id=str(row.id),
        after={"username": row.username, "role": role},
        reason="管理员创建内部员工账号（不含密码）",
    )
    return public_ops_user(row)


@router.put("/staff/{user_id}")
def update_staff(user_id: int, body: StaffUpdateBody, db: Session = Depends(get_db), user: User = Depends(require_ops("manage_staff"))):
    target = _ops_user(db, user_id)
    current_role = ops_role_of(target)
    new_role = _clean_ops_role(body.role) if body.role is not None else None
    disable = body.disabled is True
    demoting = new_role is not None and new_role != OPS_ADMIN and current_role == OPS_ADMIN
    if (disable or demoting) and current_role == OPS_ADMIN and _active_admin_count(db, exclude_id=target.id) == 0:
        raise HTTPException(status_code=400, detail="不能停用或降级唯一的管理员")
    before = {"role": current_role, "disabled": bool(target.disabled), "display_name": target.display_name}
    if body.display_name is not None:
        name = body.display_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="显示名不能为空")
        target.display_name = name
    if new_role is not None:
        target.role = new_role
    if body.password is not None and body.password != "":
        target.password_hash = hash_password(_clean_password(body.password))
    if body.disabled is not None:
        target.disabled = bool(body.disabled)
        target.disabled_at = datetime.utcnow() if target.disabled else None
    db.commit()
    db.refresh(target)
    db.query(HotelMembership).filter(HotelMembership.user_id == target.id).delete(synchronize_session=False)
    db.commit()
    write_audit(
        db,
        actor=user.username,
        action="ops_staff_update",
        entity_type="ops_user",
        entity_id=str(target.id),
        before=before,
        after={"role": ops_role_of(target), "disabled": bool(target.disabled), "display_name": target.display_name},
        reason="管理员更新内部员工账号或紧急停用（不含密码）",
    )
    return public_ops_user(target)


@router.get("/onboarding")
def get_onboarding_list(db: Session = Depends(get_db), user: User = Depends(require_ops("view_onboarding"))):
    items = list_onboarding(db)
    if not has_ops_permission(user, "view_channel_hosts"):
        for item in items:
            item.pop("last_error", None)
    return {"agreements": items}


@router.get("/hotels/{hotel_id}/onboarding")
def get_onboarding(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("view_onboarding"))):
    hotel = _hotel(db, hotel_id)
    payload = public_onboarding(db, hotel, include_esign=has_ops_permission(user, "view_channel_hosts") or has_ops_permission(user, "install_contracts"))
    if payload.get("esign") and not has_ops_permission(user, "view_channel_hosts"):
        payload["esign"] = sanitize_ops_connection(payload["esign"], user)
    return payload


@router.post("/onboarding/initiate")
def post_initiate(body: InitiateBody, db: Session = Depends(get_db), user: User = Depends(require_ops("initiate_onboarding"))):
    hotel = _hotel(db, body.hotel_id) if body.hotel_id is not None else None
    return initiate_onboarding(
        db,
        actor=user.username,
        hotel=hotel,
        name=body.name,
        city=body.city,
        address=body.address,
        tenant_key=body.tenant_key,
        approval_required=body.approval_required,
        notes=body.notes,
    )


@router.post("/hotels/{hotel_id}/onboarding/start-signing")
def post_start_signing(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("initiate_onboarding"))):
    return start_signing(db, _hotel(db, hotel_id), actor=user.username)


@router.post("/hotels/{hotel_id}/onboarding/submit-approval")
def post_submit_approval(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("initiate_onboarding", "review_onboarding", any_of=True))):
    return submit_for_approval(db, _hotel(db, hotel_id), actor=user.username)


@router.post("/hotels/{hotel_id}/onboarding/approve")
def post_approve(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("review_onboarding"))):
    return approve_onboarding(db, _hotel(db, hotel_id), actor=user.username)


@router.post("/hotels/{hotel_id}/onboarding/mark-payment-pending")
def post_payment_pending(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("review_onboarding"))):
    return mark_payment_pending(db, _hotel(db, hotel_id), actor=user.username)


@router.post("/hotels/{hotel_id}/onboarding/confirm-payment")
def post_confirm_payment(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("confirm_payment"))):
    return confirm_payment(db, _hotel(db, hotel_id), actor=user.username)


@router.post("/hotels/{hotel_id}/onboarding/enable-product")
def post_enable_product(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("enable_product"))):
    return enable_product(db, _hotel(db, hotel_id), actor=user.username)


def _esign_public(db: Session, hotel: Hotel, user: User) -> dict:
    status = public_esign_contract_status(db, hotel_id=hotel.id)
    from app.adapters.esign.official_http import load_official_esign_credentials

    payload = {
        "hotel": public_hotel(hotel),
        **status,
        "has_official_credentials": bool(load_official_esign_credentials(db, hotel.id)),
    }
    return sanitize_ops_connection(payload, user) if not has_ops_permission(user, "view_channel_hosts") else payload


@router.get("/hotels/{hotel_id}/esign")
def get_esign(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("view_onboarding", "install_contracts", any_of=True))):
    return _esign_public(db, _hotel(db, hotel_id), user)


@router.post("/hotels/{hotel_id}/esign/contract/validate")
def validate_esign(hotel_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(require_ops("install_contracts"))):
    _hotel(db, hotel_id)
    report = esign_validation_report(body)
    if not report["valid"]:
        raise HTTPException(status_code=400, detail="；".join(report["errors"] or [report["message"]]))
    return report


@router.put("/hotels/{hotel_id}/esign/contract")
def put_esign_contract(hotel_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(require_ops("install_contracts"))):
    hotel = _hotel(db, hotel_id)
    try:
        result = install_official_esign_contract(db, body, actor=user.username, hotel_id=hotel.id)
    except ContractValidationError as exc:
        raise HTTPException(status_code=400, detail="；".join(exc.errors or [exc.message])) from exc
    public = _esign_public(db, hotel, user)
    public.update({k: result[k] for k in result if k not in {"headers", "contract", "auth_header", "key_header", "request_template", "base_url"}})
    return public


@router.delete("/hotels/{hotel_id}/esign/contract")
def delete_esign_contract(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("install_contracts"))):
    hotel = _hotel(db, hotel_id)
    try:
        result = uninstall_official_esign_contract(db, actor=user.username, hotel_id=hotel.id)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    public = _esign_public(db, hotel, user)
    public.update({k: result[k] for k in result if k not in public})
    return public


@router.put("/hotels/{hotel_id}/esign/official-credentials")
def put_esign_credentials(hotel_id: int, body: OfficialCredentialsBody, db: Session = Depends(get_db), user: User = Depends(require_ops("install_contracts"))):
    hotel = _hotel(db, hotel_id)
    stored = save_official_esign_credentials(db, hotel.id, body.model_dump(exclude_unset=True))
    write_audit(
        db,
        actor=user.username,
        action="ops_esign_credentials",
        entity_type="official_esign_contract",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={"has_official_credentials": bool(stored)},
        reason="运维端保存官方电子合同接口凭证（密钥不写入审计，也不回显）",
    )
    return _esign_public(db, hotel, user)


@router.put("/hotels/{hotel_id}/esign/enable-flag")
def put_esign_flag(hotel_id: int, body: FlagBody, db: Session = Depends(get_db), user: User = Depends(require_ops("install_contracts"))):
    hotel = _hotel(db, hotel_id)
    enabled = set_esign_enabled(db, hotel.id, body.enabled)
    write_audit(
        db,
        actor=user.username,
        action="ops_esign_enable_flag",
        entity_type="official_esign_contract",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={"esign_enabled": enabled},
        reason="运维端更新电子合同出站开关（不会单独发起出站请求）",
    )
    return _esign_public(db, hotel, user)


@router.get("/hotels/{hotel_id}/write-failures")
def list_write_failures(
    hotel_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("view_write_failures")),
    limit: int = Query(default=50, ge=1, le=200),
):
    hotel = _hotel(db, hotel_id)
    failed = {"failed", "timeout", "partial", "refused"}
    rows = (
        db.query(WriteJob)
        .filter(WriteJob.hotel_id == hotel.id, WriteJob.status.in_(failed))
        .order_by(WriteJob.id.desc())
        .limit(limit)
        .all()
    )
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "hotel_id": hotel.id,
                "kind": row.kind,
                "action": row.action,
                "platform": row.platform,
                "status": row.status,
                "write_mode": row.write_mode,
                "attempt_count": row.attempt_count,
                "last_error": row.last_error or "",
                "refuse_reason": row.refuse_reason or "",
            }
        )
    return {"hotel": public_hotel(hotel), "failures": items}


@router.get("/audit")
def list_ops_audit(
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("view_audit")),
    hotel_id: int | None = None,
    limit: int = Query(default=80, ge=1, le=200),
):
    q = db.query(AuditLog).order_by(AuditLog.id.desc())
    if hotel_id is not None:
        q = q.filter(AuditLog.hotel_id == hotel_id)
    rows = q.limit(limit).all()
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "hotel_id": row.hotel_id,
                "actor": row.actor,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "reason": row.reason,
                "before": row.before,
                "after": row.after,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return {"entries": items}


@router.get("/system")
def system_status(db: Session = Depends(get_db), user: User = Depends(require_ops("view_system"))):
    settings = get_settings()
    info = engine_info()
    gw = get_gateway().describe(db, for_ops=True)
    adapters = []
    for item in gw.get("adapters") or []:
        adapters.append(
            {
                "vendor": item.get("vendor"),
                "role": item.get("role"),
                "available": item.get("available"),
                "status": item.get("status"),
                "outbound": item.get("outbound"),
                "model": item.get("model"),
            }
        )
    return {
        "env": (settings.oiro_env or "development").strip() or "development",
        "production": is_production_env(settings),
        "database": "postgresql" if is_postgres_url(settings.database_url) else "sqlite",
        "job_queue": job_queue_mode(),
        "broker": safe_broker_label(),
        "ingest_interval_seconds": ingest_interval_seconds(),
        "retry_interval_seconds": retry_interval_seconds(),
        "reconcile_interval_seconds": reconcile_interval_seconds(),
        "workflow_engine": info.get("engine"),
        "gateway": {
            "overseas_enabled": gw.get("overseas_enabled"),
            "default_chain": gw.get("default_chain"),
            "adapters": adapters,
            "critical_tasks_blocked_from_overseas": gw.get("critical_tasks_blocked_from_overseas"),
            "task_preferences": gw.get("task_preferences"),
            "preference_notes": gw.get("preference_notes") or [],
            "resilience": gw.get("resilience"),
            "circuit_breakers": gw.get("circuit_breakers") or [],
            "rules": gw.get("rules") or [],
        },
        "notes": [
            "系统运维岗不安装商务或官方 API 契约，也不查看酒店密码或官方密钥。",
            "电子签与支付供应商尚未接通；演示默认不打电子合同出站开关。",
        ],
    }


def _hotel_contract_forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail="官方电子合同契约由运维端配置，酒店账号不能校验、安装或卸载。")


hotel_esign_router = APIRouter(prefix="/api/esign", tags=["esign"], dependencies=[Depends(get_current_user)])


@hotel_esign_router.post("/contract/validate")
def hotel_validate(body: dict | None = None, user: User = Depends(get_current_user)):
    raise _hotel_contract_forbidden()


@hotel_esign_router.put("/contract")
def hotel_put(body: dict | None = None, user: User = Depends(get_current_user)):
    raise _hotel_contract_forbidden()


@hotel_esign_router.delete("/contract")
def hotel_delete(user: User = Depends(get_current_user)):
    raise _hotel_contract_forbidden()
