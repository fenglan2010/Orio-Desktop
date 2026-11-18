"""Official OTA write adapter for Phase II.

Default path records a sandbox official receipt locally and never opens an
outbound HTTP connection. Live mode sends a real official HTTP write only when
the channel is authorized, credentials exist, the write flag is on, and an
official contract is configured. Missing any of those refuses clearly and does
not open a socket. Review replies are never auto-posted and never sent live.

Sandbox may simulate accepted, partial, timeout, or failed receipts when the
caller (or intended payload) sets `_sandbox_simulate`. Normal demo writes omit
that flag and always record a full accepted sandbox receipt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


MODE_SANDBOX = "sandbox_official"
MODE_LIVE = "live"
MODE_REFUSED = "refused"

OUTCOME_ACCEPTED = "accepted"
OUTCOME_PARTIAL = "partial"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_FAILED = "failed"
OUTCOME_REFUSED = "refused"

SIMULATE_VALUES = {OUTCOME_ACCEPTED, OUTCOME_PARTIAL, OUTCOME_TIMEOUT, OUTCOME_FAILED}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_request_id() -> str:
    return "req-" + uuid.uuid4().hex[:16]


def new_receipt_id() -> str:
    return "rcpt-" + uuid.uuid4().hex[:16]


def _clean_payload(payload: dict | None) -> dict:
    data = dict(payload or {})
    data.pop("_sandbox_simulate", None)
    data.pop("sandbox_simulate", None)
    return data


def resolve_simulate(payload: dict | None, simulate: str | None = None) -> str:
    raw = simulate or (payload or {}).get("_sandbox_simulate") or (payload or {}).get("sandbox_simulate") or OUTCOME_ACCEPTED
    value = str(raw).strip().lower()
    if value in {"ok", "success", "accepted"}:
        return OUTCOME_ACCEPTED
    if value in SIMULATE_VALUES:
        return value
    return OUTCOME_ACCEPTED


def build_intended_items(payload: dict | None, action: str = "apply_price") -> list[dict[str, Any]]:
    """Build official-shaped line items from a write payload."""
    payload = payload or {}
    if isinstance(payload.get("items"), list) and payload["items"]:
        items = []
        for raw in payload["items"]:
            if not isinstance(raw, dict):
                continue
            items.append(dict(raw))
        if items:
            return items

    items: list[dict[str, Any]] = []
    if action == "post_review_reply":
        items.append(
            {
                "type": "review_reply",
                "review_id": payload.get("review_id"),
                "reply": payload.get("reply") or payload.get("draft") or "",
            }
        )
        return items

    if action == "apply_campaign":
        campaign = payload.get("campaign") or payload.get("campaign_advice") or {}
        if not isinstance(campaign, dict):
            campaign = {"campaign_name": str(campaign)}
        items.append(
            {
                "type": "campaign_signup",
                "campaign_name": campaign.get("campaign_name") or campaign.get("name") or "渠道活动",
                "recommendation": campaign.get("recommendation"),
                "recommend_join": bool(campaign.get("recommend_join")),
                "intended": "join" if campaign.get("recommend_join") or campaign.get("recommendation") in {"join", "join_limited"} else "do_not_join",
            }
        )
        return items

    breakdown = payload.get("daily_breakdown") or []
    room_type_id = payload.get("room_type_id")
    room_name = payload.get("room_name")
    if breakdown:
        for day in breakdown:
            if not isinstance(day, dict):
                continue
            items.append(
                {
                    "type": "price",
                    "room_type_id": day.get("room_type_id") or room_type_id,
                    "room_name": day.get("room_name") or room_name,
                    "date": day.get("date"),
                    "intended_price": day.get("recommended") or day.get("intended_price") or payload.get("recommended_price"),
                    "current_price": day.get("current_list") or day.get("current_price"),
                }
            )
        return items

    if payload.get("recommended_price") is not None:
        items.append(
            {
                "type": "price",
                "room_type_id": room_type_id,
                "room_name": room_name,
                "date_from": payload.get("date_from"),
                "date_to": payload.get("date_to"),
                "intended_price": payload.get("recommended_price"),
                "current_price": payload.get("current_price"),
            }
        )
    return items


def _simulate_items(items: list[dict], simulate: str) -> tuple[list[dict], str, bool, str]:
    """Return (receipt_items, outcome, accepted, message_suffix)."""
    receipt_items: list[dict] = []
    if simulate == OUTCOME_TIMEOUT:
        for item in items:
            receipt_items.append({**item, "accepted": False, "status": "timeout", "error": "沙盒模拟：写入超时，未收到官方确认。"})
        return receipt_items, OUTCOME_TIMEOUT, False, "沙盒模拟超时，未向真实 OTA 发送请求。"
    if simulate == OUTCOME_FAILED:
        for item in items:
            receipt_items.append({**item, "accepted": False, "status": "failed", "error": "沙盒模拟：写入失败。"})
        return receipt_items, OUTCOME_FAILED, False, "沙盒模拟写入失败，未向真实 OTA 发送请求。"
    if simulate == OUTCOME_PARTIAL:
        if not items:
            return [], OUTCOME_PARTIAL, False, "沙盒模拟部分成功，但没有可写入的行项目。"
        cut = max(1, len(items) // 2)
        for index, item in enumerate(items):
            if index < cut:
                receipt_items.append(
                    {
                        **item,
                        "accepted": True,
                        "status": "accepted",
                        "actual_price": item.get("intended_price"),
                    }
                )
            else:
                receipt_items.append({**item, "accepted": False, "status": "failed", "error": "沙盒模拟：该行写入失败。"})
        return receipt_items, OUTCOME_PARTIAL, False, "沙盒模拟部分成功，部分行项目未写入，未向真实 OTA 发送请求。"
    for item in items:
        row = {**item, "accepted": True, "status": "accepted"}
        if item.get("intended_price") is not None:
            row["actual_price"] = item.get("intended_price")
        receipt_items.append(row)
    return receipt_items, OUTCOME_ACCEPTED, True, ""


def build_write_receipt(
    *,
    platform: str,
    mode: str,
    accepted: bool,
    message: str,
    payload: dict | None,
    checkpoint_id: str | None = None,
    action: str = "apply_price",
    extra: dict[str, Any] | None = None,
    items: list[dict] | None = None,
    outcome: str | None = None,
    request_id: str | None = None,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    resolved_outcome = outcome or (OUTCOME_ACCEPTED if accepted else OUTCOME_FAILED)
    receipt: dict[str, Any] = {
        "platform": platform,
        "mode": mode,
        "accepted": bool(accepted),
        "outcome": resolved_outcome,
        "message": message,
        "checkpoint_id": checkpoint_id or (payload or {}).get("checkpoint_id") or "",
        "payload": _clean_payload(payload),
        "timestamp": _utc_now(),
        "action": action,
        "request_id": request_id or new_request_id(),
        "receipt_id": receipt_id or (None if resolved_outcome == OUTCOME_TIMEOUT else new_receipt_id()),
        "items": items if items is not None else build_intended_items(payload, action),
    }
    if extra:
        receipt.update(extra)
    return receipt


class OfficialWriteAdapter:
    """Official write contract. Sandbox is local; live uses OfficialHttpClient when ready."""

    def __init__(self, platform: str = "ctrip"):
        self.platform = platform

    def apply_price(self, payload: dict, *, write_mode: str = "sandbox", simulate: str | None = None, live_context=None) -> dict:
        return self._dispatch("apply_price", payload, write_mode, target="价格", simulate=simulate, live_context=live_context)

    def post_review_reply(self, payload: dict, *, write_mode: str = "sandbox", simulate: str | None = None, live_context=None) -> dict:
        return self._dispatch("post_review_reply", payload, write_mode, target="评价回复", simulate=simulate, live_context=live_context)

    def apply_campaign(self, payload: dict, *, write_mode: str = "sandbox", simulate: str | None = None, live_context=None) -> dict:
        return self._dispatch("apply_campaign", payload, write_mode, target="活动报名", simulate=simulate, live_context=live_context)

    def _dispatch(self, action: str, payload: dict, write_mode: str, *, target: str, simulate: str | None = None, live_context=None) -> dict:
        mode = (write_mode or "sandbox").strip().lower()
        if mode in {"live", "production", "official_live"}:
            return self._live_or_refuse(action, payload, target, live_context=live_context)
        intended = build_intended_items(payload, action)
        outcome_sim = resolve_simulate(payload, simulate)
        items, outcome, accepted, sim_msg = _simulate_items(intended, outcome_sim)
        message = sim_msg or f"已按官方写接口合同记录{target}沙盒回执，未向携程或美团发送真实请求。"
        extra = {}
        if outcome == OUTCOME_PARTIAL:
            extra["partial"] = True
        if outcome_sim != OUTCOME_ACCEPTED:
            extra["simulated_outcome"] = outcome_sim
        return build_write_receipt(
            platform=self.platform,
            mode=MODE_SANDBOX,
            accepted=accepted,
            message=message,
            payload=payload,
            checkpoint_id=(payload or {}).get("checkpoint_id") or "",
            action=action,
            items=items,
            outcome=outcome,
            extra=extra,
        )

    def _refuse(self, action: str, payload: dict, intended: list[dict], reason: str, message: str) -> dict:
        refused_items = [{**item, "accepted": False, "status": "refused"} for item in intended]
        return build_write_receipt(
            platform=self.platform,
            mode=MODE_LIVE,
            accepted=False,
            message=message,
            payload=payload,
            checkpoint_id=(payload or {}).get("checkpoint_id") or "",
            action=action,
            items=refused_items,
            outcome=OUTCOME_REFUSED,
            extra={"refused": True, "refuse_reason": reason},
        )

    def _live_or_refuse(self, action: str, payload: dict, target: str, live_context=None) -> dict:
        intended = build_intended_items(payload, action)
        if action == "post_review_reply":
            return self._refuse(
                action,
                payload,
                intended,
                "review_reply_not_auto_posted",
                "评价回复不会自动对外发布。即使已人审，本阶段也不向官方渠道出站发送回复。",
            )

        if live_context is None:
            from app.services.channels import resolve_live_write_context

            live_context = resolve_live_write_context(None, None, self.platform)

        if live_context.refuse_reason:
            return self._refuse(
                action,
                payload,
                intended,
                live_context.refuse_reason,
                live_context.refuse_message or "已拒绝真实写入，未发起外部请求。",
            )

        from app.adapters.ota.official_http import OfficialHttpClient, OfficialHttpError, contract_is_configured

        if not contract_is_configured(live_context.contract, action):
            if action == "apply_campaign":
                return self._refuse(
                    action,
                    payload,
                    intended,
                    "action_not_in_contract",
                    f"已配置官方契约，但未包含{target}动作，已拒绝出站请求。",
                )
            return self._refuse(
                action,
                payload,
                intended,
                "missing_contract",
                "未配置该写入动作的官方契约，已拒绝出站请求。",
            )

        client = OfficialHttpClient(self.platform, live_context.contract, live_context.credentials)
        try:
            return client.send_write(action, payload, intended, request_id=(payload or {}).get("request_id"))
        except OfficialHttpError as exc:
            items = [{**item, "accepted": False, "status": exc.outcome} for item in intended]
            extra = {"http_status": exc.status_code}
            if not exc.retryable:
                extra["refused"] = True
                extra["refuse_reason"] = "http_4xx" if exc.outcome == OUTCOME_REFUSED else (exc.outcome or "refused")
            return build_write_receipt(
                platform=self.platform,
                mode=MODE_LIVE,
                accepted=False,
                message=exc.message,
                payload=payload,
                checkpoint_id=(payload or {}).get("checkpoint_id") or "",
                action=action,
                items=items,
                outcome=exc.outcome,
                extra=extra,
            )


def get_write_adapter(platform: str = "ctrip") -> OfficialWriteAdapter:
    return OfficialWriteAdapter(platform=platform)
