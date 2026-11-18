"""Application settings. Secrets come from the environment; never log raw keys."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("OIRO_DATA_DIR", str(PROJECT_ROOT / "data")))
LOCAL_DIR = DATA_DIR / "local"
DEFAULT_DB_PATH = DATA_DIR / "oiro.db"

CRITICAL_TASKS = frozenset({"pricing_strategy", "review_reply"})
OVERSEAS_VENDORS = frozenset({"openai", "gemini", "grok"})
DOMESTIC_VENDORS = frozenset({"deepseek", "qwen", "self_hosted"})

# Local-demo default only. Production (OIRO_ENV=production) must replace this.
DEMO_JWT_SECRET = "oiro-demo-secret-change-me"
PRODUCTION_ENVS = frozenset({"production", "prod"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OIRO"
    # development | production (also accepts prod). Local demo stays development.
    oiro_env: str = "development"
    # source | packaged — official desktop installers set packaged; from-source stays source.
    oiro_distribution: str = Field(default="source", validation_alias="OIRO_DISTRIBUTION")
    database_url: str = Field(default=f"sqlite:///{DEFAULT_DB_PATH}")
    jwt_secret: str = DEMO_JWT_SECRET
    jwt_expire_hours: int = 24

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_model: str = "gemini-2.0-flash"

    grok_api_key: str = ""
    grok_base_url: str = "https://api.x.ai/v1"
    grok_model: str = "grok-2-latest"

    # Reserved self-hosted OSS model (OpenAI-compatible). All empty = disabled.
    # Never invent a production host. Fill these only after deploying one
    # on 腾讯云 / 阿里云 (or another in-country host). Server / ops only.
    self_hosted_api_key: str = ""
    self_hosted_base_url: str = ""
    self_hosted_model: str = ""

    overseas_enabled: bool = False

    # Per-task model preference overrides (comma-separated vendor names).
    # Empty means the documented default. Unknown vendors are ignored.
    # Critical tasks cannot be routed overseas even if listed here.
    # Server / ops only — never expose as hotel Settings.
    llm_task_pref_analysis: str = ""
    llm_task_pref_pricing_strategy: str = ""
    llm_task_pref_review_reply: str = ""
    llm_task_pref_assistant_chat: str = ""
    llm_task_pref_vision_or_longdoc: str = ""

    # In-process gateway resilience. Multi-replica needs a later shared limiter.
    llm_rate_limit_per_minute: int = 30
    llm_vendor_concurrency: int = 4
    llm_global_concurrency: int = 8
    llm_queue_wait_seconds: float = 5.0
    llm_circuit_failure_threshold: int = 3
    llm_circuit_cooldown_seconds: float = 30.0

    # Official OTA write (server-only). Never expose these on the hotel UI.
    # Live HTTP is sent only when the channel is authorized, credentials exist,
    # the platform write flag is on, and an official contract is configured.
    # Default demo leaves these off. This repo does not ship production
    # Ctrip/Meituan/Fliggy URLs; contracts are loaded from env/file/settings.
    ctrip_write_enabled: bool = False
    ctrip_write_key: str = ""
    ctrip_write_secret: str = ""
    ctrip_hotel_id: str = ""

    meituan_write_enabled: bool = False
    meituan_write_key: str = ""
    meituan_write_secret: str = ""
    meituan_hotel_id: str = ""

    fliggy_write_enabled: bool = False
    fliggy_write_key: str = ""
    fliggy_write_secret: str = ""
    fliggy_hotel_id: str = ""
    fliggy_session: str = ""

    oiro_official_contract_dir: str = ""
    oiro_official_contract_ctrip: str = ""
    oiro_official_contract_meituan: str = ""
    oiro_official_contract_fliggy: str = ""
    oiro_official_pms_contract_dir: str = ""
    oiro_official_pms_contract: str = ""
    oiro_official_crs_contract: str = ""
    oiro_official_esign_contract_dir: str = ""
    oiro_official_esign_contract: str = ""
    esign_enabled: bool = False
    pms_read_enabled: bool = False
    crs_read_enabled: bool = False
    pms_write_enabled: bool = False
    crs_write_enabled: bool = False
    oiro_credentials_key: str = ""

    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 0

    demo_username: str = "admin"
    demo_password: str = "admin123"

    # Background job queue. Leave broker empty for the local standalone worker
    # (no Redis required). Never log these URLs; they may contain passwords.
    celery_broker_url: str = ""
    redis_url: str = ""
    oiro_job_queue: str = "auto"  # auto | local | celery | off
    oiro_ingest_interval_seconds: int = 1800
    oiro_write_retry_interval_seconds: int = 120
    oiro_reconcile_interval_seconds: int = 300
    oiro_job_startup_delay_seconds: int = 5


class ProductionConfigError(RuntimeError):
    """Unsafe production settings. The message must never include secrets or URLs."""


def is_production_env(settings: Settings | None = None, value: str | None = None) -> bool:
    raw = value if value is not None else (settings or get_settings()).oiro_env
    return (raw or "").strip().lower() in PRODUCTION_ENVS


def validate_runtime_settings(settings: Settings | None = None) -> Settings:
    """Refuse an unsafe production process.

    When OIRO_ENV=production (or prod): reject the demo JWT_SECRET, and do not
    silently use SQLite as the database. Development and tests are unchanged.
    Error text must not contain passwords, URLs, or the secret itself.
    """
    s = settings or get_settings()
    if not is_production_env(s):
        return s
    problems: list[str] = []
    jwt = (s.jwt_secret or "").strip()
    if not jwt or jwt == DEMO_JWT_SECRET:
        problems.append(
            "OIRO_ENV=production 不能使用默认演示 JWT_SECRET，请在启动前更换为独立密钥。"
        )
    url = (s.database_url or "").strip()
    if not url or url.lower().startswith("sqlite:"):
        problems.append(
            "OIRO_ENV=production 必须配置 PostgreSQL 的 DATABASE_URL，不会静默回退到 SQLite。"
        )
    if problems:
        raise ProductionConfigError(" ".join(problems))
    return s


@lru_cache
def get_settings() -> Settings:
    from app.services.distribution import ensure_data_dirs

    ensure_data_dirs()
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
