"""Unit tests for scheduler.py — BudgetManager, SportDiscovery, FetchScheduler."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from odds_engine.config import Settings
from odds_engine.exceptions import BudgetExhaustedError, OddsAPIError
from odds_engine.schemas.odds_api import OddsAPISport
from odds_engine.services.scheduler import BudgetManager, FetchScheduler, SportDiscovery

FIXTURES = Path(__file__).parent.parent / "fixtures" / "odds_api"


def make_settings(**overrides) -> Settings:
    defaults = dict(
        odds_api_key="test",
        basestar_address="localhost",
        db_name="test",
        db_user="test",
        db_password="test",
        cache_password="test",
        api_secret_key="test",
        monthly_credit_limit=500,
        daily_credit_target=16,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_sport(key: str, group: str = "Tennis") -> OddsAPISport:
    return OddsAPISport(
        key=key,
        group=group,
        title=key,
        description=key,
        active=True,
        has_outrights=False,
    )


# ---------------------------------------------------------------------------
# BudgetManager
# ---------------------------------------------------------------------------


class TestBudgetManager:
    def _make_cache(self, daily_used: int = 0, monthly_used: int = 0) -> MagicMock:
        cache = MagicMock()
        cache.get_budget = AsyncMock(
            return_value={"daily_used": daily_used, "monthly_used": monthly_used}
        )
        cache.increment_daily_budget = AsyncMock(return_value=daily_used)
        cache.increment_monthly_budget = AsyncMock(return_value=monthly_used)
        return cache

    @pytest.mark.asyncio
    async def test_check_budget_raises_when_daily_exceeded(self):
        """daily_used=15, cost=3 → 15+3=18 > 16 → BudgetExhaustedError."""
        cache = self._make_cache(daily_used=15, monthly_used=0)
        manager = BudgetManager(make_settings(daily_credit_target=16), cache)

        with pytest.raises(BudgetExhaustedError):
            await manager.check_budget(estimated_cost=3)

    @pytest.mark.asyncio
    async def test_check_budget_raises_when_monthly_exceeded(self):
        """monthly_used=498, cost=3 → 498+3=501 > 500 → BudgetExhaustedError."""
        cache = self._make_cache(daily_used=0, monthly_used=498)
        manager = BudgetManager(make_settings(monthly_credit_limit=500), cache)

        with pytest.raises(BudgetExhaustedError):
            await manager.check_budget(estimated_cost=3)

    @pytest.mark.asyncio
    async def test_check_budget_passes_when_within_limits(self):
        """daily=10, monthly=100, cost=3 → no exception."""
        cache = self._make_cache(daily_used=10, monthly_used=100)
        manager = BudgetManager(
            make_settings(daily_credit_target=16, monthly_credit_limit=500), cache
        )

        # Should not raise
        await manager.check_budget(estimated_cost=3)

    @pytest.mark.asyncio
    async def test_is_budget_available_returns_false_when_exhausted(self):
        """Wraps check_budget exception → returns False."""
        cache = self._make_cache(daily_used=15, monthly_used=0)
        manager = BudgetManager(make_settings(daily_credit_target=16), cache)

        result = await manager.is_budget_available(estimated_cost=3)

        assert result is False

    @pytest.mark.asyncio
    async def test_record_usage_increments_both_counters(self):
        """Calls increment_daily_budget and increment_monthly_budget with correct value."""
        cache = self._make_cache()
        manager = BudgetManager(make_settings(), cache)

        await manager.record_usage(5)

        cache.increment_daily_budget.assert_awaited_once_with(5)
        cache.increment_monthly_budget.assert_awaited_once_with(5)


# ---------------------------------------------------------------------------
# SportDiscovery
# ---------------------------------------------------------------------------


class TestSportDiscovery:
    def _make_client(self) -> MagicMock:
        client = MagicMock()
        client.get_sports = AsyncMock()
        client.get_events = AsyncMock()
        return client

    def _make_cache(self, cached_sports=None) -> MagicMock:
        cache = MagicMock()
        cache.get_active_sports = AsyncMock(return_value=cached_sports)
        cache.set_active_sports = AsyncMock()
        return cache

    @pytest.mark.asyncio
    async def test_returns_cached_sports_on_hit(self):
        """Cache hit → client.get_sports() NOT called."""
        cached = [
            make_sport("tennis_atp_indian_wells"),
            make_sport("tennis_wta_indian_wells"),
            make_sport("basketball_ncaab", "Basketball"),
        ]
        cache = self._make_cache(cached_sports=cached)
        client = self._make_client()
        discovery = SportDiscovery(client, cache)

        result = await discovery.get_active_sport_keys()

        client.get_sports.assert_not_awaited()
        assert set(result) == {
            "tennis_atp_indian_wells",
            "tennis_wta_indian_wells",
            "basketball_ncaab",
        }

    @pytest.mark.asyncio
    async def test_calls_api_on_cache_miss(self):
        """Cache miss → client.get_sports() called, result cached, filtered."""
        api_sports = [
            make_sport("tennis_atp_indian_wells"),
            make_sport("basketball_ncaab", "Basketball"),
            make_sport("soccer_epl", "Soccer"),
        ]
        cache = self._make_cache(cached_sports=None)
        client = self._make_client()
        client.get_sports.return_value = api_sports
        discovery = SportDiscovery(client, cache)

        result = await discovery.get_active_sport_keys()

        client.get_sports.assert_awaited_once_with(active_only=True)
        cache.set_active_sports.assert_awaited_once_with(api_sports)
        assert set(result) == {"tennis_atp_indian_wells", "basketball_ncaab"}
        assert "soccer_epl" not in result

    @pytest.mark.asyncio
    async def test_filters_to_target_sports(self):
        """From the full sports.json fixture, only our target sports are returned."""
        raw_sports = json.loads((FIXTURES / "sports.json").read_text())
        all_sports = [OddsAPISport.model_validate(s) for s in raw_sports]

        cache = self._make_cache(cached_sports=None)
        client = self._make_client()
        client.get_sports.return_value = all_sports
        discovery = SportDiscovery(client, cache)

        result = await discovery.get_active_sport_keys()

        # Every returned key must match our target patterns
        for key in result:
            assert (
                key.startswith("tennis_atp_")
                or key.startswith("tennis_wta_")
                or key == "basketball_ncaab"
            ), f"Unexpected sport key returned: {key}"

        # Specific keys from the fixture that should be included
        assert "tennis_atp_indian_wells" in result
        assert "tennis_wta_indian_wells" in result
        assert "basketball_ncaab" in result

        # Non-target sports must not appear
        assert "soccer_epl" not in result
        assert "basketball_nba" not in result

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_target_sports_active(self):
        """All sports are unrelated → returns []."""
        unrelated = [
            make_sport("soccer_epl", "Soccer"),
            make_sport("basketball_nba", "Basketball"),
            make_sport("icehockey_nhl", "Ice Hockey"),
        ]
        cache = self._make_cache(cached_sports=None)
        client = self._make_client()
        client.get_sports.return_value = unrelated
        discovery = SportDiscovery(client, cache)

        result = await discovery.get_active_sport_keys()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_events_count_returns_zero_on_api_error(self):
        """client.get_events raises OddsAPIError → returns 0 (graceful degradation)."""
        cache = self._make_cache()
        client = self._make_client()
        client.get_events.side_effect = OddsAPIError(500, "Internal Server Error")
        discovery = SportDiscovery(client, cache)

        result = await discovery.get_active_events_count("basketball_ncaab")

        assert result == 0


# ---------------------------------------------------------------------------
# FetchScheduler
# ---------------------------------------------------------------------------


class TestFetchScheduler:
    def _make_budget_manager(self, available: bool = True) -> MagicMock:
        manager = MagicMock()
        manager.is_budget_available = AsyncMock(return_value=available)
        return manager

    def _make_sport_discovery(
        self, sport_keys: list[str] | None = None, event_counts: dict[str, int] | None = None
    ) -> MagicMock:
        if sport_keys is None:
            sport_keys = []
        if event_counts is None:
            event_counts = {}

        discovery = MagicMock()
        discovery.get_active_sport_keys = AsyncMock(return_value=sport_keys)

        async def _get_count(key: str) -> int:
            return event_counts.get(key, 0)

        discovery.get_active_events_count = AsyncMock(side_effect=_get_count)
        return discovery

    @pytest.mark.asyncio
    async def test_get_sports_to_fetch_returns_empty_on_exhausted_budget(self):
        """Budget exhausted → returns []."""
        sport_keys = ["tennis_atp_indian_wells", "basketball_ncaab"]
        event_counts = {"tennis_atp_indian_wells": 4, "basketball_ncaab": 12}

        budget = self._make_budget_manager(available=False)
        discovery = self._make_sport_discovery(sport_keys, event_counts)
        scheduler = FetchScheduler(make_settings(), budget, discovery)

        result = await scheduler.get_sports_to_fetch()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_sports_to_fetch_skips_sports_with_no_events(self):
        """Sport with 0 events is excluded from result."""
        sport_keys = ["tennis_atp_indian_wells", "tennis_wta_indian_wells", "basketball_ncaab"]
        event_counts = {
            "tennis_atp_indian_wells": 0,
            "tennis_wta_indian_wells": 3,
            "basketball_ncaab": 8,
        }

        budget = self._make_budget_manager(available=True)
        discovery = self._make_sport_discovery(sport_keys, event_counts)
        scheduler = FetchScheduler(make_settings(), budget, discovery)

        result = await scheduler.get_sports_to_fetch()

        assert "tennis_atp_indian_wells" not in result
        assert "tennis_wta_indian_wells" in result
        assert "basketball_ncaab" in result

    @pytest.mark.asyncio
    async def test_get_sports_to_fetch_returns_active_sports(self):
        """2 sports with events, budget available → both returned."""
        sport_keys = ["tennis_atp_indian_wells", "basketball_ncaab"]
        event_counts = {"tennis_atp_indian_wells": 2, "basketball_ncaab": 10}

        budget = self._make_budget_manager(available=True)
        discovery = self._make_sport_discovery(sport_keys, event_counts)
        scheduler = FetchScheduler(make_settings(), budget, discovery)

        result = await scheduler.get_sports_to_fetch()

        assert set(result) == {"tennis_atp_indian_wells", "basketball_ncaab"}

    def test_estimate_cost(self):
        """3 sports x 3 markets = 9 credits."""
        budget = self._make_budget_manager()
        discovery = self._make_sport_discovery()
        scheduler = FetchScheduler(make_settings(), budget, discovery)

        cost = scheduler.estimate_cost(
            ["tennis_atp_indian_wells", "tennis_wta_indian_wells", "basketball_ncaab"]
        )

        assert cost == 9
