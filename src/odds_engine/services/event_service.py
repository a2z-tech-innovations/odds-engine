"""Event service — query and cache-aware event retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING

from odds_engine.exceptions import EventNotFoundError
from odds_engine.logging import get_logger
from odds_engine.schemas.events import EventFilterParams, EventListResponse, EventResponse

if TYPE_CHECKING:
    from odds_engine.repositories.cache_repo import CacheRepository
    from odds_engine.repositories.event_repo import EventRepository

logger = get_logger(__name__)


class EventService:
    def __init__(self, repo: EventRepository, cache: CacheRepository) -> None:
        self._repo = repo
        self._cache = cache

    async def get_events(self, filters: EventFilterParams) -> EventListResponse:
        """Return a list of events, preferring the cache when sport_group is specified.

        1. If filters.sport_group is set, try cache.get_active_events(sport_group).
        2. On cache hit: map EnrichedEventResponse → EventResponse.
        3. On miss: query repo.get_many(filters) + repo.count(filters).
        4. Return EventListResponse.
        """
        if filters.sport_group is not None:
            cached = await self._cache.get_active_events(filters.sport_group)
            if cached is not None:
                logger.debug(
                    "events cache hit",
                    sport_group=filters.sport_group,
                    count=len(cached),
                )
                events = [
                    EventResponse(
                        id=e.snapshot_id,  # closest available UUID in enriched schema
                        external_id=e.event_id,
                        sport_key=e.sport_key,
                        sport_group=e.sport_group,
                        home_team=e.home_team,
                        away_team=e.away_team,
                        commence_time=e.commence_time,
                        status=e.status,  # type: ignore[arg-type]
                        created_at=e.fetched_at,
                        updated_at=e.fetched_at,
                    )
                    for e in cached
                ]
                return EventListResponse(events=events, total=len(events))

        db_events = await self._repo.get_many(filters)
        total = await self._repo.count(filters)
        logger.debug("events db query", total=total)
        return EventListResponse(
            events=[EventResponse.model_validate(e) for e in db_events],
            total=total,
        )

    async def get_event(self, event_id: str) -> EventResponse:
        """Return a single event by its external_id (Odds API ID string).

        1. Try cache.get_event(event_id).
        2. On miss: repo.get_by_external_id(event_id).
        3. Not found: raise EventNotFoundError(event_id).
        4. Return EventResponse.model_validate(db_event).
        """
        cached = await self._cache.get_event(event_id)
        if cached is not None:
            logger.debug("event cache hit", event_id=event_id)
            return EventResponse(
                id=cached.snapshot_id,
                external_id=cached.event_id,
                sport_key=cached.sport_key,
                sport_group=cached.sport_group,
                home_team=cached.home_team,
                away_team=cached.away_team,
                commence_time=cached.commence_time,
                status=cached.status,  # type: ignore[arg-type]
                created_at=cached.fetched_at,
                updated_at=cached.fetched_at,
            )

        db_event = await self._repo.get_by_external_id(event_id)
        if db_event is None:
            raise EventNotFoundError(event_id)

        logger.debug("event db hit", event_id=event_id)
        return EventResponse.model_validate(db_event)
