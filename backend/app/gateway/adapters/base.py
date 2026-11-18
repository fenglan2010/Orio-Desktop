"""Adapter contract. All vendors implement this and nothing else leaks into business code."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.gateway.protocol import LLMRequest, LLMResponse


class BaseAdapter(ABC):
    vendor: str
    outbound: bool = False

    @abstractmethod
    def available(self) -> bool:
        """True when a real key is configured and the adapter can be called."""

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Synchronous completion. The gateway handles timeout around this call."""
