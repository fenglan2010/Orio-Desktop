from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.models import ChatMessage, Hotel
from app.schemas import ChatRequest
from app.services.chat import chat as run_chat

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(get_current_user)])


def _dump(m: ChatMessage) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "meta": m.meta,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/history")
def history(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.hotel_id == hotel.id)
        .order_by(ChatMessage.id)
        .all()
    )
    return {"messages": [_dump(m) for m in rows]}


@router.post("")
def send(body: ChatRequest, db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    msg = run_chat(db, hotel, body.message)
    return _dump(msg)
