"""Shared OpenAI-compatible chat completions client (DeepSeek / Qwen / OpenAI / Grok)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.gateway.adapters.base import BaseAdapter
from app.gateway.protocol import LLMRequest, LLMResponse


def _parse_json_content(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


class OpenAICompatAdapter(BaseAdapter):
    def __init__(
        self,
        vendor: str,
        api_key: str,
        base_url: str,
        model: str,
        outbound: bool,
        timeout: float,
    ) -> None:
        self.vendor = vendor
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.outbound = outbound
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_schema:
            payload["response_format"] = {"type": "json_object"}
            # Nudge the model if the caller didn't already mention JSON.
            msgs = list(request.messages)
            if msgs and msgs[0].get("role") == "system":
                msgs[0] = {
                    **msgs[0],
                    "content": (msgs[0].get("content") or "") + "\n请只输出 JSON 对象。",
                }
            else:
                msgs.insert(0, {"role": "system", "content": "请只输出 JSON 对象。"})
            payload["messages"] = msgs
        if request.allow_tools and request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters or {"type": "object", "properties": {}},
                    },
                }
                for t in request.tools
            ]

        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()

        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or "{}"
            try:
                parsed_args = json.loads(args)
            except json.JSONDecodeError:
                parsed_args = {"_raw": args}
            tool_calls.append({"name": fn.get("name") or "", "arguments": parsed_args, "id": tc.get("id")})
        usage = body.get("usage") or {}
        structured = _parse_json_content(content) if request.response_schema else None
        return LLMResponse(
            vendor=self.vendor,
            model=body.get("model") or self.model,
            content=content,
            structured=structured,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
            },
            degraded=False,
            outbound=self.outbound,
            status="ok",
        )
