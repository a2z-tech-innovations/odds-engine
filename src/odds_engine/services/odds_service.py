"""Odds service — main orchestration for fetch, enrich, persist, publish."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from odds_engine.exceptions import BudgetExhaustedError  # noqa: F401 — re-exported for DI callers
from odds_engine.logging import get_logger
from odds_engine.schemas.odds import ManualFetchResponse
from odds_engine.services.enrichment import build_enriched_event
from odds_engine.sport_groups import markets_for_sport

if TYPE_CHECKING:
    from odds_engine.clients.odds_api import OddsAPIClient
    from odds_engine.repositories.cache_repo import CacheRepository
    from odds_engine.repositories.event_repo import EventRepository
    from odds_engine.repositories.odds_repo import OddsRepository
    from odds_engine.schemas.enriched import EnrichedEventResponse
    from odds_engine.services.publisher import OddsPublisher

logger = get_logger(__name__)


class OddsService:
    def __init__(
        self,
        client: OddsAPIClient,
        event_repo: EventRepository,
        odds_repo: OddsRepository,
        cache: CacheRepository,
        publisher: OddsPublisher,
    ) -> None:
        self._client = client
        self._event_repo = event_repo
        self._odds_repo = odds_repo
        self._cache = cache
        self._publisher = publisher

    async def fetch_and_store(self, sport_key: str, sport_group: str) -> ManualFetchResponse:
        """Full fetch pipeline for one sport key.

        1.  client.get_odds(sport_key) → (api_events, usage)
        2.  If empty: record usage, return early.
        3.  For each api_event:
            a. event_repo.upsert_event(...) → db_event
            b. odds_repo.create_snapshot(...) → snapshot
            c. Build bookmaker_rows from api_event.bookmakers
            d. odds_repo.create_bookmaker_odds_batch(bookmaker_rows)
            e. odds_repo.get_latest_enriched(db_event.id) → previous enriched
            f. build_enriched_event(...)
            g. Serialize enriched to plain dicts via json.loads(model.model_dump_json())
            h. odds_repo.create_enriched_snapshot(...)
        4.  cache.increment_daily_budget(usage.credits_used)
        5.  cache.increment_monthly_budget(usage.credits_used)
        6.  odds_repo.record_api_usage(...)
        7.  publisher.publish_batch(enriched_events)
        8.  Return ManualFetchResponse
        """
        logger.debug("starting fetch_and_store", sport_key=sport_key)

        api_events, usage = await self._client.get_odds(sport_key, markets=markets_for_sport(sport_key))

        if not api_events:
            logger.debug("no events returned from API", sport_key=sport_key)
            await self._odds_repo.record_api_usage(
                usage.credits_used, usage.credits_remaining, "odds", sport_key
            )
            await self._cache.increment_daily_budget(usage.credits_used)
            await self._cache.increment_monthly_budget(usage.credits_used)
            return ManualFetchResponse(
                sport_key=sport_key,
                events_fetched=0,
                credits_used=usage.credits_used,
            )

        now = datetime.now(tz=UTC)
        enriched_events: list[EnrichedEventResponse] = []

        for api_event in api_events:
            # a. Upsert event
            db_event = await self._event_repo.upsert_event(
                external_id=api_event.id,
                sport_key=api_event.sport_key,
                sport_group=sport_group,
                home_team=api_event.home_team or api_event.sport_title,
                away_team=api_event.away_team or api_event.sport_title,
                commence_time=api_event.commence_time,
                status="upcoming",
            )

            # b. Create snapshot
            snapshot = await self._odds_repo.create_snapshot(
                event_id=db_event.id,
                fetched_at=now,
                credits_used=usage.credits_used,
            )

            # c. Build bookmaker rows
            bookmaker_rows: list[dict] = []
            for bookmaker in api_event.bookmakers:
                for market in bookmaker.markets:
                    for outcome in market.outcomes:
                        bookmaker_rows.append(
                            {
                                "id": uuid.uuid4(),
                                "snapshot_id": snapshot.id,
                                "bookmaker_key": bookmaker.key,
                                "market_key": market.key,
                                "outcome_name": outcome.name,
                                "outcome_price": outcome.price,
                                "outcome_point": outcome.point,
                                "last_update": market.last_update,
                            }
                        )

            # d. Bulk insert bookmaker odds
            await self._odds_repo.create_bookmaker_odds_batch(bookmaker_rows)

            # e. Get previous enriched snapshot for movement calculation
            previous_enriched = await self._odds_repo.get_latest_enriched(db_event.id)
            previous_bookmaker_odds: list[dict] | None = None
            if previous_enriched is not None:
                previous_bookmaker_odds = (
                    await self._odds_repo.get_bookmaker_odds_for_snapshot(
                        previous_enriched.snapshot_id
                    )
                )

            # f. Build enriched event
            enriched = build_enriched_event(
                event=api_event,
                snapshot_id=snapshot.id,
                sport_group=sport_group,
                status=db_event.status,
                previous_bookmaker_odds=previous_bookmaker_odds,
            )

            # g. Serialize to plain dicts (CRITICAL: use json.loads(model_dump_json()))
            enriched_plain = json.loads(enriched.model_dump_json())

            # g2. Persist opening line on first fetch (no previous enriched snapshot)
            if previous_enriched is None:
                await self._event_repo.set_opening_line(db_event.id, enriched_plain["best_line"])

            # h. Persist enriched snapshot
            await self._odds_repo.create_enriched_snapshot(
                snapshot_id=snapshot.id,
                event_id=db_event.id,
                best_line=enriched_plain["best_line"],
                consensus_line=enriched_plain["consensus"],
                vig_free=enriched_plain["vig_free"],
                movement=enriched_plain["movement"],
                bookmakers=enriched_plain["bookmakers"],
            )

            enriched_events.append(enriched)

        # 4-5. Budget tracking
        await self._cache.increment_daily_budget(usage.credits_used)
        await self._cache.increment_monthly_budget(usage.credits_used)

        # 6. Record API usage
        await self._odds_repo.record_api_usage(
            usage.credits_used, usage.credits_remaining, "odds", sport_key
        )

        # 7. Publish
        await self._publisher.publish_batch(enriched_events)

        logger.debug(
            "fetch_and_store complete",
            sport_key=sport_key,
            events_fetched=len(api_events),
            credits_used=usage.credits_used,
        )

        return ManualFetchResponse(
            sport_key=sport_key,
            events_fetched=len(api_events),
            credits_used=usage.credits_used,
        )

    async def get_best_lines(
        self,
        sport_group: str | None = None,
        market: str | None = None,
    ) -> list[dict]:
        """Return best-line data for active events, optionally filtered.

        Queries cache for active events by sport_group (or all if None).
        Extracts best_line data, optionally filtered to a specific market.

        Returns list of dicts:
        [{event_id, home_team, away_team, sport_key, best_line: {...}}, ...]
        """
        events: list[EnrichedEventResponse] = []

        if sport_group is not None:
            cached = await self._cache.get_active_events(sport_group)
            if cached:
                events = cached
        else:
            # No sport_group filter — nothing to aggregate from cache without
            # knowing all active sport groups. Return empty; callers should
            # specify sport_group or extend with a "list all" cache key.
            pass

        results: list[dict] = []
        for event in events:
            best_line = event.best_line
            if market is not None:
                best_line = {k: v for k, v in best_line.items() if k == market}
            results.append(
                {
                    "event_id": event.event_id,
                    "home_team": event.home_team,
                    "away_team": event.away_team,
                    "sport_key": event.sport_key,
                    "best_line": best_line,
                }
            )

        return results

