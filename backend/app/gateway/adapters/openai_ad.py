from __future__ import annotations

from app.config import Settings
from app.gateway.adapters.openai_compat import OpenAICompatAdapter


class OpenAIAdapter(OpenAICompatAdapter):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            vendor="openai",
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            outbound=True,
            timeout=settings.llm_timeout_seconds,
        )
