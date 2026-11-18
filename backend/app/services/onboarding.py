"""Commercial hotel onboarding and product-enablement gate.

Agreed flow (not mixed with official OTA/PMS install):
Operations initiates and signs an electronic contract with the hotel
→ contract desk confirms and the contract goes through hierarchical approval
→ both parties' contract takes effect
→ payment is confirmed
→ only then does the hotel's product capability / permission become effective.

Official OTA/PMS contract install and live write flags remain a later, separate
ops step after commercial onboarding. E-sign and payment providers are not
connected yet: outbound e-sign HTTP is refused unless a non-example official
contract is enabled with credentials and an explicit switch.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.adapters.esign.official_http import (
    OfficialHttpError,
    esign_enabled,
    load_official_esign_credentials,
    outbound_ready,
    public_esign_contract_status,
    send_esign_action,
)
from app.models import Hotel, HotelOnboardingAgreement
from app.services.tenancy import public_hotel
from app.services.workflow import write_audit

DEMO_TENANT_KEYS = frozenset({"demo", "demo_chengdu"})

STATUS_INITIATED = "initiated"
STATUS_SIGNING = "signing"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_CONTRACT_EFFECTIVE = "contract_effective"
STATUS_PAYMENT_PENDING = "payment_pending"
STATUS_PAYMENT_CONFIRMED = "payment_confirmed"
STATUS_PRODUCT_ENABLED = "product_enabled"

ONBOARDING_STATUSES = (
    STATUS_INITIATED,
    STATUS_SIGNING,
    STATUS_PENDING_APPROVAL,
    STATUS_CONTRACT_EFFECTIVE,
    STATUS_PAYMENT_PENDING,
    STATUS_PAYMENT_CONFIRMED,
    STATUS_PRODUCT_ENABLED,
)

STATUS_LABELS = {
    STATUS_INITIATED: "已发起",
    STATUS_SIGNING: "签署中",
    STATUS_PENDING_APPROVAL: "待逐级审批",
    STATUS_CONTRACT_EFFECTIVE: "合同已生效",
    STATUS_PAYMENT_PENDING: "待确认付款",
    STATUS_PAYMENT_CONFIRMED: "付款已确认",
    STATUS_PRODUCT_ENABLED: "已开通",
}

VENDOR_NOT_CONFIGURED = "not_configured"
VENDOR_WAITING = "waiting_vendor"
VENDOR_READY = "ready"
VENDOR_DISABLED = "disabled"

DEFAULT_APPROVAL_REQUIRED = 2

PRODUCT_DISABLED_DETAIL = "酒店尚未开通，待电子合同生效并确认付款后再使用。"


def _now() -> datetime:
    return datetime.utcnow()


def is_demo_hotel(hotel: Hotel | None) -> bool:
    if hotel is None:
        return False
    return (hotel.tenant_key or "") in DEMO_TENANT_KEYS


def get_agreement(db: Session, hotel_id: int) -> HotelOnboardingAgreement | None:
    return (
        db.query(HotelOnboardingAgreement)
        .filter(HotelOnboardingAgreement.hotel_id == int(hotel_id))
        .one_or_none()
    )


def is_product_enabled(db: Session, hotel: Hotel | int) -> bool:
    if isinstance(hotel, int):
        row = db.get(Hotel, hotel)
        if row is None:
            return False
        hotel = row
    agreement = get_agreement(db, hotel.id)
    if agreement is None:
        return is_demo_hotel(hotel)
    return agreement.status == STATUS_PRODUCT_ENABLED


def ensure_product_enabled(db: Session, hotel: Hotel) -> None:
    if is_product_enabled(db, hotel):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PRODUCT_DISABLED_DETAIL)


def public_entitlement(db: Session, hotel: Hotel) -> dict[str, Any]:
    enabled = is_product_enabled(db, hotel)
    agreement = get_agreement(db, hotel.id)
    state = agreement.status if agreement is not None else (STATUS_PRODUCT_ENABLED if enabled else STATUS_INITIATED)
    return {
        "product_enabled": enabled,
        "product_status": "已开通" if enabled else "尚未开通",
        "onboarding_status": state,
        "onboarding_status_label": STATUS_LABELS.get(state, state),
    }


def _vendor_state(db: Session, hotel_id: int) -> str:
    ready, reason = outbound_ready(db, hotel_id)
    if ready:
        return VENDOR_READY
    if reason == "esign_disabled":
        return VENDOR_DISABLED
    if reason == "missing_credentials":
        return VENDOR_WAITING
    return VENDOR_NOT_CONFIGURED


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def public_onboarding(db: Session, hotel: Hotel, *, include_esign: bool = False) -> dict[str, Any]:
    agreement = get_agreement(db, hotel.id)
    entitlement = public_entitlement(db, hotel)
    vendor = agreement.vendor_state if agreement is not None else _vendor_state(db, hotel.id)
    payload = {
        "hotel": public_hotel(hotel),
        **entitlement,
        "vendor_state": vendor,
        "approval_level": agreement.approval_level if agreement is not None else 0,
        "approval_required": agreement.approval_required if agreement is not None else DEFAULT_APPROVAL_REQUIRED,
        "initiated_by": agreement.initiated_by if agreement is not None else "",
        "initiated_at": _iso(agreement.initiated_at) if agreement is not None else None,
        "signing_started_at": _iso(agreement.signing_started_at) if agreement is not None else None,
        "submitted_for_approval_at": _iso(agreement.submitted_for_approval_at) if agreement is not None else None,
        "last_approved_by": agreement.last_approved_by if agreement is not None else "",
        "last_approved_at": _iso(agreement.last_approved_at) if agreement is not None else None,
        "contract_effective_at": _iso(agreement.contract_effective_at) if agreement is not None else None,
        "payment_confirmed_by": agreement.payment_confirmed_by if agreement is not None else "",
        "payment_confirmed_at": _iso(agreement.payment_confirmed_at) if agreement is not None else None,
        "product_enabled_by": agreement.product_enabled_by if agreement is not None else "",
        "product_enabled_at": _iso(agreement.product_enabled_at) if agreement is not None else None,
        "last_error": agreement.last_error if agreement is not None else "",
        "notes": agreement.notes if agreement is not None else "",
    }
    if include_esign:
        status = public_esign_contract_status(db, hotel_id=hotel.id)
        creds = load_official_esign_credentials(db, hotel.id)
        payload["esign"] = {
            **status,
            "has_official_credentials": bool(creds),
            "esign_enabled": esign_enabled(db, hotel_id=hotel.id),
        }
    return payload


def hotel_public_onboarding(db: Session, hotel: Hotel) -> dict[str, Any]:
    """Hotel-facing: 已开通 / 尚未开通 only. No contract JSON or vendor hosts."""
    return public_entitlement(db, hotel)


def ensure_demo_product_enabled(db: Session, hotel: Hotel, *, actor: str = "system:seed") -> HotelOnboardingAgreement:
    """Seed or repair a demo hotel so it stays usable."""
    now = _now()
    row = get_agreement(db, hotel.id)
    if row is None:
        row = HotelOnboardingAgreement(
            hotel_id=hotel.id,
            status=STATUS_PRODUCT_ENABLED,
            vendor_state=VENDOR_NOT_CONFIGURED,
            approval_level=DEFAULT_APPROVAL_REQUIRED,
            approval_required=DEFAULT_APPROVAL_REQUIRED,
            initiated_by=actor,
            initiated_at=now,
            contract_effective_at=now,
            payment_confirmed_by=actor,
            payment_confirmed_at=now,
            product_enabled_by=actor,
            product_enabled_at=now,
            notes="演示酒店默认已开通，无需走商务开通流程。",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    if row.status != STATUS_PRODUCT_ENABLED:
        row.status = STATUS_PRODUCT_ENABLED
        row.product_enabled_by = row.product_enabled_by or actor
        row.product_enabled_at = row.product_enabled_at or now
        row.payment_confirmed_at = row.payment_confirmed_at or now
        row.contract_effective_at = row.contract_effective_at or now
        db.commit()
        db.refresh(row)
    return row


def _slug_tenant_key(name: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return (raw or "hotel")[:48]


def _unique_tenant_key(db: Session, requested: str) -> str:
    base = (requested or "hotel")[:48]
    key = base
    suffix = 2
    while db.query(Hotel).filter(Hotel.tenant_key == key).one_or_none() is not None:
        key = f"{base}_{suffix}"[:64]
        suffix += 1
    return key


def _require_status(row: HotelOnboardingAgreement, allowed: set[str], message: str) -> None:
    if row.status not in allowed:
        raise HTTPException(status_code=400, detail=message)


def _get_or_raise(db: Session, hotel: Hotel) -> HotelOnboardingAgreement:
    row = get_agreement(db, hotel.id)
    if row is None:
        raise HTTPException(status_code=400, detail="该酒店尚未发起开通，请先由运营发起")
    return row


def initiate_onboarding(
    db: Session,
    *,
    actor: str,
    hotel: Hotel | None = None,
    name: str | None = None,
    city: str = "",
    address: str = "",
    tenant_key: str | None = None,
    approval_required: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    if hotel is None:
        title = (name or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="请提供酒店名称以发起开通")
        requested = (tenant_key or "").strip() or _slug_tenant_key(title)
        if requested in DEMO_TENANT_KEYS:
            raise HTTPException(status_code=400, detail="不能把新开通酒店写成演示租户")
        hotel = Hotel(
            name=title,
            city=(city or "").strip() or "未填写",
            address=(address or "").strip(),
            positioning="",
            tenant_key=_unique_tenant_key(db, requested),
            platform="ctrip",
        )
        db.add(hotel)
        db.flush()
    elif is_demo_hotel(hotel) and is_product_enabled(db, hotel):
        raise HTTPException(status_code=400, detail="演示酒店已开通，不能重新走商务开通来关闭权限")

    required = DEFAULT_APPROVAL_REQUIRED
    if approval_required is not None:
        try:
            required = int(approval_required)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="审批级数必须是正整数") from exc
        if required < 1 or required > 9:
            raise HTTPException(status_code=400, detail="审批级数须在 1–9 之间")

    row = get_agreement(db, hotel.id)
    now = _now()
    if row is None:
        row = HotelOnboardingAgreement(
            hotel_id=hotel.id,
            status=STATUS_INITIATED,
            vendor_state=_vendor_state(db, hotel.id),
            approval_level=0,
            approval_required=required,
            initiated_by=actor,
            initiated_at=now,
            notes=(notes or "").strip(),
        )
        db.add(row)
    else:
        if row.status == STATUS_PRODUCT_ENABLED:
            raise HTTPException(status_code=400, detail="该酒店已开通，不能重复发起")
        if row.status != STATUS_INITIATED:
            raise HTTPException(status_code=400, detail=f"开通已在进行中（{STATUS_LABELS.get(row.status, row.status)}），不能重新发起")
        row.initiated_by = actor
        row.initiated_at = row.initiated_at or now
        row.approval_required = required
        if notes:
            row.notes = notes.strip()
        row.vendor_state = _vendor_state(db, hotel.id)
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=actor,
        action="onboarding_initiate",
        entity_type="hotel_onboarding",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={"status": row.status, "vendor_state": row.vendor_state},
        reason="运营发起酒店商务开通（电子合同与付款确认之后才会开通产品权限）",
    )
    return public_onboarding(db, hotel, include_esign=True)


def start_signing(db: Session, hotel: Hotel, *, actor: str) -> dict[str, Any]:
    row = _get_or_raise(db, hotel)
    _require_status(row, {STATUS_INITIATED, STATUS_SIGNING}, "当前状态不能发起签署")
    ready, reason = outbound_ready(db, hotel.id)
    if not ready:
        row.vendor_state = VENDOR_WAITING if reason != "esign_disabled" else VENDOR_DISABLED
        if reason == "missing_contract":
            row.vendor_state = VENDOR_WAITING
            row.last_error = "电子合同供应商尚未配置正式契约，已拒绝出站，开通单保持等待供应商。"
        elif reason == "esign_disabled":
            row.last_error = "电子合同出站开关未打开，已拒绝出站。"
        else:
            row.last_error = "电子合同官方凭证未配置，已拒绝出站。"
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=row.last_error + " 部署正式电子合同供应商并安装非示例契约、配置凭证并打开开关后即可启用。",
        )
    try:
        send_esign_action(db, hotel.id, payload={"title": hotel.name, "hotel_id": hotel.id})
    except OfficialHttpError as exc:
        row.vendor_state = VENDOR_WAITING
        row.last_error = str(exc)
        db.commit()
        raise HTTPException(status_code=400, detail=f"电子合同出站失败：{exc}") from exc
    now = _now()
    row.status = STATUS_SIGNING
    row.vendor_state = VENDOR_READY
    row.signing_started_at = row.signing_started_at or now
    row.last_error = ""
    db.commit()
    write_audit(
        db,
        actor=actor,
        action="onboarding_start_signing",
        entity_type="hotel_onboarding",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={"status": row.status, "vendor_state": row.vendor_state},
        reason="向已启用的官方电子合同契约发起签署（不含密钥）",
    )
    return public_onboarding(db, hotel, include_esign=True)


def submit_for_approval(db: Session, hotel: Hotel, *, actor: str) -> dict[str, Any]:
    row = _get_or_raise(db, hotel)
    _require_status(row, {STATUS_SIGNING, STATUS_PENDING_APPROVAL}, "请先完成签署再提交审批")
    row.status = STATUS_PENDING_APPROVAL
    row.submitted_for_approval_at = row.submitted_for_approval_at or _now()
    db.commit()
    write_audit(
        db,
        actor=actor,
        action="onboarding_submit_approval",
        entity_type="hotel_onboarding",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={"status": row.status, "approval_level": row.approval_level, "approval_required": row.approval_required},
        reason="商务电子合同提交逐级审批",
    )
    return public_onboarding(db, hotel, include_esign=True)


def approve_onboarding(db: Session, hotel: Hotel, *, actor: str) -> dict[str, Any]:
    row = _get_or_raise(db, hotel)
    _require_status(row, {STATUS_PENDING_APPROVAL}, "当前没有待审批的合同")
    now = _now()
    row.approval_level = int(row.approval_level or 0) + 1
    row.last_approved_by = actor
    row.last_approved_at = now
    if row.approval_level >= int(row.approval_required or DEFAULT_APPROVAL_REQUIRED):
        row.status = STATUS_CONTRACT_EFFECTIVE
        row.contract_effective_at = now
    db.commit()
    write_audit(
        db,
        actor=actor,
        action="onboarding_approve",
        entity_type="hotel_onboarding",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={
            "status": row.status,
            "approval_level": row.approval_level,
            "approval_required": row.approval_required,
        },
        reason="合同岗逐级审批商务电子合同",
    )
    return public_onboarding(db, hotel, include_esign=True)


def mark_payment_pending(db: Session, hotel: Hotel, *, actor: str) -> dict[str, Any]:
    row = _get_or_raise(db, hotel)
    _require_status(row, {STATUS_CONTRACT_EFFECTIVE, STATUS_PAYMENT_PENDING}, "双方合同生效后才能进入待确认付款")
    row.status = STATUS_PAYMENT_PENDING
    db.commit()
    write_audit(
        db,
        actor=actor,
        action="onboarding_payment_pending",
        entity_type="hotel_onboarding",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={"status": row.status},
        reason="双方合同已生效，进入待确认付款（不是支付通道对接）",
    )
    return public_onboarding(db, hotel, include_esign=True)


def confirm_payment(db: Session, hotel: Hotel, *, actor: str) -> dict[str, Any]:
    row = _get_or_raise(db, hotel)
    _require_status(row, {STATUS_PAYMENT_PENDING, STATUS_PAYMENT_CONFIRMED}, "进入待确认付款后才能确认付款")
    now = _now()
    row.status = STATUS_PAYMENT_CONFIRMED
    row.payment_confirmed_by = actor
    row.payment_confirmed_at = now
    db.commit()
    write_audit(
        db,
        actor=actor,
        action="onboarding_confirm_payment",
        entity_type="hotel_onboarding",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={"status": row.status},
        reason="确认付款（预留状态钩子，不是支付通道或计费产品）",
    )
    return public_onboarding(db, hotel, include_esign=True)


def enable_product(db: Session, hotel: Hotel, *, actor: str) -> dict[str, Any]:
    row = _get_or_raise(db, hotel)
    _require_status(
        row,
        {STATUS_PAYMENT_CONFIRMED, STATUS_PRODUCT_ENABLED},
        "须先确认付款后才能开通酒店产品权限，不能跳过商务开通流程",
    )
    now = _now()
    row.status = STATUS_PRODUCT_ENABLED
    row.product_enabled_by = actor
    row.product_enabled_at = now
    db.commit()
    write_audit(
        db,
        actor=actor,
        action="onboarding_enable_product",
        entity_type="hotel_onboarding",
        entity_id=str(hotel.id),
        hotel_id=hotel.id,
        after={"status": row.status, "product_enabled": True},
        reason="付款已确认，开通酒店产品能力与后台权限",
    )
    return public_onboarding(db, hotel, include_esign=True)


def list_onboarding(db: Session) -> list[dict[str, Any]]:
    hotels = db.query(Hotel).order_by(Hotel.id.asc()).all()
    return [public_onboarding(db, hotel, include_esign=False) for hotel in hotels]
