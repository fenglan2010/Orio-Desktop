from __future__ import annotations

from app.config import Settings
from app.gateway.adapters.openai_compat import OpenAICompatAdapter


class DeepSeekAdapter(OpenAICompatAdapter):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            vendor="deepseek",
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            outbound=False,
            timeout=settings.llm_timeout_seconds,
        )
