"""Scheduled ingest: official HTTP only with a real read action; otherwise a mock refresh.

This module does not scrape OTA sites and does not invent official endpoints.
Demo story numbers are not rewritten when the path is a local mock refresh.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.adapters.ota.ctrip_mock import CtripMockAdapter
from app.adapters.ota.official_http import (
    OfficialHttpClient,
    OfficialHttpError,
    contract_is_configured,
    load_official_contract,
)
from app.models import ChannelConnection, DailyMetric, Hotel, Strategy
from app.services.channels import STATUS_AUTHORIZED, has_usable_credentials, merged_credentials
from app.services.pms import run_pms_ingest

WRITE_ACTIONS = frozenset({"apply_price", "apply_campaign", "post_review_reply"})
READ_ACTION_NAMES = frozenset(
    {
        "ingest",
        "read",
        "fetch",
        "sync",
        "fetch_metrics",
        "fetch_competitors",
        "fetch_reviews",
        "read_metrics",
        "read_competitors",
        "read_reviews",
        "ingest_metrics",
        "sync_metrics",
        "pull_metrics",
        "list_metrics",
        "get_metrics",
        "query_metrics",
        "fetch_data",
        "sync_data",
    }
)
READ_KIND_TAGS = frozenset({"read", "ingest", "fetch", "sync"})
READ_PREFIXES = ("fetch_", "read_", "ingest_", "sync_", "get_", "list_", "pull_", "query_")


class LocalRefreshAdapter(CtripMockAdapter):
    """Reread in-app rows for the hotel's platform. Does not invent market facts."""

    def __init__(self, platform: str = "ctrip"):
        self.platform = (platform or "ctrip").strip().lower() or "ctrip"


def is_read_action(name: str, spec: dict | None = None) -> bool:
    action = (name or "").strip().lower()
    if not action or action in WRITE_ACTIONS:
        return False
    payload = spec if isinstance(spec, dict) else {}
    kind = str(payload.get("kind") or payload.get("type") or payload.get("purpose") or "").strip().lower()
    if kind in READ_KIND_TAGS:
        return True
    if action in READ_ACTION_NAMES:
        return True
    return action.startswith(READ_PREFIXES)


def official_read_actions(contract: dict | None) -> list[str]:
    if not contract or not isinstance(contract, dict):
        return []
    actions = contract.get("actions") or {}
    if not isinstance(actions, dict):
        return []
    names: list[str] = []
    for name, spec in actions.items():
        if is_read_action(str(name), spec if isinstance(spec, dict) else None) and contract_is_configured(contract, str(name)):
            names.append(str(name))
    return names


def _channel_row(db: Session, hotel_id: int, platform: str) -> ChannelConnection | None:
    return (
        db.query(ChannelConnection)
        .filter(ChannelConnection.hotel_id == hotel_id, ChannelConnection.platform == platform)
        .one_or_none()
    )


def resolve_official_ingest(db: Session, hotel: Hotel, platform: str) -> dict[str, Any]:
    """Decide whether official HTTP ingest is allowed. Never invents a URL."""
    contract = load_official_contract(platform, db, hotel_id=hotel.id)
    read_actions = official_read_actions(contract)
    row = _channel_row(db, hotel.id, platform)
    creds = merged_credentials(row, platform)
    has_creds = has_usable_credentials(creds)
    authorized = bool(row and row.status == STATUS_AUTHORIZED and has_creds)
    if not authorized and has_creds and row is None:
        authorized = True

    if not read_actions:
        reason = "no_official_read_action"
        message = "官方契约未定义可读/采集动作，不会猜测 URL，改为本地模拟刷新。"
        if not contract:
            message = "未安装带采集动作的官方契约，改为本地模拟刷新。"
        return {
            "allowed": False,
            "reason": reason,
            "message": message,
            "contract": contract,
            "actions": [],
            "credentials": creds,
            "authorized": authorized,
            "has_credentials": has_creds,
        }
    if not has_creds:
        return {
            "allowed": False,
            "reason": "missing_credentials",
            "message": "官方契约含采集动作，但缺少凭证，未发起外部请求，改为本地模拟刷新。",
            "contract": contract,
            "actions": read_actions,
            "credentials": creds,
            "authorized": authorized,
            "has_credentials": False,
        }
    if not authorized:
        return {
            "allowed": False,
            "reason": "channel_unauthorized",
            "message": "官方契约含采集动作，但渠道未授权，未发起外部请求，改为本地模拟刷新。",
            "contract": contract,
            "actions": read_actions,
            "credentials": creds,
            "authorized": False,
            "has_credentials": True,
        }
    return {
        "allowed": True,
        "reason": "",
        "message": "将按官方契约中的采集动作发起请求。",
        "contract": contract,
        "actions": read_actions,
        "credentials": creds,
        "authorized": True,
        "has_credentials": True,
    }


def _window() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=14), today + timedelta(days=14)


