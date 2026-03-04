"""Pydantic schemas for parsing raw Odds API v4 responses."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OddsAPIOutcome(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    price: float
    point: float | None = None


class OddsAPIMarket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    last_update: datetime | None = None
    outcomes: list[OddsAPIOutcome]


class OddsAPIBookmaker(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    title: str
    last_update: datetime | None = None
    markets: list[OddsAPIMarket]


class OddsAPIEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    sport_key: str
    sport_title: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmakers: list[OddsAPIBookmaker] = []


class OddsAPISport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    group: str
    title: str
    description: str
    active: bool
    has_outrights: bool
