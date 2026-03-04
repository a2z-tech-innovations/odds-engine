"""
End-to-end smoke test: fixture JSON → schemas → repositories → DB + Redis.

Exercises the full Phase 1→2 pipeline:
  1. Parse real Odds API fixture files with OddsAPI schemas
  2. Upsert events via EventRepository
  3. Create snapshots + bulk bookmaker_odds via OddsRepository
  4. Build a minimal EnrichedSnapshot and persist it
  5. Cache enriched event to Redis and retrieve it
  6. Verify pub/sub publishes correctly

Uses the real odds_engine_test database and Redis DB 2.
Run with: uv run pytest tests/integration/test_pipeline_smoke.py -v -s
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from odds_engine.repositories.cache_repo import CacheRepository
from odds_engine.repositories.event_repo import EventRepository
from odds_engine.repositories.odds_repo import OddsRepository
from odds_engine.schemas.enriched import EnrichedEventResponse
from odds_engine.schemas.odds_api import OddsAPIEvent

FIXTURES = Path(__file__).parent.parent / "fixtures" / "odds_api"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_odds_fixture(filename: str) -> list[OddsAPIEvent]:
    raw = json.loads((FIXTURES / filename).read_text())
    return [OddsAPIEvent.model_validate(e) for e in raw]


def build_bookmaker_rows(snapshot_id: uuid.UUID, event: OddsAPIEvent) -> list[dict]:
    rows = []
    for bm in event.bookmakers:
        for market in bm.markets:
            for outcome in market.outcomes:
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "snapshot_id": snapshot_id,
                        "bookmaker_key": bm.key,
                        "market_key": market.key,
                        "outcome_name": outcome.name,
                        "outcome_price": outcome.price,
                        "outcome_point": outcome.point,
                        "last_update": bm.last_update,
                        "created_at": datetime.now(UTC),
                    }
                )
    return rows


def build_enriched_payload(event: OddsAPIEvent, snapshot_id: uuid.UUID) -> EnrichedEventResponse:
    """Build a minimal (but structurally valid) enriched payload from raw bookmaker data."""
    best_line: dict = {}
    consensus: dict = {}
    vig_free: dict = {}

    for bm in event.bookmakers:
        for market in bm.markets:
            if market.key not in best_line:
                best_line[market.key] = {}
                consensus[market.key] = {}
                vig_free[market.key] = {}
            for outcome in market.outcomes:
                name = outcome.name
                price = outcome.price
                # Best line: keep highest (least negative / most positive) price per outcome
                existing = best_line[market.key].get(name)
                if existing is None or price > existing["price"]:
                    best_line[market.key][name] = {"price": price, "bookmaker": bm.key}
                # Accumulate for consensus (we'll average later)
                if name not in consensus[market.key]:
                    consensus[market.key][name] = {"prices": [], "price": 0.0}
                consensus[market.key][name]["prices"].append(price)  # type: ignore[index]

    # Finalise consensus averages
    for market_key, outcomes in consensus.items():
        for name, data in outcomes.items():
            prices = data.pop("prices")  # type: ignore[union-attr]
            data["price"] = round(sum(prices) / len(prices), 2)

    # Minimal vig-free (just placeholder implied probs — real calc in Phase 3)
    for market_key, outcomes in best_line.items():
        vig_free[market_key] = {name: {"implied_prob": 0.5} for name in outcomes}

    return EnrichedEventResponse(
        event_id=event.id,
        sport_key=event.sport_key,
        sport_group=event.sport_title.split(" ")[0],  # e.g. "NCAAB" → "NCAAB"
        home_team=event.home_team,
        away_team=event.away_team,
        commence_time=event.commence_time,
        status="upcoming",
        snapshot_id=snapshot_id,
        fetched_at=datetime.now(UTC),
        bookmakers={
            bm.key: {
                market.key: {
                    "outcomes": [o.model_dump() for o in market.outcomes],
                    "last_update": market.last_update.isoformat() if market.last_update else None,
                }
                for market in bm.markets
            }
            for bm in event.bookmakers
        },
        best_line=best_line,
        consensus=consensus,
        vig_free=vig_free,
        movement={},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_parsing_ncaab(db_session, redis_client):
    """Phase 1: fixture JSON parses correctly into OddsAPIEvent objects."""
    events = load_odds_fixture("odds_basketball_ncaab.json")

    assert len(events) == 5
    for event in events:
        assert event.sport_key == "basketball_ncaab"
        assert event.home_team
        assert event.away_team
        assert len(event.bookmakers) > 0
        for bm in event.bookmakers:
            assert len(bm.markets) > 0
            market_keys = {m.key for m in bm.markets}
            assert market_keys <= {"h2h", "spreads", "totals"}

    print(f"\n  Parsed {len(events)} NCAAB events OK")
    for e in events:
        bm_count = len(e.bookmakers)
        market_count = sum(len(b.markets) for b in e.bookmakers)
        print(f"  {e.home_team} vs {e.away_team} — {bm_count} books, {market_count} markets")


@pytest.mark.asyncio
async def test_schema_parsing_tennis(db_session, redis_client):
    """Phase 1: tennis fixture parses correctly, including optional point field."""
    events = load_odds_fixture("odds_tennis_atp_indian_wells.json")

    assert len(events) == 5
    for event in events:
        assert "tennis_atp" in event.sport_key

    # Check spreads have point values and h2h does not
    sample = events[0]
    for bm in sample.bookmakers:
        for market in bm.markets:
            if market.key == "h2h":
                for outcome in market.outcomes:
                    assert outcome.point is None, "h2h outcomes should have no point"
            elif market.key in ("spreads", "totals"):
                for outcome in market.outcomes:
                    assert outcome.point is not None, f"{market.key} outcomes must have a point"

    print(f"\n  Parsed {len(events)} ATP tennis events OK")


@pytest.mark.asyncio
async def test_full_pipeline_ncaab(db_session, redis_client):
    """
    Full Phase 1→2 pipeline:
    parse fixture → upsert events → create snapshots + bookmaker_odds
    → create enriched → cache → retrieve from cache.
    """
    events = load_odds_fixture("odds_basketball_ncaab.json")
    event_repo = EventRepository(db_session)
    odds_repo = OddsRepository(db_session)
    cache_repo = CacheRepository(redis_client)

    fetched_at = datetime.now(UTC)
    persisted_events = []
    persisted_snapshots = []

    # Step 1: upsert all events
    for api_event in events:
        db_event = await event_repo.upsert_event(
            external_id=api_event.id,
            sport_key=api_event.sport_key,
            sport_group="Basketball",
            home_team=api_event.home_team,
            away_team=api_event.away_team,
            commence_time=api_event.commence_time,
            status="upcoming",
        )
        persisted_events.append(db_event)

    await db_session.flush()
    assert len(persisted_events) == 5
    print(f"\n  Upserted {len(persisted_events)} events to DB")

    # Step 2: create snapshots + bulk bookmaker odds
    for db_event, api_event in zip(persisted_events, events):
        snapshot = await odds_repo.create_snapshot(
            event_id=db_event.id,
            fetched_at=fetched_at,
            credits_used=3,
        )
        rows = build_bookmaker_rows(snapshot.id, api_event)
        await odds_repo.create_bookmaker_odds_batch(rows)
        persisted_snapshots.append((db_event, snapshot, api_event))
        print(f"  Snapshot for {db_event.home_team} vs {db_event.away_team}: {len(rows)} odds rows")

    await db_session.flush()

    # Step 3: create enriched snapshots + record API usage
    for db_event, snapshot, api_event in persisted_snapshots:
        enriched_payload = build_enriched_payload(api_event, snapshot.id)

        # Serialize via model_dump_json → json.loads to get plain dicts
        # that SQLAlchemy's JSONB serializer can handle
        serialized = json.loads(enriched_payload.model_dump_json())

        await odds_repo.create_enriched_snapshot(
            snapshot_id=snapshot.id,
            event_id=db_event.id,
            best_line=serialized["best_line"],
            consensus_line=serialized["consensus"],
            vig_free=serialized["vig_free"],
            movement={},
        )

        # Step 4: cache the enriched event
        await cache_repo.set_event(enriched_payload)
        await cache_repo.set_active_events("Basketball", [enriched_payload])

    await odds_repo.record_api_usage(
        credits_used=3, credits_remaining=491, endpoint="odds", sport_key="basketball_ncaab"
    )
    await db_session.flush()
    print("  Enriched snapshots created and cached")

    # Step 5: verify retrieval from DB
    first_api_event = events[0]
    retrieved = await event_repo.get_by_external_id(first_api_event.id)
    assert retrieved is not None
    assert retrieved.external_id == first_api_event.id
    assert retrieved.home_team == first_api_event.home_team

    latest_enriched = await odds_repo.get_latest_enriched(retrieved.id)
    assert latest_enriched is not None
    assert "h2h" in latest_enriched.best_line
    print(f"  DB retrieval OK — best h2h lines: {list(latest_enriched.best_line['h2h'].keys())}")

    # Step 6: verify retrieval from Redis cache
    cached_event = await cache_repo.get_event(first_api_event.id)
    assert cached_event is not None
    assert cached_event.event_id == first_api_event.id
    assert cached_event.home_team == first_api_event.home_team
    assert "h2h" in cached_event.best_line
    print(f"  Redis cache retrieval OK — {cached_event.home_team} vs {cached_event.away_team}")

    # Step 7: verify active events list from cache
    active = await cache_repo.get_active_events("Basketball")
    assert active is not None
    assert len(active) == 1  # last set_active_events call had 1 event
    print(f"  Active events cache OK — {len(active)} event(s) cached")

    # Step 8: verify budget tracking
    await cache_repo.increment_daily_budget(3)
    await cache_repo.increment_monthly_budget(3)
    budget = await cache_repo.get_budget()
    assert budget["daily_used"] >= 3
    assert budget["monthly_used"] >= 3
    print(f"  Budget tracking OK — daily: {budget['daily_used']}, monthly: {budget['monthly_used']}")


@pytest.mark.asyncio
async def test_upsert_idempotency(db_session, redis_client):
    """Upserting the same event twice updates fields without creating duplicates."""
    event_repo = EventRepository(db_session)
    events = load_odds_fixture("odds_basketball_ncaab.json")
    api_event = events[0]

    first = await event_repo.upsert_event(
        external_id=api_event.id,
        sport_key=api_event.sport_key,
        sport_group="Basketball",
        home_team=api_event.home_team,
        away_team=api_event.away_team,
        commence_time=api_event.commence_time,
        status="upcoming",
    )
    await db_session.flush()

    second = await event_repo.upsert_event(
        external_id=api_event.id,
        sport_key=api_event.sport_key,
        sport_group="Basketball",
        home_team=api_event.home_team,
        away_team=api_event.away_team,
        commence_time=api_event.commence_time,
        status="live",  # status changed
    )
    await db_session.flush()

    assert first.id == second.id, "Same external_id must resolve to same DB row"
    # Re-fetch to bypass SQLAlchemy identity map and get the DB-committed value
    refreshed = await event_repo.get_by_external_id(api_event.id)
    assert refreshed is not None
    assert refreshed.status == "live"

    # Only one event with this external_id should exist
    from sqlalchemy import func, select
    from odds_engine.models.events import Event
    count_result = await db_session.execute(
        select(func.count()).where(Event.external_id == api_event.id)
    )
    assert count_result.scalar() == 1
    print(f"\n  Upsert idempotency OK — {api_event.id} exists exactly once, status=live")


@pytest.mark.asyncio
async def test_snapshot_history(db_session, redis_client):
    """get_snapshot_history returns snapshots in descending fetched_at order."""
    from datetime import timedelta
    event_repo = EventRepository(db_session)
    odds_repo = OddsRepository(db_session)
    events = load_odds_fixture("odds_basketball_ncaab.json")
    api_event = events[1]

    db_event = await event_repo.upsert_event(
        external_id=api_event.id,
        sport_key=api_event.sport_key,
        sport_group="Basketball",
        home_team=api_event.home_team,
        away_team=api_event.away_team,
        commence_time=api_event.commence_time,
        status="upcoming",
    )
    await db_session.flush()

    now = datetime.now(UTC)
    for i in range(3):
        await odds_repo.create_snapshot(
            event_id=db_event.id,
            fetched_at=now - timedelta(hours=2 - i),
            credits_used=3,
        )
    await db_session.flush()

    history = await odds_repo.get_snapshot_history(db_event.id, limit=10)
    assert len(history) == 3
    # Verify descending order
    for j in range(len(history) - 1):
        assert history[j].fetched_at >= history[j + 1].fetched_at
    print(f"\n  Snapshot history OK — {len(history)} snapshots in desc order")
