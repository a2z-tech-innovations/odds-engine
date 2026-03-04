"""Pydantic schemas for the events API layer."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from odds_engine.models.enums import EventStatus


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    external_id: str
    sport_key: str
    sport_group: str
    home_team: str
    away_team: str
    commence_time: datetime
    status: EventStatus
    created_at: datetime
    updated_at: datetime


class EventListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    events: list[EventResponse]
    total: int


class EventFilterParams(BaseModel):
    sport_group: str | None = None
    sport_key: str | None = None
    status: EventStatus | None = None
    commence_from: datetime | None = None
    commence_to: datetime | None = None
