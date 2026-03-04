"""Unit tests for OddsService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from odds_engine.clients.odds_api import OddsAPIUsage
from odds_engine.schemas.odds import ManualFetchResponse
from odds_engine.schemas.odds_api import (
    OddsAPIBookmaker,
    OddsAPIEvent,
    OddsAPIMarket,
    OddsAPIOutcome,
)
from odds_engine.services.odds_service import OddsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_api_event(event_id: str = "evt1", bookmakers_count: int = 2) -> OddsAPIEvent:
    outcomes = [
        OddsAPIOutcome(name="Team A", price=-150),
        OddsAPIOutcome(name="Team B", price=130),
    ]
    market = OddsAPIMarket(key="h2h", outcomes=outcomes)
    bookmaker_keys = ["draftkings", "fanduel", "betmgm", "caesars"]
    bookmakers = [
        OddsAPIBookmaker(key=bookmaker_keys[i], title=bookmaker_keys[i].title(), markets=[market])
        for i in range(bookmakers_count)
    ]
    return OddsAPIEvent(
        id=event_id,
        sport_key="basketball_ncaab",
        sport_title="NCAAB",
        home_team="Team A",
        away_team="Team B",
        commence_time=datetime(2026, 3, 10, 19, 0, tzinfo=UTC),
        bookmakers=bookmakers,
    )


def make_db_event(external_id: str = "evt1") -> MagicMock:
    event = MagicMock()
    event.id = uuid4()
    event.external_id = external_id
    event.sport_key = "basketball_ncaab"
    event.sport_group = "Basketball"
    event.home_team = "Team A"
    event.away_team = "Team B"
    event.status = "upcoming"
    return event


def make_snapshot(event_id=None) -> MagicMock:
    snap = MagicMock()
    snap.id = uuid4()
    snap.event_id = event_id or uuid4()
    return snap


def make_usage(credits_used: int = 3, credits_remaining: int = 497) -> OddsAPIUsage:
    return OddsAPIUsage(credits_used=credits_used, credits_remaining=credits_remaining)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.get_odds = AsyncMock()
    return client


@pytest.fixture()
def mock_event_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.upsert_event = AsyncMock(side_effect=lambda **kw: make_db_event(kw["external_id"]))
    return repo


@pytest.fixture()
def mock_odds_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.create_snapshot = AsyncMock(side_effect=lambda **kw: make_snapshot(kw["event_id"]))
    repo.create_bookmaker_odds_batch = AsyncMock()
    repo.get_latest_enriched = AsyncMock(return_value=None)
    repo.create_enriched_snapshot = AsyncMock(return_value=MagicMock())
    repo.record_api_usage = AsyncMock(return_value=MagicMock())
    return repo


@pytest.fixture()
def mock_cache() -> AsyncMock:
    cache = AsyncMock()
    cache.increment_daily_budget = AsyncMock(return_value=3)
    cache.increment_monthly_budget = AsyncMock(return_value=3)
    cache.get_active_events = AsyncMock(return_value=None)
    return cache


@pytest.fixture()
def mock_publisher() -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish_batch = AsyncMock()
    return publisher


@pytest.fixture()
def service(
    mock_client: AsyncMock,
    mock_event_repo: AsyncMock,
    mock_odds_repo: AsyncMock,
    mock_cache: AsyncMock,
    mock_publisher: AsyncMock,
) -> OddsService:
    return OddsService(
        client=mock_client,
        event_repo=mock_event_repo,
        odds_repo=mock_odds_repo,
        cache=mock_cache,
        publisher=mock_publisher,
    )


# ---------------------------------------------------------------------------
# fetch_and_store tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_store_returns_empty_on_no_events(
    service: OddsService,
    mock_client: AsyncMock,
    mock_event_repo: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    """When client returns no events, ManualFetchResponse has events_fetched=0."""
    mock_client.get_odds.return_value = ([], make_usage(credits_used=0, credits_remaining=500))

    result = await service.fetch_and_store("basketball_ncaab", "Basketball")

    assert isinstance(result, ManualFetchResponse)
    assert result.events_fetched == 0
    assert result.sport_key == "basketball_ncaab"
    mock_event_repo.upsert_event.assert_not_awaited()
    mock_publisher.publish_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_and_store_upserts_events(
    service: OddsService,
    mock_client: AsyncMock,
    mock_event_repo: AsyncMock,
) -> None:
    """For 2 API events, event_repo.upsert_event is called twice."""
    api_events = [make_api_event("evt1"), make_api_event("evt2")]
    mock_client.get_odds.return_value = (api_events, make_usage())

    await service.fetch_and_store("basketball_ncaab", "Basketball")

    assert mock_event_repo.upsert_event.await_count == 2
    call_external_ids = [
        c.kwargs["external_id"] for c in mock_event_repo.upsert_event.call_args_list
    ]
    assert "evt1" in call_external_ids
    assert "evt2" in call_external_ids


@pytest.mark.asyncio
async def test_fetch_and_store_creates_snapshots(
    service: OddsService,
    mock_client: AsyncMock,
    mock_odds_repo: AsyncMock,
) -> None:
    """For 2 API events, create_snapshot is called twice."""
    api_events = [make_api_event("evt1"), make_api_event("evt2")]
    mock_client.get_odds.return_value = (api_events, make_usage())

    await service.fetch_and_store("basketball_ncaab", "Basketball")

    assert mock_odds_repo.create_snapshot.await_count == 2


@pytest.mark.asyncio
async def test_fetch_and_store_bulk_inserts_bookmaker_odds(
    service: OddsService,
    mock_client: AsyncMock,
    mock_odds_repo: AsyncMock,
) -> None:
    """2 events each with 2 bookmakers, 1 market, 2 outcomes -> 4 rows each call."""
    # 2 bookmakers x 1 market x 2 outcomes = 4 rows per event
    api_events = [
        make_api_event("evt1", bookmakers_count=2),
        make_api_event("evt2", bookmakers_count=2),
    ]
    mock_client.get_odds.return_value = (api_events, make_usage())

    await service.fetch_and_store("basketball_ncaab", "Basketball")

    assert mock_odds_repo.create_bookmaker_odds_batch.await_count == 2
    for call_obj in mock_odds_repo.create_bookmaker_odds_batch.call_args_list:
        rows = call_obj.args[0]
        # 2 bookmakers x 1 market x 2 outcomes = 4 rows
        assert len(rows) == 4


@pytest.mark.asyncio
async def test_fetch_and_store_records_api_usage(
    service: OddsService,
    mock_client: AsyncMock,
    mock_odds_repo: AsyncMock,
) -> None:
    """After fetch, record_api_usage is called with the correct values."""
    api_events = [make_api_event("evt1")]
    usage = make_usage(credits_used=3, credits_remaining=497)
    mock_client.get_odds.return_value = (api_events, usage)

    await service.fetch_and_store("basketball_ncaab", "Basketball")

    mock_odds_repo.record_api_usage.assert_awaited_once_with(
        3, 497, "odds", "basketball_ncaab"
    )


@pytest.mark.asyncio
async def test_fetch_and_store_increments_budget(
    service: OddsService,
    mock_client: AsyncMock,
    mock_cache: AsyncMock,
) -> None:
    """After fetch, both daily and monthly budget counters are incremented."""
    api_events = [make_api_event("evt1")]
    usage = make_usage(credits_used=3)
    mock_client.get_odds.return_value = (api_events, usage)

    await service.fetch_and_store("basketball_ncaab", "Basketball")

    mock_cache.increment_daily_budget.assert_awaited_once_with(3)
    mock_cache.increment_monthly_budget.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_fetch_and_store_publishes_enriched_events(
    service: OddsService,
    mock_client: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    """publisher.publish_batch is called once with the list of enriched events."""
    api_events = [make_api_event("evt1"), make_api_event("evt2")]
    mock_client.get_odds.return_value = (api_events, make_usage())

    await service.fetch_and_store("basketball_ncaab", "Basketball")

    mock_publisher.publish_batch.assert_awaited_once()
    published = mock_publisher.publish_batch.call_args.args[0]
    assert len(published) == 2


@pytest.mark.asyncio
async def test_fetch_and_store_returns_correct_response(
    service: OddsService,
    mock_client: AsyncMock,
) -> None:
    """ManualFetchResponse has correct sport_key, events_fetched, credits_used."""
    api_events = [make_api_event("evt1"), make_api_event("evt2")]
    usage = make_usage(credits_used=6, credits_remaining=494)
    mock_client.get_odds.return_value = (api_events, usage)

    result = await service.fetch_and_store("basketball_ncaab", "Basketball")

    assert result.sport_key == "basketball_ncaab"
    assert result.events_fetched == 2
    assert result.credits_used == 6
