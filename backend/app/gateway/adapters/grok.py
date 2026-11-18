from __future__ import annotations

from app.config import Settings
from app.gateway.adapters.openai_compat import OpenAICompatAdapter


class GrokAdapter(OpenAICompatAdapter):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            vendor="grok",
            api_key=settings.grok_api_key,
            base_url=settings.grok_base_url,
            model=settings.grok_model,
            outbound=True,
            timeout=settings.llm_timeout_seconds,
        )
