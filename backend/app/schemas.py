"""Pydantic schemas for the API surface."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    hotel_id: int | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    display_name: str
    role: str
    hotel_id: int
    hotel_name: str
    city: str = ""
    product_enabled: bool = True
    product_status: str = "已开通"
    onboarding_status: str = "product_enabled"
    onboarding_status_label: str = "已开通"


class UserMe(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    role_label: str = ""
    disabled: bool = False
    hotel_id: int
    hotel_name: str
    city: str
    address: str = ""
    positioning: str = ""
    platform: str
    tenant_key: str = ""
    pages: list[str] = []
    permissions: dict[str, bool] = {}
    product_enabled: bool = True
    product_status: str = "已开通"
    onboarding_status: str = "product_enabled"
    onboarding_status_label: str = "已开通"


class ReviewDecision(BaseModel):
    decision: str = Field(description="approve or reject")
    comment: str = ""


class RollbackRequest(BaseModel):
    reason: str = ""


class GenerateStrategiesRequest(BaseModel):
    room_type_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    kind: Literal["all", "pricing", "campaign"] = "all"


class DraftReplyRequest(BaseModel):
    pass


class ChatRequest(BaseModel):
    message: str


class ExecutionPolicyUpdate(BaseModel):
    auto_execute_enabled: bool | None = None
    auto_execute_max_risk: str | None = None
    holiday_requires_review: bool | None = None
    price_change_human_threshold_pct: float | None = None
    rollback_policy: str | None = None
    write_mode: str | None = None


class SettingsUpdate(BaseModel):
    overseas_enabled: bool | None = None
    execution_policy: ExecutionPolicyUpdate | None = None


class RoomConstraintsUpdate(BaseModel):
    min_price: float
    max_price: float


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str
    role: str


class UserUpdate(BaseModel):
    display_name: str | None = None
    role: str | None = None
    password: str | None = None
    disabled: bool | None = None


class HealthResponse(BaseModel):
    status: str
    app: str
    workflow_engine: str
    time: datetime
    job_queue: str = "local"


# Generic envelopes used in tests and docs
class OkResponse(BaseModel):
    ok: bool = True
    data: Any = None
    message: str = ""
