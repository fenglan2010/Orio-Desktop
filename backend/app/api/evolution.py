"""L5 evolution APIs: knowledge graph, offline RL, and the in-hotel engine."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.models import Hotel, User
from app.services.evolution import (
    invent_strategies,
    list_candidates,
    overview,
    serialize_engine,
    set_engine_enabled,
    set_sandbox_mode,
)
from app.services.knowledge_graph import rebuild_hotel_graph, serialize_graph
from app.services.permissions import require, require_permission
from app.services.rl import serialize_policy

router = APIRouter(prefix="/api/evolution", tags=["evolution"], dependencies=[Depends(require("view_l5"))])


class EngineToggle(BaseModel):
    enabled: bool


class SandboxToggle(BaseModel):
    sandbox_mode: bool


@router.get("")
def get_overview(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    return overview(db, hotel.id)


@router.get("/engine")
def get_engine(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    return serialize_engine(db, hotel.id)


@router.post("/engine")
def toggle_engine(
    body: EngineToggle,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    require_permission(user, "update_policy")
    return set_engine_enabled(db, hotel.id, body.enabled, actor=user.username)


@router.post("/sandbox")
def toggle_sandbox(
    body: SandboxToggle,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    require_permission(user, "update_policy")
    try:
        engine = set_sandbox_mode(db, hotel.id, body.sandbox_mode, actor=user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    candidates = list_candidates(db, hotel.id) if engine["sandbox_mode"] else list_candidates(db, hotel.id)
    return {"engine": engine, "candidates": candidates}


@router.get("/graph")
def get_graph(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    payload = serialize_graph(db, hotel.id)
    if not payload["nodes"]:
        payload = rebuild_hotel_graph(db, hotel.id)
    return payload


@router.get("/rl")
def get_rl(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    return serialize_policy(db, hotel.id)


@router.get("/candidates")
def get_candidates(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    return {"candidates": list_candidates(db, hotel.id)}


@router.post("/invent")
def invent(
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    require_permission(user, "generate")
    try:
        return invent_strategies(db, hotel.id, actor=user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
