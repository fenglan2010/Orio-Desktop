"""Packaged desktop initial hotel setup (no auth until complete)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.first_run import complete_initial_setup, initial_setup_status

router = APIRouter(prefix="/api/first-run", tags=["first-run"])


class FirstRunRoomType(BaseModel):
    code: str | None = Field(default=None, description="房型代码")
    room_type: str | None = Field(default=None, description="房型代码别名")
    name: str
    inventory: int | None = None
    quantity: int | None = None
    base_price: float | None = None
    price: float | None = None
    location: str = ""
    capacity: int | None = None


class OtaSetupItem(BaseModel):
    platform: str
    account: str | None = None
    password: str | None = None
    contract_file_path: str | None = None


class PmsSetupItem(BaseModel):
    kind: str
    api_endpoint: str | None = None
    contract_file_path: str | None = None


class ModelKeysSetup(BaseModel):
    vendors: dict[str, Any] = Field(default_factory=dict)


class FirstRunCompleteBody(BaseModel):
    hotel_name: str
    city: str = ""
    address: str = ""
    positioning: str = ""
    platform: str = "ctrip"
    admin_username: str
    admin_password: str
    admin_display_name: str | None = None
    tenant_key: str | None = None
    room_types: list[FirstRunRoomType] = Field(min_length=1)
    model_keys: ModelKeysSetup | None = None
    ota: list[OtaSetupItem] = Field(default_factory=list)
    pms: list[PmsSetupItem] = Field(default_factory=list)


@router.get("/status")
def first_run_status(db: Session = Depends(get_db)):
    return initial_setup_status(db)


@router.post("/complete")
def first_run_complete(body: FirstRunCompleteBody, db: Session = Depends(get_db)):
    payload = body.model_dump()
    if body.model_keys is not None:
        payload["model_keys"] = body.model_keys.model_dump()
    try:
        return complete_initial_setup(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc