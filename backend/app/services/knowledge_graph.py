"""In-hotel knowledge graph built from already recorded facts (Phase IV increment 4).

Strictly scoped by hotel_id. Nodes and edges never include another hotel's
name, id, prices, or operators. Visible anonymized industry patterns may be
linked by pattern_key only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import GraphEdge, GraphNode, MemoryRecord, RoomType
from app.services.industry_patterns import (
    load_visible_patterns,
    pattern_key as make_pattern_key,
    price_direction_of,
    signal_types_of,
)
from app.services.memory import ACTION_KIND_LABELS, MEMORY_KIND, STATUS_LABELS

NODE_TYPES = (
    "room_type",
    "signal_type",
    "decision_episode",
    "action_kind",
    "terminal_status",
    "observed_outcome",
    "industry_pattern",
)

EDGE_TYPES = (
    "triggered_by",
    "for_room_type",
    "has_status",
    "has_action",
    "observed_outcome",
    "similar_to",
)

SIGNAL_LABELS = {
    "rank_drop": "排名下跌",
    "price_inversion": "价格倒挂",
    "conversion_drop": "转化走弱",
    "competitor_cut": "竞对降价",
    "negative_review": "差评信号",
}

GRAPH_NOTE = (
    "本店知识图谱只包含当前酒店已沉淀的决策、信号、房型与可见的匿名行业模式。"
    "不包含其他酒店的名称、身份或可识别数据。"
)


def _node_key(kind: str, ident: str | int) -> str:
    return f"{kind}:{ident}"


def upsert_node(
    db: Session,
    hotel_id: int,
    node_key: str,
    node_type: str,
    label: str,
    attributes: dict | None = None,
) -> GraphNode:
    row = (
        db.query(GraphNode)
        .filter(GraphNode.hotel_id == int(hotel_id), GraphNode.node_key == node_key)
        .one_or_none()
    )
    attrs = dict(attributes or {})
    if row is None:
        for pending in list(db.new):
            if isinstance(pending, GraphNode) and pending.hotel_id == int(hotel_id) and pending.node_key == node_key:
                row = pending
                break
    if row is None:
        row = GraphNode(
            hotel_id=int(hotel_id),
            node_key=node_key,
            node_type=node_type,
            label=label,
            attributes=attrs,
        )
        db.add(row)
    else:
        row.node_type = node_type
        row.label = label
        row.attributes = attrs
    db.flush()
    return row


def upsert_edge(
    db: Session,
    hotel_id: int,
    from_key: str,
    to_key: str,
    edge_type: str,
    attributes: dict | None = None,
) -> GraphEdge:
    row = (
        db.query(GraphEdge)
        .filter(
            GraphEdge.hotel_id == int(hotel_id),
            GraphEdge.from_key == from_key,
            GraphEdge.to_key == to_key,
            GraphEdge.edge_type == edge_type,
        )
        .one_or_none()
    )
    attrs = dict(attributes or {})
    if row is None:
        for pending in list(db.new):
            if (
                isinstance(pending, GraphEdge)
                and pending.hotel_id == int(hotel_id)
                and pending.from_key == from_key
                and pending.to_key == to_key
                and pending.edge_type == edge_type
            ):
                row = pending
                break
    if row is None:
        row = GraphEdge(
            hotel_id=int(hotel_id),
            from_key=from_key,
            to_key=to_key,
            edge_type=edge_type,
            attributes=attrs,
        )
        db.add(row)
    else:
        row.attributes = attrs
    db.flush()
    return row


def _visible_pattern_keys(db: Session, hotel_id: int) -> dict[str, dict[str, Any]]:
    patterns, _total = load_visible_patterns(db, hotel_id, limit=200, offset=0)
    return {str(item.get("pattern_key") or ""): item for item in patterns if item.get("pattern_key")}


def _episode_pattern_key(row: MemoryRecord) -> str | None:
    payload = row.payload or {}
    flags = signal_types_of(None, payload)
    direction = price_direction_of(None, payload)
    from app.services.industry_patterns import pattern_features

    features = pattern_features(
        action_kind=row.action_kind or payload.get("action_kind") or "",
        signal_types=flags,
        risk_level=row.risk_level or payload.get("risk_level") or "",
        terminal_status=row.approval_status or payload.get("status") or "",
        price_direction=direction,
    )
    key = make_pattern_key(features)
    return key or None


def upsert_episode_graph(db: Session, row: MemoryRecord, *, commit: bool = True) -> None:
    """Idempotently attach one in-hotel episode to the hotel graph."""
    if row is None or not row.hotel_id:
        return
    if (row.kind or MEMORY_KIND) != MEMORY_KIND:
        return
    hotel_id = int(row.hotel_id)
    payload = row.payload or {}
    episode_key = _node_key("episode", row.id)
    kind = row.action_kind or payload.get("action_kind") or ""
    status = row.approval_status or payload.get("status") or ""
    outcome = payload.get("outcome") or "not_measured"
    summary = (payload.get("room_name") or "") + (f" · {status}" if status else "")
    upsert_node(
        db,
        hotel_id,
        episode_key,
        "decision_episode",
        label=f"决策片段 #{row.id}",
        attributes={
            "memory_id": row.id,
            "action_kind": kind,
            "approval_status": status,
            "outcome": outcome,
            "room_name": payload.get("room_name") or "",
            "date_from": payload.get("date_from"),
            "date_to": payload.get("date_to"),
            "risk_level": row.risk_level or payload.get("risk_level") or "",
        },
    )
    if kind:
        action_key = _node_key("action", kind)
        upsert_node(
            db,
            hotel_id,
            action_key,
            "action_kind",
            label=ACTION_KIND_LABELS.get(kind, kind),
            attributes={"action_kind": kind},
        )
        upsert_edge(db, hotel_id, episode_key, action_key, "has_action")
    if status:
        status_key = _node_key("status", status)
        upsert_node(
            db,
            hotel_id,
            status_key,
            "terminal_status",
            label=STATUS_LABELS.get(status, status),
            attributes={"terminal_status": status},
        )
        upsert_edge(db, hotel_id, episode_key, status_key, "has_status")

    room_type_id = payload.get("room_type_id") or (payload.get("proposed_action") or {}).get("room_type_id")
    if room_type_id:
        room = db.get(RoomType, int(room_type_id))
        if room is not None and room.hotel_id == hotel_id:
            room_key = _node_key("room", room.id)
            upsert_node(
                db,
                hotel_id,
                room_key,
                "room_type",
                label=room.name,
                attributes={"room_type_id": room.id, "code": room.code},
            )
            upsert_edge(db, hotel_id, episode_key, room_key, "for_room_type")

    for flag in signal_types_of(None, payload):
        sig_key = _node_key("signal", flag)
        upsert_node(
            db,
            hotel_id,
            sig_key,
            "signal_type",
            label=SIGNAL_LABELS.get(flag, flag),
            attributes={"signal_type": flag},
        )
        upsert_edge(db, hotel_id, episode_key, sig_key, "triggered_by")

    if outcome in {"measured", "partially_measured"} and isinstance(payload.get("impact"), dict):
        impact = payload["impact"]
        out_key = _node_key("outcome", row.id)
        upsert_node(
            db,
            hotel_id,
            out_key,
            "observed_outcome",
            label=impact.get("title") or "观察结果",
            attributes={
                "outcome": outcome,
                "observation_kind": impact.get("observation_kind") or "",
                "terminal_status": impact.get("terminal_status") or status,
                "has_realized": bool(impact.get("realized")),
                "has_otb": bool(impact.get("otb_pickup")),
            },
        )
        upsert_edge(db, hotel_id, episode_key, out_key, "observed_outcome")

    visible = _visible_pattern_keys(db, hotel_id)
    pattern = _episode_pattern_key(row)
    if pattern and pattern in visible:
        pat_key = _node_key("pattern", pattern)
        item = visible[pattern]
        upsert_node(
            db,
            hotel_id,
            pat_key,
            "industry_pattern",
            label="可见匿名行业模式",
            attributes={
                "pattern_key": pattern,
                "action_kind": item.get("action_kind") or "",
                "terminal_status": item.get("terminal_status") or "",
                "peer_hotel_count": item.get("peer_hotel_count"),
                "peer_episode_count": item.get("peer_episode_count"),
            },
        )
        upsert_edge(db, hotel_id, episode_key, pat_key, "similar_to", {"pattern_key": pattern})

    if commit:
        db.commit()


def rebuild_hotel_graph(db: Session, hotel_id: int) -> dict[str, Any]:
    """Rebuild one hotel's graph from its current episodes. Idempotent."""
    hotel_id = int(hotel_id)
    db.query(GraphEdge).filter(GraphEdge.hotel_id == hotel_id).delete()
    db.query(GraphNode).filter(GraphNode.hotel_id == hotel_id).delete()
    db.flush()
    rows = (
        db.query(MemoryRecord)
        .filter(MemoryRecord.hotel_id == hotel_id, MemoryRecord.kind == MEMORY_KIND)
        .order_by(MemoryRecord.id.asc())
        .all()
    )
    for row in rows:
        upsert_episode_graph(db, row, commit=False)
    db.commit()
    return serialize_graph(db, hotel_id)


