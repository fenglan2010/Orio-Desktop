"""Unified model gateway. Business code talks only to this package."""

from app.gateway.protocol import LLMRequest, LLMResponse, ToolSpec
from app.gateway.router import ModelGateway, get_gateway

__all__ = ["LLMRequest", "LLMResponse", "ToolSpec", "ModelGateway", "get_gateway"]
