"""Unit tests for OddsPublisher."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from odds_engine.schemas.enriched import EnrichedEventResponse
from odds_engine.services.publisher import OddsPublisher


def make_enriched_event(
    event_id: str = "evt1",
    sport_group: str = "Basketball",
    sport_key: str = "basketball_ncaab",
) -> EnrichedEventResponse:
    snapshot_id = uuid4()
    return EnrichedEventResponse(
        event_id=event_id,
        sport_key=sport_key,
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


@pytest.fixture()
def mock_cache() -> AsyncMock:
    cache = AsyncMock()
    cache.set_event = AsyncMock()
    cache.publish_odds_update = AsyncMock()
    cache.set_active_events = AsyncMock()
    return cache


@pytest.fixture()
def publisher(mock_cache: AsyncMock) -> OddsPublisher:
    return OddsPublisher(cache=mock_cache)


@pytest.mark.asyncio
async def test_publish_calls_set_event_and_publish(
    publisher: OddsPublisher, mock_cache: AsyncMock
) -> None:
    """publish() must call set_event and publish_odds_update with the event."""
    event = make_enriched_event()

    await publisher.publish(event)

    mock_cache.set_event.assert_awaited_once_with(event)
    mock_cache.publish_odds_update.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_publish_batch_publishes_all_events(
    publisher: OddsPublisher, mock_cache: AsyncMock
) -> None:
    """publish_batch() with 3 events calls publish (set_event + publish_odds_update) 3 times."""
    events = [
        make_enriched_event("evt1"),
        make_enriched_event("evt2"),
        make_enriched_event("evt3"),
    ]

    await publisher.publish_batch(events)

    assert mock_cache.set_event.await_count == 3
    assert mock_cache.publish_odds_update.await_count == 3


@pytest.mark.asyncio
async def test_publish_batch_updates_active_events_per_sport_group(
    publisher: OddsPublisher, mock_cache: AsyncMock
) -> None:
    """publish_batch() groups by sport_group and calls set_active_events once per group."""
    basketball_1 = make_enriched_event("evt1", sport_group="Basketball")
    basketball_2 = make_enriched_event("evt2", sport_group="Basketball")
    tennis_1 = make_enriched_event("evt3", sport_group="Tennis", sport_key="tennis_atp_ro")

    await publisher.publish_batch([basketball_1, basketball_2, tennis_1])

    # set_active_events must be called exactly twice — once per sport group
    assert mock_cache.set_active_events.await_count == 2

    # Capture all calls and verify by sport_group
    calls = mock_cache.set_active_events.call_args_list
    call_map: dict[str, list] = {}
    for call in calls:
        group, group_events = call.args
        call_map[group] = group_events

    assert "Basketball" in call_map
    assert "Tennis" in call_map
    assert len(call_map["Basketball"]) == 2
    assert len(call_map["Tennis"]) == 1
    assert basketball_1 in call_map["Basketball"]
    assert basketball_2 in call_map["Basketball"]
    assert tennis_1 in call_map["Tennis"]
