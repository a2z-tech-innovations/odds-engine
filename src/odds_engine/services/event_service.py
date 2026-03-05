"""Event service — query and cache-aware event retrieval."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from odds_engine.exceptions import EventNotFoundError
from odds_engine.logging import get_logger
from odds_engine.schemas.enriched import EnrichedEventResponse
from odds_engine.schemas.events import EventFilterParams

if TYPE_CHECKING:
    from odds_engine.models.odds import EnrichedSnapshot
    from odds_engine.repositories.cache_repo import CacheRepository
    from odds_engine.repositories.event_repo import EventRepository
    from odds_engine.repositories.odds_repo import OddsRepository

logger = get_logger(__name__)


def _db_event_to_enriched(event, enriched: EnrichedSnapshot | None = None) -> EnrichedEventResponse:
    """Build an EnrichedEventResponse from an ORM Event.

    If an EnrichedSnapshot is provided, populates best_line/consensus/vig_free/movement
    from the DB. bookmakers is always empty in the DB fallback path (not reconstructed
    from raw bookmaker_odds rows).
    """
    return EnrichedEventResponse(
        event_id=event.external_id,
        sport_key=event.sport_key,
        sport_group=event.sport_group,
        home_team=event.home_team,
        away_team=event.away_team,
        commence_time=event.commence_time,
        status=event.status.value if hasattr(event.status, "value") else str(event.status),
        snapshot_id=enriched.snapshot_id if enriched else uuid.uuid4(),
        fetched_at=enriched.computed_at if enriched else event.updated_at,
        bookmakers=enriched.bookmakers if enriched else {},
        best_line=enriched.best_line if enriched else {},
        opening_line=event.opening_line if hasattr(event, "opening_line") else {},
        consensus=enriched.consensus_line if enriched else {},
        vig_free=enriched.vig_free if enriched else {},
        movement=enriched.movement if enriched else {},
    )


class EventService:
    def __init__(
        self, repo: EventRepository, cache: CacheRepository, odds_repo: OddsRepository
    ) -> None:
        self._repo = repo
        self._cache = cache
        self._odds_repo = odds_repo

    async def get_events(self, filters: EventFilterParams) -> list[EnrichedEventResponse]:
        """Return enriched events, preferring cache when sport_group is specified.

        Cache path (sport_group set): returns full EnrichedEventResponse with all odds data.
        DB fallback: joins enriched_snapshots to populate best_line/consensus/vig_free/movement.
        Additional filters (sport_key, status, etc.) are applied client-side on cache results.
        """
        if filters.sport_group is not None:
            cached = await self._cache.get_active_events(filters.sport_group)
            if cached:
                logger.debug(
                    "events cache hit",
                    sport_group=filters.sport_group,
                    count=len(cached),
                )
                events = cached
                if filters.sport_key:
                    events = [e for e in events if e.sport_key == filters.sport_key]
                if filters.status:
                    events = [e for e in events if e.status == filters.status.value]
                if filters.commence_from:
                    events = [e for e in events if e.commence_time >= filters.commence_from]
                if filters.commence_to:
                    events = [e for e in events if e.commence_time <= filters.commence_to]
                return events

        db_events = await self._repo.get_many(filters)
        logger.debug("events db query", count=len(db_events))
        enriched_map = await self._odds_repo.get_latest_enriched_bulk(
            [e.id for e in db_events]
        )
        return [_db_event_to_enriched(e, enriched_map.get(e.id)) for e in db_events]

    async def get_event(self, event_id: str) -> EnrichedEventResponse:
        """Return a single enriched event by its external_id (Odds API ID string).

        Cache path: returns full EnrichedEventResponse with all odds data.
        DB fallback: joins enriched_snapshots to populate best_line/consensus/vig_free/movement.
        Raises EventNotFoundError if not found in cache or DB.
        """
        cached = await self._cache.get_event(event_id)
        if cached is not None:
            logger.debug("event cache hit", event_id=event_id)
            return cached

        db_event = await self._repo.get_by_external_id(event_id)
        if db_event is None:
            raise EventNotFoundError(event_id)

        enriched = await self._odds_repo.get_latest_enriched(db_event.id)
        logger.debug("event db hit", event_id=event_id)
        return _db_event_to_enriched(db_event, enriched)
