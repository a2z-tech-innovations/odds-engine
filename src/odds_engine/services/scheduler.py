"""Budget-aware polling scheduler logic.

The actual APScheduler job wiring lives in main.py (Phase 4).
This module provides the core fetch-decision logic as pure, testable classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from odds_engine.exceptions import BudgetExhaustedError, OddsAPIError

if TYPE_CHECKING:
    from odds_engine.clients.odds_api import OddsAPIClient
    from odds_engine.config import Settings
    from odds_engine.repositories.cache_repo import CacheRepository

log = structlog.get_logger(__name__)

# Sport key prefixes / exact keys we care about
_TARGET_PREFIXES = ("tennis_atp_", "tennis_wta_", "golf_")
_TARGET_EXACT = frozenset({"basketball_ncaab"})


def _is_target_sport(key: str) -> bool:
    """Return True if *key* belongs to one of our target sports."""
    if key in _TARGET_EXACT:
        return True
    return any(key.startswith(prefix) for prefix in _TARGET_PREFIXES)


class BudgetManager:
    """Tracks and enforces credit budget limits."""

    def __init__(self, settings: Settings, cache: CacheRepository) -> None:
        self._settings = settings
        self._cache = cache

    async def get_daily_used(self) -> int:
        """Current daily credit spend from Redis."""
        budget = await self._cache.get_budget()
        return budget["daily_used"]

    async def get_monthly_used(self) -> int:
        """Current monthly credit spend from Redis."""
        budget = await self._cache.get_budget()
        return budget["monthly_used"]

    async def record_usage(self, credits: int) -> None:
        """Increment both daily and monthly counters."""
        await self._cache.increment_daily_budget(credits)
        await self._cache.increment_monthly_budget(credits)
        log.info("budget.recorded", credits=credits)

    async def check_budget(self, estimated_cost: int = 3) -> None:
        """Raise BudgetExhaustedError if proceeding would exceed limits.

        Checks both daily_credit_target and monthly_credit_limit.
        """
        daily_used = await self.get_daily_used()
        monthly_used = await self.get_monthly_used()

        if daily_used + estimated_cost > self._settings.daily_credit_target:
            raise BudgetExhaustedError(
                f"Daily budget would be exceeded: used={daily_used}, "
                f"cost={estimated_cost}, target={self._settings.daily_credit_target}"
            )

        if monthly_used + estimated_cost > self._settings.monthly_credit_limit:
            raise BudgetExhaustedError(
                f"Monthly budget would be exceeded: used={monthly_used}, "
                f"cost={estimated_cost}, limit={self._settings.monthly_credit_limit}"
            )

    async def is_budget_available(self, estimated_cost: int = 3) -> bool:
        """Returns False instead of raising — useful for scheduler decisions."""
        try:
            await self.check_budget(estimated_cost)
            return True
        except BudgetExhaustedError:
            return False


class SportDiscovery:
    """Discovers active sport keys from The Odds API."""

    def __init__(self, client: OddsAPIClient, cache: CacheRepository) -> None:
        self._client = client
        self._cache = cache

    async def get_active_sport_keys(self) -> list[str]:
        """Return active sport keys for our target sports.

        1. Check Redis cache (sports:active key, TTL 1 hour)
        2. On miss: call client.get_sports(active_only=True)
        3. Filter to tennis_atp_*, tennis_wta_*, basketball_ncaab
        4. Cache the result
        5. Return list of sport keys
        """
        cached = await self._cache.get_active_sports()
        if cached is not None:
            log.debug("sport_discovery.cache_hit", count=len(cached))
            return [s.key for s in cached if _is_target_sport(s.key)]

        log.debug("sport_discovery.cache_miss")
        all_sports = await self._client.get_sports(active_only=True)
        await self._cache.set_active_sports(all_sports)

        target = [s.key for s in all_sports if _is_target_sport(s.key)]
        log.info("sport_discovery.fetched", total=len(all_sports), target=len(target))
        return target

    async def get_active_events_count(self, sport_key: str) -> int:
        """Call client.get_events(sport_key) and return the count.

        Returns 0 on OddsAPIError (graceful degradation).
        Free endpoint — no budget impact.
        """
        try:
            events = await self._client.get_events(sport_key)
            return len(events)
        except OddsAPIError as exc:
            log.warning("sport_discovery.events_error", sport_key=sport_key, error=str(exc))
            return 0


class FetchScheduler:
    """Decides what to fetch and when, respecting the budget.

    The actual scheduling (APScheduler jobs) is wired in main.py.
    This class provides the core fetch-decision logic.
    """

    def __init__(
        self,
        settings: Settings,
        budget_manager: BudgetManager,
        sport_discovery: SportDiscovery,
    ) -> None:
        self._settings = settings
        self._budget = budget_manager
        self._discovery = sport_discovery

    async def get_sports_to_fetch(self) -> list[str]:
        """Return the list of sport keys that should be fetched right now.

        Logic:
        1. Get active sport keys via sport_discovery
        2. For each sport key, check events count (skip if 0)
        3. Check budget — if exhausted, return []
        4. Return list of sport keys with events
        """
        sport_keys = await self._discovery.get_active_sport_keys()

        sports_with_events: list[str] = []
        for key in sport_keys:
            count = await self._discovery.get_active_events_count(key)
            if count > 0:
                sports_with_events.append(key)
            else:
                log.debug("scheduler.skip_no_events", sport_key=key)

        if not sports_with_events:
            return []

        estimated = self.estimate_cost(sports_with_events)
        if not await self._budget.is_budget_available(estimated):
            log.warning(
                "scheduler.budget_exhausted",
                estimated_cost=estimated,
                sport_keys=sports_with_events,
            )
            return []

        return sports_with_events

    async def should_fetch_sport(self, sport_key: str) -> bool:
        """Return True if this sport should be fetched in this cycle.

        - If budget unavailable: False
        - If no active events: False
        - Otherwise: True
        """
        if not await self._budget.is_budget_available():
            return False

        count = await self._discovery.get_active_events_count(sport_key)
        return count > 0

    def estimate_cost(self, sport_keys: list[str], markets: int = 3) -> int:
        """Estimate total credit cost for fetching a list of sport keys.

        Cost = len(sport_keys) * markets (default 3: h2h, spreads, totals)
        """
        return len(sport_keys) * markets
