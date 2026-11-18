from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.gateway.adapters.base import BaseAdapter
from app.gateway.adapters.openai_compat import _parse_json_content
from app.gateway.protocol import LLMRequest, LLMResponse


class GeminiAdapter(BaseAdapter):
    vendor = "gemini"
    outbound = True

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.gemini_api_key
        self.base_url = settings.gemini_base_url.rstrip("/")
        self.model = settings.gemini_model
        self.timeout = settings.llm_timeout_seconds

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        system_parts = []
        contents: list[dict[str, Any]] = []
        for msg in request.messages:
            role = msg.get("role")
            text = msg.get("content") or ""
            if role == "system":
                system_parts.append(text)
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": text}]})
            else:
                contents.append({"role": "user", "parts": [{"text": text}]})
        payload: dict[str, Any] = {
            "contents": contents or [{"role": "user", "parts": [{"text": "你好"}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        if request.response_schema:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        url = f"{self.base_url}/models/{self.model}:generateContent"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, params={"key": self.api_key}, json=payload)
            resp.raise_for_status()
            body = resp.json()

        candidates = body.get("candidates") or [{}]
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        content = "".join(p.get("text") or "" for p in parts)
        usage_meta = body.get("usageMetadata") or {}
        structured = _parse_json_content(content) if request.response_schema else None
        return LLMResponse(
            vendor="gemini",
            model=self.model,
            content=content,
            structured=structured,
            usage={
                "prompt_tokens": int(usage_meta.get("promptTokenCount") or 0),
                "completion_tokens": int(usage_meta.get("candidatesTokenCount") or 0),
            },
            degraded=False,
            outbound=True,
            status="ok",
        )
