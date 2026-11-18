"""Reserved self-hosted open-source model (OpenAI-compatible endpoint).

This is a hook, not a claim that an OSS model is already deployed.
It stays disabled until SELF_HOSTED_BASE_URL, SELF_HOSTED_API_KEY and
SELF_HOSTED_MODEL are all set on the server. No production host is invented here.
"""

from __future__ import annotations

from app.config import Settings
from app.gateway.adapters.openai_compat import OpenAICompatAdapter


class SelfHostedAdapter(OpenAICompatAdapter):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            vendor="self_hosted",
            api_key=settings.self_hosted_api_key,
            base_url=(settings.self_hosted_base_url or "").rstrip("/"),
            model=settings.self_hosted_model,
            outbound=False,
            timeout=settings.llm_timeout_seconds,
        )

    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)
