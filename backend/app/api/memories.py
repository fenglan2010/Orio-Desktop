"""Hotel-scoped in-hotel memory list and anonymized industry patterns."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.database import get_db
from app.models import Hotel, MemoryRecord
from app.services.industry_patterns import API_NOTE, load_visible_patterns
from app.services.memory import MEMORY_KIND, serialize_record
from app.services.permissions import require

router = APIRouter(prefix="/api/memories", tags=["memories"], dependencies=[Depends(require("view_l5"))])


@router.get("")
def list_memories(
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = (
        db.query(MemoryRecord)
        .filter(
            MemoryRecord.hotel_id == hotel.id,
            MemoryRecord.kind == MEMORY_KIND,
        )
        .order_by(MemoryRecord.id.desc())
    )
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return {
        "memories": [serialize_record(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/patterns")
def list_industry_patterns(
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Anonymized peer patterns visible to the current hotel (k-anonymity, self-excluded counts)."""
    patterns, total = load_visible_patterns(db, hotel.id, limit=limit, offset=offset)
    return {
        "patterns": patterns,
        "total": total,
        "limit": limit,
        "offset": offset,
        "mode": "anonymous",
        "note": API_NOTE,
    }

@router.get("/{memory_id}")
def get_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
):
    """Read one in-hotel episode, including outcome and impact when present."""
    row = (
        db.query(MemoryRecord)
        .filter(
            MemoryRecord.id == int(memory_id),
            MemoryRecord.hotel_id == hotel.id,
            MemoryRecord.kind == MEMORY_KIND,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="找不到这条本店经验记录")
    return serialize_record(row)