def _readable_summary(nodes: list[dict], edges: list[dict]) -> str:
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node["node_type"]] = counts.get(node["node_type"], 0) + 1
    bits = [f"共 {len(nodes)} 个节点、{len(edges)} 条关系"]
    if counts.get("decision_episode"):
        bits.append(f"决策片段 {counts['decision_episode']} 条")
    if counts.get("industry_pattern"):
        bits.append(f"可见匿名模式 {counts['industry_pattern']} 个")
    return "；".join(bits) + "。仅当前酒店，不含其他酒店身份。"


def serialize_graph(db: Session, hotel_id: int) -> dict[str, Any]:
    hotel_id = int(hotel_id)
    nodes = (
        db.query(GraphNode)
        .filter(GraphNode.hotel_id == hotel_id)
        .order_by(GraphNode.node_type.asc(), GraphNode.id.asc())
        .all()
    )
    edges = (
        db.query(GraphEdge)
        .filter(GraphEdge.hotel_id == hotel_id)
        .order_by(GraphEdge.edge_type.asc(), GraphEdge.id.asc())
        .all()
    )
    node_payload = [
        {
            "key": n.node_key,
            "node_type": n.node_type,
            "label": n.label,
            "attributes": n.attributes or {},
        }
        for n in nodes
    ]
    edge_payload = [
        {
            "from": e.from_key,
            "to": e.to_key,
            "edge_type": e.edge_type,
            "attributes": e.attributes or {},
        }
        for e in edges
    ]
    return {
        "hotel_id": hotel_id,
        "nodes": node_payload,
        "edges": edge_payload,
        "summary": _readable_summary(node_payload, edge_payload),
        "note": GRAPH_NOTE,
    }
