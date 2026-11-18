"""Server-side per-task model preference table.

Business code must not bind itself to a vendor. Preferences are a routing
hint for the gateway only, overridable via server environment variables.
Hotels never configure this table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import CRITICAL_TASKS, OVERSEAS_VENDORS, Settings

KNOWN_VENDORS = ("deepseek", "qwen", "self_hosted", "openai", "gemini", "grok", "mock")
KNOWN_TASKS = (
    "analysis",
    "pricing_strategy",
    "review_reply",
    "assistant_chat",
    "vision_or_longdoc",
)

# Default order by model strengths. Safe default remains domestic-first.
# Overseas names may appear here only for non-critical tasks; they are
# stripped unless the overseas switch is on and the task is not critical.
DEFAULT_DOMESTIC_CHAIN = ("deepseek", "qwen", "self_hosted", "mock")

DEFAULT_TASK_PREFERENCES: dict[str, tuple[str, ...]] = {
    "analysis": DEFAULT_DOMESTIC_CHAIN,
    "pricing_strategy": DEFAULT_DOMESTIC_CHAIN,
    "review_reply": DEFAULT_DOMESTIC_CHAIN,
    "assistant_chat": DEFAULT_DOMESTIC_CHAIN,
    # After official domestic APIs and the reserved self-hosted hook,
    # prefer Gemini for long context / multimodal when overseas is allowed.
    "vision_or_longdoc": ("deepseek", "qwen", "self_hosted", "gemini", "openai", "grok", "mock"),
}

TASK_STRENGTHS: dict[str, str] = {
    "analysis": "推理与叙事，默认 DeepSeek → Qwen → 自建开源（预留）→ Mock",
    "pricing_strategy": "定价策略建议（关键任务，禁出境），默认 DeepSeek → Qwen → 自建开源（预留）→ Mock",
    "review_reply": "评价回复草稿（关键任务，禁出境），默认 DeepSeek → Qwen → 自建开源（预留）→ Mock",
    "assistant_chat": "助手对话，默认 DeepSeek → Qwen → 自建开源（预留）→ Mock；境外仅在开关打开且非关键时追加",
    "vision_or_longdoc": "长上下文与多模态；国内官方 API 与自建开源预留之后，境外打开且非关键时可优先 Gemini",
}

# When overseas is allowed and the resolved preference has no overseas
# vendor, append this list before Mock so the historical default chain is kept.
DEFAULT_OVERSEAS_APPEND: dict[str, tuple[str, ...]] = {
    "analysis": ("openai", "gemini", "grok"),
    "assistant_chat": ("openai", "gemini", "grok"),
    "vision_or_longdoc": ("gemini", "openai", "grok"),
}

_TASK_ENV_FIELDS: dict[str, str] = {
    "analysis": "llm_task_pref_analysis",
    "pricing_strategy": "llm_task_pref_pricing_strategy",
    "review_reply": "llm_task_pref_review_reply",
    "assistant_chat": "llm_task_pref_assistant_chat",
    "vision_or_longdoc": "llm_task_pref_vision_or_longdoc",
}


@dataclass(frozen=True)
class PreferenceNote:
    task: str
    vendor: str
    reason: str


@dataclass
class PreferenceTable:
    """Resolved preferred vendor lists (may still include overseas names)."""

    by_task: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notes: list[PreferenceNote] = field(default_factory=list)
    overrides: dict[str, str] = field(default_factory=dict)

    def preferred(self, task_type: str) -> tuple[str, ...]:
        if task_type in self.by_task:
            return self.by_task[task_type]
        return DEFAULT_TASK_PREFERENCES.get(task_type, DEFAULT_DOMESTIC_CHAIN)


def parse_vendor_list(raw: str) -> tuple[list[str], list[str]]:
    """Return (known vendors in order, unknown tokens). Empty raw → ([], [])."""
    known: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for token in (raw or "").split(","):
        name = token.strip().lower()
        if not name:
            continue
        if name not in KNOWN_VENDORS:
            unknown.append(name)
            continue
        if name in seen:
            continue
        seen.add(name)
        known.append(name)
    return known, unknown


def _field_value(settings: Settings, task: str) -> str:
    attr = _TASK_ENV_FIELDS.get(task)
    if not attr:
        return ""
    return str(getattr(settings, attr, "") or "").strip()


def resolve_preferences(settings: Settings) -> PreferenceTable:
    table = PreferenceTable()
    for task in KNOWN_TASKS:
        raw = _field_value(settings, task)
        default = list(DEFAULT_TASK_PREFERENCES[task])
        if not raw:
            table.by_task[task] = tuple(default)
            continue
        table.overrides[task] = raw
        vendors, unknown = parse_vendor_list(raw)
        for name in unknown:
            table.notes.append(PreferenceNote(task, name, "unknown_vendor_ignored"))
        critical = task in CRITICAL_TASKS
        kept: list[str] = []
        for name in vendors:
            if critical and name in OVERSEAS_VENDORS:
                table.notes.append(
                    PreferenceNote(task, name, "critical_task_blocked_from_overseas")
                )
                continue
            kept.append(name)
        if "mock" not in kept:
            kept.append("mock")
        if not any(v not in {"mock"} and v not in OVERSEAS_VENDORS for v in kept):
            # An override that removed every domestic vendor — fall back.
            table.notes.append(PreferenceNote(task, "", "override_missing_domestic_fallback_default"))
            kept = default
        table.by_task[task] = tuple(_dedupe_keep_mock_last(kept))
    return table


def _dedupe_keep_mock_last(vendors: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in vendors:
        if name == "mock" or name in seen:
            continue
        seen.add(name)
        out.append(name)
    out.append("mock")
    return out


def insert_before_mock(chain: list[str], extra: list[str]) -> list[str]:
    head = [v for v in chain if v != "mock"]
    seen = set(head)
    for name in extra:
        if name not in seen:
            head.append(name)
            seen.add(name)
    head.append("mock")
    return head


def build_route_chain(
    task_type: str,
    *,
    critical: bool,
    overseas_enabled: bool,
    table: PreferenceTable,
) -> list[str]:
    """Build the try-order. Availability (keys) is checked later at call time."""
    allow_overseas = bool(overseas_enabled) and not critical
    chain = list(table.preferred(task_type))
    if allow_overseas:
        if not any(v in OVERSEAS_VENDORS for v in chain):
            extra = list(DEFAULT_OVERSEAS_APPEND.get(task_type, ("openai", "gemini", "grok")))
            chain = insert_before_mock(chain, extra)
    else:
        chain = [v for v in chain if v not in OVERSEAS_VENDORS]
    # Defence in depth: never let an overseas vendor onto a critical chain.
    if critical or not allow_overseas:
        chain = [v for v in chain if v not in OVERSEAS_VENDORS]
    chain = _dedupe_keep_mock_last(chain)
    return chain
