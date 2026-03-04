"""Pydantic schemas for normalized odds data."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OutcomeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    price: float
    point: float | None = None


class MarketSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    outcomes: list[OutcomeSchema]
    last_update: datetime | None = None


class BookmakerSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    title: str
    markets: list[MarketSchema]


class OddsSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    snapshot_id: UUID
    event_id: UUID
    fetched_at: datetime
    credits_used: int | None = None
    bookmakers: list[BookmakerSchema]


class ManualFetchRequest(BaseModel):
    sport_key: str


class ManualFetchResponse(BaseModel):
    sport_key: str
    events_fetched: int
    credits_used: int
