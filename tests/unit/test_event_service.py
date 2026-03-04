"""Unit tests for EventService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from odds_engine.exceptions import EventNotFoundError
from odds_engine.models.enums import EventStatus
from odds_engine.schemas.enriched import EnrichedEventResponse
from odds_engine.schemas.events import EventFilterParams
from odds_engine.services.event_service import EventService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_enriched_event(
    event_id: str = "ext1",
    sport_group: str = "Basketball",
    sport_key: str = "basketball_ncaab",
    status: str = "upcoming",
) -> EnrichedEventResponse:
    return EnrichedEventResponse(
        event_id=event_id,
        sport_key=sport_key,
        sport_group=sport_group,
        home_team="Team A",
        away_team="Team B",
        commence_time=datetime(2026, 3, 10, 19, 0, tzinfo=UTC),
        status=status,
        snapshot_id=uuid4(),
        fetched_at=datetime(2026, 3, 3, 12, 0, tzinfo=UTC),
        bookmakers={},
        best_line={},
        consensus={},
        vig_free={},
        movement={},
    )


def make_db_event(external_id: str = "ext1", sport_group: str = "Basketball") -> MagicMock:
    event = MagicMock()
    event.id = uuid4()
    event.external_id = external_id
    event.sport_key = "basketball_ncaab"
    event.sport_group = sport_group
    event.home_team = "Team A"
    event.away_team = "Team B"
    event.commence_time = datetime(2026, 3, 10, 19, 0, tzinfo=UTC)
    event.status = EventStatus.upcoming
    event.created_at = datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
    event.updated_at = datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
    return event


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_many = AsyncMock(return_value=[])
    repo.get_by_external_id = AsyncMock(return_value=None)
    return repo


@pytest.fixture()
def mock_cache() -> AsyncMock:
    cache = AsyncMock()
    cache.get_active_events = AsyncMock(return_value=None)
    cache.get_event = AsyncMock(return_value=None)
    return cache


@pytest.fixture()
def service(mock_repo: AsyncMock, mock_cache: AsyncMock) -> EventService:
    return EventService(repo=mock_repo, cache=mock_cache)


# ---------------------------------------------------------------------------
# get_events tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_events_returns_enriched_from_cache(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """Cache hit → returns list[EnrichedEventResponse] with odds data; repo not called."""
    cached_events = [make_enriched_event("evt1"), make_enriched_event("evt2")]
    mock_cache.get_active_events.return_value = cached_events

    result = await service.get_events(EventFilterParams(sport_group="Basketball"))

    mock_cache.get_active_events.assert_awaited_once_with("Basketball")
    mock_repo.get_many.assert_not_awaited()
    assert len(result) == 2
    assert all(isinstance(e, EnrichedEventResponse) for e in result)


@pytest.mark.asyncio
async def test_get_events_falls_back_to_db_on_cache_miss(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """Cache miss (returns None) → repo.get_many is called, returns thin enriched events."""
    mock_cache.get_active_events.return_value = None
    mock_repo.get_many.return_value = [make_db_event()]

    result = await service.get_events(EventFilterParams(sport_group="Basketball"))

    mock_repo.get_many.assert_awaited_once()
    assert len(result) == 1
    assert isinstance(result[0], EnrichedEventResponse)
    assert result[0].bookmakers == {}  # thin — no odds data from DB fallback


@pytest.mark.asyncio
async def test_get_events_db_fallback_returns_all_db_events(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """DB path returns one EnrichedEventResponse per ORM event."""
    mock_cache.get_active_events.return_value = None
    mock_repo.get_many.return_value = [make_db_event("a"), make_db_event("b"), make_db_event("c")]

    result = await service.get_events(EventFilterParams(sport_group="Basketball"))

    assert len(result) == 3


@pytest.mark.asyncio
async def test_get_events_filters_sport_key_on_cache_results(
    service: EventService, mock_cache: AsyncMock
) -> None:
    """sport_key filter applied client-side on cache results."""
    cached = [
        make_enriched_event("e1", sport_key="basketball_ncaab"),
        make_enriched_event("e2", sport_key="basketball_nba"),
    ]
    mock_cache.get_active_events.return_value = cached

    result = await service.get_events(
        EventFilterParams(sport_group="Basketball", sport_key="basketball_ncaab")
    )

    assert len(result) == 1
    assert result[0].event_id == "e1"


@pytest.mark.asyncio
async def test_get_events_filters_status_on_cache_results(
    service: EventService, mock_cache: AsyncMock
) -> None:
    """status filter applied client-side on cache results."""
    cached = [
        make_enriched_event("e1", status="upcoming"),
        make_enriched_event("e2", status="live"),
    ]
    mock_cache.get_active_events.return_value = cached

    result = await service.get_events(
        EventFilterParams(sport_group="Basketball", status=EventStatus.live)
    )

    assert len(result) == 1
    assert result[0].event_id == "e2"


# ---------------------------------------------------------------------------
# get_event tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_event_returns_enriched_from_cache(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """Cache hit → returns EnrichedEventResponse directly; repo not called."""
    enriched = make_enriched_event("ext-abc")
    mock_cache.get_event.return_value = enriched

    result = await service.get_event("ext-abc")

    mock_cache.get_event.assert_awaited_once_with("ext-abc")
    mock_repo.get_by_external_id.assert_not_awaited()
    assert isinstance(result, EnrichedEventResponse)
    assert result.event_id == "ext-abc"
    assert result.sport_key == "basketball_ncaab"


@pytest.mark.asyncio
async def test_get_event_falls_back_to_db_on_cache_miss(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """Cache miss → repo.get_by_external_id is called; returns thin enriched event."""
    mock_cache.get_event.return_value = None
    mock_repo.get_by_external_id.return_value = make_db_event("ext-abc")

    result = await service.get_event("ext-abc")

    mock_repo.get_by_external_id.assert_awaited_once_with("ext-abc")
    assert isinstance(result, EnrichedEventResponse)
    assert result.event_id == "ext-abc"
    assert result.bookmakers == {}  # thin — no odds from DB fallback


@pytest.mark.asyncio
async def test_get_event_raises_not_found_when_missing(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """Cache miss + repo returns None → EventNotFoundError raised."""
    mock_cache.get_event.return_value = None
    mock_repo.get_by_external_id.return_value = None

    with pytest.raises(EventNotFoundError) as exc_info:
        await service.get_event("missing-id")

    assert exc_info.value.event_id == "missing-id"
