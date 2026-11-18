"""OTA adapter contract. Reads stay on the simulator; writes use official sandbox receipts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from sqlalchemy.orm import Session


class OTAAdapter(ABC):
    platform: str

    @abstractmethod
    def fetch_metrics(self, db: Session, hotel_id: int, start: date, end: date) -> list[Any]:
        raise NotImplementedError

    @abstractmethod
    def fetch_competitors(self, db: Session, hotel_id: int, start: date, end: date) -> list[Any]:
        raise NotImplementedError

    @abstractmethod
    def fetch_reviews(self, db: Session, hotel_id: int) -> list[Any]:
        raise NotImplementedError

    @abstractmethod
    def apply_price(self, payload: dict) -> dict:
        """Write a price change. Phase II default is official-sandbox, never a live OTA call."""
        raise NotImplementedError

    @abstractmethod
    def post_review_reply(self, payload: dict) -> dict:
        raise NotImplementedError
