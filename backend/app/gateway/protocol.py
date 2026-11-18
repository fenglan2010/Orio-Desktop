"""Unified request/response protocol for every model adapter."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    task_type: str
    messages: list[dict[str, Any]]
    tools: list[ToolSpec] = Field(default_factory=list)
    allow_tools: bool = False
    response_schema: dict[str, Any] | None = None
    hotel_id: int | None = None
    tenant_key: str = "demo"
    temperature: float = 0.3
    max_tokens: int = 1200
    # If true, this call may lead to a price/reply execution and must never go overseas.
    execution_critical: bool = False


class LLMResponse(BaseModel):
    vendor: str
    model: str
    content: str = ""
    structured: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    degraded: bool = False
    outbound: bool = False
    status: str = "ok"
    latency_ms: int = 0
    degrade_notice: str = ""


TASK_TYPES = (
    "analysis",
    "pricing_strategy",
    "review_reply",
    "assistant_chat",
    "vision_or_longdoc",
)
