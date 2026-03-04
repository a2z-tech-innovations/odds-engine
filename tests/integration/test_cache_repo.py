"""Integration tests for CacheRepository — requires a real Redis instance."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis

from odds_engine.repositories.cache_repo import CacheRepository
from odds_engine.schemas.enriched import EnrichedEventResponse
from odds_engine.schemas.odds_api import OddsAPISport


def make_enriched_event(sport_group: str = "Basketball") -> EnrichedEventResponse:
    return EnrichedEventResponse(
        event_id="ext_abc123",
        sport_key="basketball_ncaab",
        sport_group=sport_group,
        home_team="Duke Blue Devils",
        away_team="UNC Tar Heels",
        commence_time=datetime(2026, 3, 10, 19, 0, tzinfo=UTC),
        status="upcoming",
        snapshot_id=uuid.uuid4(),
        fetched_at=datetime.now(UTC),
        bookmakers={},
        best_line={"h2h": {"Duke Blue Devils": {"price": -145.0, "bookmaker": "betmgm"}}},
        consensus={"h2h": {"Duke Blue Devils": {"price": -148.5}}},
        vig_free={"h2h": {"Duke Blue Devils": {"implied_prob": 0.597}}},
        movement={},
    )


def make_odds_api_sport(key: str = "basketball_ncaab") -> OddsAPISport:
    return OddsAPISport(
        key=key,
        group="Basketball",
        title="NCAAB",
        description="US College Basketball",
        active=True,
        has_outrights=False,
    )


# ---------------------------------------------------------------------------
# Event cache tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_event(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)
    event = make_enriched_event()

    await repo.set_event(event)
    result = await repo.get_event(event.event_id)

    assert result is not None
    assert result.event_id == event.event_id
    assert result.sport_key == event.sport_key
    assert result.sport_group == event.sport_group
    assert result.home_team == event.home_team
    assert result.away_team == event.away_team
    assert result.status == event.status
    assert result.snapshot_id == event.snapshot_id


@pytest.mark.asyncio
async def test_get_event_returns_none_on_miss(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    result = await repo.get_event("nonexistent_event_id")

    assert result is None


@pytest.mark.asyncio
async def test_set_event_has_ttl(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)
    event = make_enriched_event()

    await repo.set_event(event)
    ttl = await redis_client.ttl(f"event:{event.event_id}")

    assert ttl > 0
    assert ttl <= 300


@pytest.mark.asyncio
async def test_set_and_get_active_events(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)
    events = [
        make_enriched_event("Basketball"),
        make_enriched_event("Basketball"),
    ]
    # Give each event a distinct snapshot_id
    events[1] = EnrichedEventResponse(
        **{**events[1].model_dump(), "event_id": "ext_def456", "snapshot_id": uuid.uuid4()}
    )

    await repo.set_active_events("Basketball", events)
    result = await repo.get_active_events("Basketball")

    assert result is not None
    assert len(result) == 2
    ids = {e.event_id for e in result}
    assert "ext_abc123" in ids
    assert "ext_def456" in ids


@pytest.mark.asyncio
async def test_get_active_events_returns_none_on_miss(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    result = await repo.get_active_events("NonExistentSport")

    assert result is None


@pytest.mark.asyncio
async def test_active_events_ttl(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)
    events = [make_enriched_event("Tennis")]

    await repo.set_active_events("Tennis", events)
    ttl = await redis_client.ttl("events:Tennis:active")

    assert ttl > 0
    assert ttl <= 300


# ---------------------------------------------------------------------------
# Sports cache tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_active_sports(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)
    sports = [
        make_odds_api_sport("basketball_ncaab"),
        make_odds_api_sport("tennis_atp_indian_wells"),
    ]

    await repo.set_active_sports(sports)
    result = await repo.get_active_sports()

    assert result is not None
    assert len(result) == 2
    keys = {s.key for s in result}
    assert "basketball_ncaab" in keys
    assert "tennis_atp_indian_wells" in keys


@pytest.mark.asyncio
async def test_get_active_sports_returns_none_on_miss(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    result = await repo.get_active_sports()

    assert result is None


@pytest.mark.asyncio
async def test_active_sports_ttl(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)
    sports = [make_odds_api_sport()]

    await repo.set_active_sports(sports)
    ttl = await redis_client.ttl("sports:active")

    assert ttl > 0
    assert ttl <= 3600


# ---------------------------------------------------------------------------
# Budget tracking tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_increment_daily_budget(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    first = await repo.increment_daily_budget(3)
    second = await repo.increment_daily_budget(5)

    assert first == 3
    assert second == 8


@pytest.mark.asyncio
async def test_increment_daily_budget_sets_expiry(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    await repo.increment_daily_budget(3)
    ttl = await redis_client.ttl("budget:daily")

    assert ttl > 0
    assert ttl <= 86400  # at most 24 hours


@pytest.mark.asyncio
async def test_increment_monthly_budget(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    total = await repo.increment_monthly_budget(10)

    assert total == 10


@pytest.mark.asyncio
async def test_increment_monthly_budget_accumulates(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    await repo.increment_monthly_budget(10)
    total = await repo.increment_monthly_budget(7)

    assert total == 17


@pytest.mark.asyncio
async def test_increment_monthly_budget_sets_expiry(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    await repo.increment_monthly_budget(5)
    ttl = await redis_client.ttl("budget:monthly")

    assert ttl > 0
    assert ttl <= 31 * 86400  # at most ~31 days


@pytest.mark.asyncio
async def test_get_budget_returns_zeros_on_empty(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)

    budget = await repo.get_budget()

    assert budget == {"daily_used": 0, "monthly_used": 0}


@pytest.mark.asyncio
async def test_get_budget_returns_current_values(redis_client: Redis) -> None:
    repo = CacheRepository(redis_client)
    await repo.increment_daily_budget(4)
    await repo.increment_monthly_budget(12)

    budget = await repo.get_budget()

    assert budget["daily_used"] == 4
    assert budget["monthly_used"] == 12


# ---------------------------------------------------------------------------
# Pub/Sub tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_odds_update(redis_client: Redis) -> None:
    event = make_enriched_event("Basketball")
    repo = CacheRepository(redis_client)

    pubsub = redis_client.pubsub()
    await pubsub.subscribe("odds:updates:all", f"odds:updates:{event.sport_group}")

    # Allow time for subscriptions to be registered
    await asyncio.sleep(0.1)

    await repo.publish_odds_update(event)

    received_channels = set()
    for _ in range(4):  # drain up to 4 messages (2 subscribe confirmations + 2 publishes)
        try:
            msg = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                timeout=2.0,
            )
        except TimeoutError:
            break
        if msg is not None:
            received_channels.add(msg["channel"])

    await pubsub.unsubscribe()
    await pubsub.aclose()

    assert "odds:updates:all" in received_channels
    assert f"odds:updates:{event.sport_group}" in received_channels


@pytest.mark.asyncio
async def test_publish_odds_update_payload_is_valid_json(redis_client: Redis) -> None:
    event = make_enriched_event("Tennis")
    repo = CacheRepository(redis_client)

    pubsub = redis_client.pubsub()
    await pubsub.subscribe("odds:updates:all")

    await asyncio.sleep(0.1)

    await repo.publish_odds_update(event)

    msg = None
    for _ in range(3):
        try:
            msg = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                timeout=2.0,
            )
        except TimeoutError:
            break
        if msg is not None:
            break

    await pubsub.unsubscribe()
    await pubsub.aclose()

    assert msg is not None
    parsed = EnrichedEventResponse.model_validate_json(msg["data"])
    assert parsed.event_id == event.event_id
    assert parsed.sport_group == event.sport_group
