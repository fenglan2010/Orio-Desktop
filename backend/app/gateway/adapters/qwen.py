from __future__ import annotations

from app.config import Settings
from app.gateway.adapters.openai_compat import OpenAICompatAdapter


class QwenAdapter(OpenAICompatAdapter):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            vendor="qwen",
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            model=settings.qwen_model,
            outbound=False,
            timeout=settings.llm_timeout_seconds,
        )
