"""Consumer-ready denormalized schema for Redis, WebSocket, and REST API responses."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, RootModel

from odds_engine.schemas.odds import OutcomeSchema


class BestLineOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    price: float
    bookmaker: str


class BestLineMarket(RootModel[dict[str, BestLineOutcome]]):
    pass


class ConsensusOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    price: float


class VigFreeOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    implied_prob: float


class MovementOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    price_delta: float
    point_delta: float | None = None
    previous_price: float | None = None
    previous_point: float | None = None


class EnrichedBookmakerMarket(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outcomes: list[OutcomeSchema]
    last_update: datetime | None = None


class EnrichedBookmakerOdds(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    h2h: EnrichedBookmakerMarket | None = None
    spreads: EnrichedBookmakerMarket | None = None
    totals: EnrichedBookmakerMarket | None = None


class EnrichedEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    sport_key: str
    sport_group: str
    home_team: str
    away_team: str
    commence_time: datetime
    status: str
    snapshot_id: UUID
    fetched_at: datetime
    bookmakers: dict[str, dict[str, EnrichedBookmakerMarket]]
    best_line: dict[str, dict[str, BestLineOutcome]]
    opening_line: dict[str, dict[str, BestLineOutcome]] = {}
    consensus: dict[str, dict[str, ConsensusOutcome]]
    vig_free: dict[str, dict[str, VigFreeOutcome]]
    movement: dict[str, dict[str, MovementOutcome]]
