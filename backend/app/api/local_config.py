"""Local-only configuration: model keys and integration file paths."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.ota.official_http import ContractValidationError, PLATFORMS
from app.adapters.pms.official_http import KINDS
from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.models import Hotel, User
from app.services.local_integrations import public_local_integrations, save_ota_contract_path, save_pms_local_config
from app.services.local_secrets import public_model_keys_payload, save_model_keys
from app.services.permissions import require_permission
from app.services.workflow import write_audit

router = APIRouter(prefix="/api/local-config", tags=["local-config"], dependencies=[Depends(get_current_user)])


class ModelKeysBody(BaseModel):
    vendors: dict = Field(default_factory=dict)


class OtaContractPathBody(BaseModel):
    contract_file_path: str | None = None
    account: str | None = None


class PmsLocalBody(BaseModel):
    contract_file_path: str | None = None
    api_endpoint: str | None = None


@router.get("")
def read_local_config(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    return {
        "model_keys": public_model_keys_payload(),
        "integrations": public_local_integrations(db, hotel.id),
    }


@router.put("/model-keys")
def put_model_keys(
    body: ModelKeysBody,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    require_permission(user, "update_policy")
    try:
        saved = save_model_keys(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user.username,
        action="local_model_keys_update",
        entity_type="local_config",
        entity_id="model_keys",
        after={"vendors": list((saved.get("vendors") or {}).keys())},
        reason="在本机保存模型网关密钥（审计不含密钥）",
        hotel_id=hotel.id,
    )
    return saved


@router.put("/ota/{platform}/contract-path")
def put_ota_contract_path(
    platform: str,
    body: OtaContractPathBody,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    require_permission(user, "manage_channels")
    value = (platform or "").strip().lower()
    if value not in PLATFORMS:
        raise HTTPException(status_code=404, detail="仅支持携程、美团、飞猪")
    try:
        result = save_ota_contract_path(
            db,
            hotel.id,
            value,
            body.contract_file_path,
            account_hint=body.account,
        )
    except ContractValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user.username,
        action="ota_contract_path",
        entity_type="local_config",
        entity_id=f"{hotel.id}:{value}",
        after={"platform": value, "has_path": bool(result.get("contract_file_path"))},
        reason="绑定本机 OTA 官方契约 JSON 路径（不含文件正文）",
        hotel_id=hotel.id,
    )
    return result


@router.put("/pms/{kind}")
def put_pms_local(
    kind: str,
    body: PmsLocalBody,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    require_permission(user, "manage_channels")
    value = (kind or "").strip().lower()
    if value not in KINDS:
        raise HTTPException(status_code=404, detail="仅支持 PMS 或 CRS")
    try:
        result = save_pms_local_config(
            db,
            hotel.id,
            value,
            contract_file_path=body.contract_file_path,
            api_endpoint=body.api_endpoint,
        )
    except ContractValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user.username,
        action="pms_local_config",
        entity_type="local_config",
        entity_id=f"{hotel.id}:{value}",
        after={
            "kind": value,
            "has_contract_path": bool(result.get("contract_file_path")),
            "has_api_endpoint": bool(result.get("api_endpoint")),
        },
        reason="保存本机 PMS/CRS 契约路径与 API 位置（不含密钥）",
        hotel_id=hotel.id,
    )
    return result