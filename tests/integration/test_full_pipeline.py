"""
Full service-layer pipeline integration tests.

Exercises the complete pipeline end-to-end using:
- Real db_session and redis_client fixtures (from conftest.py)
- A mocked OddsAPIClient that returns fixture data (no real HTTP calls)
- Real EventRepository, OddsRepository, CacheRepository
- Real OddsService, EventService, OddsPublisher — the actual business logic

Run with: uv run pytest tests/integration/test_full_pipeline.py -v -s
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis

from odds_engine.clients.odds_api import OddsAPIClient, OddsAPIUsage
from odds_engine.models.events import Event
from odds_engine.repositories.cache_repo import CacheRepository
from odds_engine.repositories.event_repo import EventRepository
from odds_engine.repositories.odds_repo import OddsRepository
from odds_engine.schemas.enriched import (
    BestLineOutcome,
    ConsensusOutcome,
    EnrichedBookmakerMarket,
    EnrichedEventResponse,
    VigFreeOutcome,
)
from odds_engine.schemas.odds import ManualFetchResponse, OutcomeSchema
from odds_engine.schemas.odds_api import OddsAPIEvent
from odds_engine.services.event_service import EventService
from odds_engine.services.odds_service import OddsService
from odds_engine.services.publisher import OddsPublisher

FIXTURES = Path(__file__).parent.parent / "fixtures" / "odds_api"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_odds_fixture(filename: str) -> list[OddsAPIEvent]:
    raw = json.loads((FIXTURES / filename).read_text())
    return [OddsAPIEvent.model_validate(e) for e in raw]


def make_mock_client(api_events: list[OddsAPIEvent], credits_used: int = 3, credits_remaining: int = 491) -> OddsAPIClient:
    """Build an AsyncMock stand-in for OddsAPIClient.

    get_odds() returns (api_events, OddsAPIUsage).
    """
    mock_client = MagicMock(spec=OddsAPIClient)
    usage = OddsAPIUsage(credits_used=credits_used, credits_remaining=credits_remaining)
    mock_client.get_odds = AsyncMock(return_value=(api_events, usage))
    return mock_client


def build_minimal_enriched(event_id: str | None = None, sport_group: str = "Basketball") -> EnrichedEventResponse:
    """Construct a minimal but structurally-valid EnrichedEventResponse for testing."""
    eid = event_id or f"ext_{uuid.uuid4().hex[:12]}"
    snap_id = uuid.uuid4()
    home = "Duke Blue Devils"
    away = "UNC Tar Heels"
    market_key = "h2h"
    return EnrichedEventResponse(
        event_id=eid,
        sport_key="basketball_ncaab",
        sport_group=sport_group,
        home_team=home,
        away_team=away,
        commence_time=datetime.now(UTC),
        status="upcoming",
        snapshot_id=snap_id,
        fetched_at=datetime.now(UTC),
        bookmakers={
            "draftkings": {
                market_key: EnrichedBookmakerMarket(
                    outcomes=[
                        OutcomeSchema(name=home, price=-150.0),
                        OutcomeSchema(name=away, price=130.0),
                    ],
                    last_update=None,
                )
            }
        },
        best_line={
            market_key: {
                home: BestLineOutcome(price=-145.0, bookmaker="betmgm"),
                away: BestLineOutcome(price=130.0, bookmaker="draftkings"),
            }
        },
        consensus={
            market_key: {
                home: ConsensusOutcome(price=-147.5),
                away: ConsensusOutcome(price=128.0),
            }
        },
        vig_free={
            market_key: {
                home: VigFreeOutcome(implied_prob=0.597),
                away: VigFreeOutcome(implied_prob=0.403),
            }
        },
        movement={},
    )


# ---------------------------------------------------------------------------
# Test 1: fetch_and_store with NCAAB fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_store_ncaab(db_session, redis_client):
    """Full pipeline for NCAAB: mock client → upsert events → enrich → persist → cache → publish."""
    api_events = load_odds_fixture("odds_basketball_ncaab.json")
    assert len(api_events) == 5

    mock_client = make_mock_client(api_events, credits_used=3, credits_remaining=491)

    event_repo = EventRepository(db_session)
    odds_repo = OddsRepository(db_session)
    cache_repo = CacheRepository(redis_client)
    publisher = OddsPublisher(cache_repo)
    odds_service = OddsService(
        client=mock_client,
        event_repo=event_repo,
        odds_repo=odds_repo,
        cache=cache_repo,
        publisher=publisher,
    )

    result = await odds_service.fetch_and_store(
        sport_key="basketball_ncaab", sport_group="Basketball"
    )

    # fetch_and_store returns a ManualFetchResponse
    assert isinstance(result, ManualFetchResponse)
    assert result.sport_key == "basketball_ncaab"
    assert result.events_fetched == 5
    assert result.credits_used == 3

    await db_session.flush()

    # Verify first event is persisted in DB
    first_api_event = api_events[0]
    db_event = await event_repo.get_by_external_id(first_api_event.id)
    assert db_event is not None
    assert db_event.external_id == first_api_event.id
    assert db_event.home_team == first_api_event.home_team
    assert db_event.sport_key == "basketball_ncaab"

    # Verify enriched snapshot in Redis cache
    cached = await cache_repo.get_event(first_api_event.id)
    assert cached is not None
    assert cached.event_id == first_api_event.id
    assert "h2h" in cached.best_line
    assert "h2h" in cached.consensus
    assert "h2h" in cached.vig_free

    print(f"\n  NCAAB pipeline OK — {result.events_fetched} events, {result.credits_used} credits used")
    for ae in api_events:
        print(f"  {ae.home_team} vs {ae.away_team}")


# ---------------------------------------------------------------------------
# Test 2: fetch_and_store with ATP Indian Wells tennis fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_store_tennis(db_session, redis_client):
    """Full pipeline for ATP Indian Wells tennis."""
    api_events = load_odds_fixture("odds_tennis_atp_indian_wells.json")
    assert len(api_events) == 5

    mock_client = make_mock_client(api_events, credits_used=3, credits_remaining=488)

    event_repo = EventRepository(db_session)
    odds_repo = OddsRepository(db_session)
    cache_repo = CacheRepository(redis_client)
    publisher = OddsPublisher(cache_repo)
    odds_service = OddsService(
        client=mock_client,
        event_repo=event_repo,
        odds_repo=odds_repo,
        cache=cache_repo,
        publisher=publisher,
    )

    result = await odds_service.fetch_and_store(
        sport_key="tennis_atp_indian_wells", sport_group="Tennis"
    )

    assert isinstance(result, ManualFetchResponse)
    assert result.events_fetched == 5

    await db_session.flush()

    # All events should have a sport_key containing "tennis_atp"
    for ae in api_events:
        assert "tennis_atp" in ae.sport_key

    # Verify the first event's cached enriched snapshot
    first_api_event = api_events[0]
    cached = await cache_repo.get_event(first_api_event.id)
    assert cached is not None
    assert "tennis_atp" in cached.sport_key

    # Spreads market should be present in best_line if the fixture has spreads
    all_market_keys: set[str] = set()
    for ae in api_events:
        for bm in ae.bookmakers:
            for mkt in bm.markets:
                all_market_keys.add(mkt.key)

    if "spreads" in all_market_keys:
        # At least one event's cached enriched should contain spreads in best_line
        found_spreads = False
        for ae in api_events:
            c = await cache_repo.get_event(ae.id)
            if c is not None and "spreads" in c.best_line:
                found_spreads = True
                break
        assert found_spreads, "Expected spreads market in at least one cached enriched event"

    print(f"\n  Tennis ATP Indian Wells pipeline OK — markets seen: {all_market_keys}")


# ---------------------------------------------------------------------------
# Test 3: idempotency — second fetch of the same sport key succeeds without duplicates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_store_idempotent(db_session, redis_client):
    """Fetching the same sport key twice creates no DB duplicates."""
    from sqlalchemy import func, select

    api_events = load_odds_fixture("odds_basketball_ncaab.json")
    mock_client = make_mock_client(api_events, credits_used=3, credits_remaining=491)

    event_repo = EventRepository(db_session)
    odds_repo = OddsRepository(db_session)
    cache_repo = CacheRepository(redis_client)
    publisher = OddsPublisher(cache_repo)
    odds_service = OddsService(
        client=mock_client,
        event_repo=event_repo,
        odds_repo=odds_repo,
        cache=cache_repo,
        publisher=publisher,
    )

    # First fetch
    result1 = await odds_service.fetch_and_store(
        sport_key="basketball_ncaab", sport_group="Basketball"
    )
    await db_session.flush()
    assert result1.events_fetched == 5

    # Second fetch — same mock data
    result2 = await odds_service.fetch_and_store(
        sport_key="basketball_ncaab", sport_group="Basketball"
    )
    await db_session.flush()
    assert result2.events_fetched == 5

    # Events table should have exactly 5 rows for this sport_key (no duplicates)
    count_result = await db_session.execute(
        select(func.count()).where(Event.sport_key == "basketball_ncaab")
    )
    event_count = count_result.scalar()
    assert event_count == 5, f"Expected 5 unique events, got {event_count}"

    print(f"\n  Idempotency OK — {event_count} unique NCAAB events after 2 fetches")


# ---------------------------------------------------------------------------
# Test 4: publisher pushes to Redis pub/sub channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_publishes_to_redis_channel(db_session, redis_client):
    """OddsPublisher.publish() sends JSON to the Redis pub/sub channel."""
    from tests.conftest import TEST_REDIS_URL

    cache_repo = CacheRepository(redis_client)
    publisher = OddsPublisher(cache_repo)

    enriched_event = build_minimal_enriched(sport_group="Basketball")

    # Subscribe using a separate sync Redis connection before publishing
    sync_redis = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    pubsub = sync_redis.pubsub()
    channel = f"odds:updates:{enriched_event.sport_group}"
    pubsub.subscribe(channel)

    # Drain the subscription confirmation message
    pubsub.get_message(timeout=0.5)

    # Publish via the service
    await publisher.publish(enriched_event)

    # Allow brief propagation
    import time
    time.sleep(0.2)

    # Read the actual update message
    message = pubsub.get_message(timeout=1.0)

    pubsub.unsubscribe(channel)
    pubsub.close()
    sync_redis.close()

    assert message is not None, f"Expected a pub/sub message on channel '{channel}', got None"
    assert message["type"] == "message"
    assert enriched_event.event_id in message["data"]

    print(f"\n  Pub/sub OK — received message on '{channel}' containing event_id={enriched_event.event_id}")


# ---------------------------------------------------------------------------
# Test 5: EventService uses cache-first lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_service_cache_first(db_session, redis_client):
    """EventService.get_event() returns a cached enriched event without a DB hit."""
    event_repo = EventRepository(db_session)
    cache_repo = CacheRepository(redis_client)
    event_service = EventService(repo=event_repo, cache=cache_repo)

    # Build a minimal enriched event and pre-populate the cache
    enriched = build_minimal_enriched(sport_group="Basketball")
    await cache_repo.set_event(enriched)

    # DB has NO row for this external_id — the service must return from cache
    db_check = await event_repo.get_by_external_id(enriched.event_id)
    assert db_check is None, "DB should not contain this event — test setup error"

    # get_event should resolve from cache only
    response = await event_service.get_event(enriched.event_id)

    assert response is not None
    assert response.external_id == enriched.event_id
    assert response.home_team == enriched.home_team
    assert response.away_team == enriched.away_team

    print(f"\n  Cache-first OK — event_id={enriched.event_id} served from cache, not DB")


# ---------------------------------------------------------------------------
# Test 6: budget tracking accumulates across two fetches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_tracking_across_fetches(db_session, redis_client):
    """Budget increments correctly across two fetch_and_store calls."""
    api_events = load_odds_fixture("odds_basketball_ncaab.json")
    credits_per_fetch = 3
    mock_client = make_mock_client(api_events, credits_used=credits_per_fetch, credits_remaining=491)

    event_repo = EventRepository(db_session)
    odds_repo = OddsRepository(db_session)
    cache_repo = CacheRepository(redis_client)
    publisher = OddsPublisher(cache_repo)
    odds_service = OddsService(
        client=mock_client,
        event_repo=event_repo,
        odds_repo=odds_repo,
        cache=cache_repo,
        publisher=publisher,
    )

    # First fetch
    await odds_service.fetch_and_store(sport_key="basketball_ncaab", sport_group="Basketball")
    # Second fetch
    await odds_service.fetch_and_store(sport_key="basketball_ncaab", sport_group="Basketball")

    budget = await cache_repo.get_budget()
    expected_minimum = credits_per_fetch * 2  # 6

    assert budget["daily_used"] >= expected_minimum, (
        f"Expected daily_used >= {expected_minimum}, got {budget['daily_used']}"
    )
    assert budget["monthly_used"] >= expected_minimum, (
        f"Expected monthly_used >= {expected_minimum}, got {budget['monthly_used']}"
    )

    print(
        f"\n  Budget tracking OK — daily_used={budget['daily_used']}, "
        f"monthly_used={budget['monthly_used']} (min expected: {expected_minimum})"
    )