def _fingerprint_metrics(rows: list[DailyMetric]) -> list[tuple]:
    return [
        (
            row.id,
            row.date.isoformat() if row.date else None,
            row.room_type_id,
            row.platform,
            row.occupancy,
            row.list_price,
            row.adr,
            row.revenue,
            row.search_rank,
            row.impressions,
            row.clicks,
            row.bookings,
            row.conversion_rate,
            row.sold_rooms,
            row.available_rooms,
        )
        for row in rows
    ]


def mock_refresh(db: Session, hotel: Hotel) -> dict[str, Any]:
    start, end = _window()
    adapter = LocalRefreshAdapter(hotel.platform or "ctrip")
    before = _fingerprint_metrics(
        db.query(DailyMetric).filter(DailyMetric.hotel_id == hotel.id).order_by(DailyMetric.id).all()
    )
    metrics = adapter.fetch_metrics(db, hotel.id, start, end)
    competitors = adapter.fetch_competitors(db, hotel.id, start, end)
    reviews = adapter.fetch_reviews(db, hotel.id)
    after = _fingerprint_metrics(
        db.query(DailyMetric).filter(DailyMetric.hotel_id == hotel.id).order_by(DailyMetric.id).all()
    )
    now = datetime.utcnow().isoformat() + "Z"
    return {
        "source": "mock",
        "official_http": False,
        "mutated": before != after,
        "strategy_executed": False,
        "auto_approved": False,
        "last_ingested_at": now,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "counts": {
            "metrics": len(metrics),
            "competitors": len(competitors),
            "reviews": len(reviews),
        },
        "platform": adapter.platform,
    }


def _safe_host(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if not parsed.hostname:
        return ""
    return parsed.hostname


def _official_ingest(db: Session, hotel: Hotel, context: dict[str, Any]) -> dict[str, Any]:
    """Call official read actions from the contract. Do not invent endpoints or persist unmapped facts."""
    start, end = _window()
    client = OfficialHttpClient(hotel.platform or "ctrip", context["contract"], context["credentials"])
    calls: list[dict[str, Any]] = []
    failed = False
    error = ""
    retryable = False
    for action in context["actions"]:
        payload = {
            "hotel_id": hotel.id,
            "platform_hotel_id": (context["credentials"] or {}).get("platform_hotel_id") or "",
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }
        try:
            receipt = client.send_read(action, payload)
        except OfficialHttpError as exc:
            failed = True
            error = exc.message
            retryable = bool(exc.retryable)
            calls.append(
                {
                    "action": action,
                    "ok": False,
                    "retryable": retryable,
                    "http_status": exc.status_code,
                    "message": exc.message,
                }
            )
            break
        calls.append(
            {
                "action": action,
                "ok": bool(receipt.get("ok")),
                "http_status": receipt.get("http_status"),
                "host": receipt.get("host") or "",
                "item_count": len(receipt.get("items") or []),
                "persisted": False,
                "message": receipt.get("message") or "",
            }
        )
        # No automatic metric upsert: this increment does not invent a mapping
        # from partner payloads into DailyMetric. Persistence is recorded as skipped.
    now = datetime.utcnow().isoformat() + "Z"
    return {
        "source": "official",
        "official_http": True,
        "mutated": False,
        "persisted": False,
        "persist_reason": "no_ingest_mapping",
        "strategy_executed": False,
        "auto_approved": False,
        "last_ingested_at": now,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "counts": {"official_actions": len(calls), "official_items": sum(c.get("item_count") or 0 for c in calls)},
        "calls": calls,
        "ok": not failed and all(c.get("ok") for c in calls),
        "error": error,
        "retryable": retryable,
        "platform": hotel.platform or "ctrip",
    }


def snapshot_strategies(db: Session, hotel_id: int) -> list[tuple[int, str]]:
    rows = db.query(Strategy).filter(Strategy.hotel_id == hotel_id).order_by(Strategy.id).all()
    return [(row.id, row.status) for row in rows]


def run_ingest(db: Session, hotel: Hotel) -> dict[str, Any]:
    """Execute ingest for one hotel and return an auditable summary."""
    before_strategies = snapshot_strategies(db, hotel.id)
    platform = (hotel.platform or "ctrip").strip().lower() or "ctrip"
    official = resolve_official_ingest(db, hotel, platform)
    if official["allowed"]:
        result = _official_ingest(db, hotel, official)
        if not result.get("ok"):
            result["fallback"] = None
            result["status"] = "failed"
            result["pms_ingest"] = run_pms_ingest(db, hotel.id)
            return result
        result["status"] = "succeeded"
        result["reason"] = ""
        after_strategies = snapshot_strategies(db, hotel.id)
        result["strategy_executed"] = before_strategies != after_strategies
        result["pms_ingest"] = run_pms_ingest(db, hotel.id)
        return result

    result = mock_refresh(db, hotel)
    result["status"] = "succeeded"
    result["reason"] = official["reason"]
    result["message"] = official["message"]
    after_strategies = snapshot_strategies(db, hotel.id)
    result["strategy_executed"] = before_strategies != after_strategies
    result["pms_ingest"] = run_pms_ingest(db, hotel.id)
    return result
