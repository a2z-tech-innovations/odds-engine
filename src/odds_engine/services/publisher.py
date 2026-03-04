"""Publisher service — wraps CacheRepository for the publish + cache-update flow."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from odds_engine.logging import get_logger

if TYPE_CHECKING:
    from odds_engine.repositories.cache_repo import CacheRepository
    from odds_engine.schemas.enriched import EnrichedEventResponse

logger = get_logger(__name__)


class OddsPublisher:
    def __init__(self, cache: CacheRepository) -> None:
        self._cache = cache

    async def publish(self, event: EnrichedEventResponse) -> None:
        """Push a single enriched event to the cache and pub/sub channels.

        Steps:
        1. cache.set_event(event)           — update single-event cache
        2. cache.publish_odds_update(event) — push to Redis pub/sub channels
        """
        await self._cache.set_event(event)
        await self._cache.publish_odds_update(event)
        logger.debug(
            "published odds update",
            sport_key=event.sport_key,
            event_id=event.event_id,
        )

    async def publish_batch(self, events: list[EnrichedEventResponse]) -> None:
        """Publish all events then update the active-events list cache per sport_group.

        Steps:
        1. For each event: await self.publish(event)
        2. Group events by sport_group
        3. For each sport_group: cache.set_active_events(sport_group, group_events)
        """
        for event in events:
            await self.publish(event)

        by_sport_group: dict[str, list[EnrichedEventResponse]] = defaultdict(list)
        for event in events:
            by_sport_group[event.sport_group].append(event)

        for sport_group, group_events in by_sport_group.items():
            await self._cache.set_active_events(sport_group, group_events)

        logger.debug("published batch of odds updates", count=len(events))
