from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.ota.base import OTAAdapter
from app.models import CompetitorSnapshot, DailyMetric, Review


class CtripMockAdapter(OTAAdapter):
    platform = "ctrip"

    def fetch_metrics(self, db: Session, hotel_id: int, start: date, end: date) -> list[DailyMetric]:
        return (
            db.query(DailyMetric)
            .filter(
                DailyMetric.hotel_id == hotel_id,
                DailyMetric.date >= start,
                DailyMetric.date <= end,
                DailyMetric.platform == self.platform,
            )
            .order_by(DailyMetric.date, DailyMetric.room_type_id)
            .all()
        )

    def fetch_competitors(
        self, db: Session, hotel_id: int, start: date, end: date
    ) -> list[CompetitorSnapshot]:
        return (
            db.query(CompetitorSnapshot)
            .filter(
                CompetitorSnapshot.hotel_id == hotel_id,
                CompetitorSnapshot.date >= start,
                CompetitorSnapshot.date <= end,
            )
            .order_by(CompetitorSnapshot.date, CompetitorSnapshot.competitor_name)
            .all()
        )

    def fetch_reviews(self, db: Session, hotel_id: int) -> list[Review]:
        return (
            db.query(Review)
            .filter(Review.hotel_id == hotel_id, Review.platform == self.platform)
            .order_by(Review.review_date.desc())
            .all()
        )

    def apply_price(self, payload: dict, *, write_mode: str = "sandbox") -> dict:
        from app.adapters.ota.official_write import OfficialWriteAdapter

        return OfficialWriteAdapter(platform=self.platform).apply_price(payload, write_mode=write_mode)

    def post_review_reply(self, payload: dict, *, write_mode: str = "sandbox") -> dict:
        from app.adapters.ota.official_write import OfficialWriteAdapter

        return OfficialWriteAdapter(platform=self.platform).post_review_reply(payload, write_mode=write_mode)

    def apply_campaign(self, payload: dict, *, write_mode: str = "sandbox") -> dict:
        from app.adapters.ota.official_write import OfficialWriteAdapter

        return OfficialWriteAdapter(platform=self.platform).apply_campaign(payload, write_mode=write_mode)
