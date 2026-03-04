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
) -> EnrichedEventResponse:
    snapshot_id = uuid4()
    return EnrichedEventResponse(
        event_id=event_id,
        sport_key="basketball_ncaab",
        sport_group=sport_group,
        home_team="Team A",
        away_team="Team B",
        commence_time=datetime(2026, 3, 10, 19, 0, tzinfo=UTC),
        status="upcoming",
        snapshot_id=snapshot_id,
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
    repo.count = AsyncMock(return_value=0)
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
async def test_get_events_returns_from_cache_when_sport_group_set(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """Cache hit with sport_group set → repo.get_many is NOT called."""
    cached_events = [make_enriched_event("evt1"), make_enriched_event("evt2")]
    mock_cache.get_active_events.return_value = cached_events

    filters = EventFilterParams(sport_group="Basketball")
    result = await service.get_events(filters)

    mock_cache.get_active_events.assert_awaited_once_with("Basketball")
    mock_repo.get_many.assert_not_awaited()
    assert result.total == 2
    assert len(result.events) == 2


@pytest.mark.asyncio
async def test_get_events_falls_back_to_db_on_cache_miss(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """Cache miss (returns None) → repo.get_many is called."""
    mock_cache.get_active_events.return_value = None
    db_event = make_db_event()
    mock_repo.get_many.return_value = [db_event]
    mock_repo.count.return_value = 1

    filters = EventFilterParams(sport_group="Basketball")
    result = await service.get_events(filters)

    mock_repo.get_many.assert_awaited_once_with(filters)
    assert result.total == 1


@pytest.mark.asyncio
async def test_get_events_db_path_returns_correct_total(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """repo.count returns 42 → EventListResponse.total == 42."""
    mock_cache.get_active_events.return_value = None
    mock_repo.get_many.return_value = []
    mock_repo.count.return_value = 42

    filters = EventFilterParams(sport_group="Basketball")
    result = await service.get_events(filters)

    assert result.total == 42


# ---------------------------------------------------------------------------
# get_event tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_event_returns_from_cache(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """cache.get_event hit → mapped to EventResponse; repo not called."""
    enriched = make_enriched_event("ext-abc")
    mock_cache.get_event.return_value = enriched

    result = await service.get_event("ext-abc")

    mock_cache.get_event.assert_awaited_once_with("ext-abc")
    mock_repo.get_by_external_id.assert_not_awaited()
    assert result.external_id == "ext-abc"
    assert result.sport_key == "basketball_ncaab"


@pytest.mark.asyncio
async def test_get_event_falls_back_to_db_on_cache_miss(
    service: EventService, mock_repo: AsyncMock, mock_cache: AsyncMock
) -> None:
    """Cache miss → repo.get_by_external_id is called."""
    mock_cache.get_event.return_value = None
    db_event = make_db_event("ext-abc")
    mock_repo.get_by_external_id.return_value = db_event

    await service.get_event("ext-abc")

    mock_repo.get_by_external_id.assert_awaited_once_with("ext-abc")


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
