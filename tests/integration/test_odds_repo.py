"""Integration tests for OddsRepository.

Requires a real Postgres database (odds_engine_test).
The db_session fixture provides a transactional session that rolls back after each test.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from odds_engine.models.odds import BookmakerOdds
from odds_engine.repositories.odds_repo import OddsRepository

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Shared test data helpers
# ---------------------------------------------------------------------------

SAMPLE_BEST_LINE = {
    "h2h": {
        "Duke Blue Devils": {"price": -145.0, "bookmaker": "betmgm"},
        "UNC Tar Heels": {"price": 125.0, "bookmaker": "bovada"},
    }
}

SAMPLE_CONSENSUS = {
    "h2h": {
        "Duke Blue Devils": {"price": -148.5},
        "UNC Tar Heels": {"price": 122.0},
    }
}

SAMPLE_VIG_FREE = {
    "h2h": {
        "Duke Blue Devils": {"implied_prob": 0.597},
        "UNC Tar Heels": {"implied_prob": 0.403},
    }
}

SAMPLE_MOVEMENT: dict = {}


def _bookmaker_rows(snapshot_id: uuid.UUID, count: int = 6) -> list[dict]:
    bookmakers = ["draftkings", "fanduel", "betmgm", "caesars", "bovada", "betonlineag"]
    return [
        {
            "id": uuid.uuid4(),
            "snapshot_id": snapshot_id,
            "bookmaker_key": bk,
            "market_key": "h2h",
            "outcome_name": "Duke Blue Devils",
            "outcome_price": -150.0,
            "outcome_point": None,
            "last_update": datetime.now(UTC),
        }
        for bk in bookmakers[:count]
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_create_snapshot(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    event_id = uuid.uuid4()
    fetched_at = datetime.now(UTC)

    snapshot = await repo.create_snapshot(
        event_id=event_id,
        fetched_at=fetched_at,
        credits_used=3,
    )
    await db_session.flush()

    assert snapshot.id is not None
    assert snapshot.event_id == event_id
    assert snapshot.credits_used == 3

    # Verify it round-trips via a fresh query
    from odds_engine.models.odds import OddsSnapshot

    result = await db_session.get(OddsSnapshot, snapshot.id)
    assert result is not None
    assert result.event_id == event_id
    assert result.credits_used == 3


async def test_create_snapshot_without_credits(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    event_id = uuid.uuid4()

    snapshot = await repo.create_snapshot(
        event_id=event_id,
        fetched_at=datetime.now(UTC),
    )
    await db_session.flush()

    assert snapshot.credits_used is None


async def test_create_bookmaker_odds_batch(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    event_id = uuid.uuid4()
    snapshot = await repo.create_snapshot(event_id=event_id, fetched_at=datetime.now(UTC))
    await db_session.flush()

    rows = _bookmaker_rows(snapshot.id, count=6)
    await repo.create_bookmaker_odds_batch(rows)
    await db_session.flush()

    result = await db_session.execute(
        select(BookmakerOdds).where(BookmakerOdds.snapshot_id == snapshot.id)
    )
    inserted = result.scalars().all()
    assert len(inserted) == 6
    bookmaker_keys = {r.bookmaker_key for r in inserted}
    assert bookmaker_keys == {"draftkings", "fanduel", "betmgm", "caesars", "bovada", "betonlineag"}


async def test_create_bookmaker_odds_batch_empty(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    # Should not raise; empty batch is a no-op
    await repo.create_bookmaker_odds_batch([])


async def test_create_enriched_snapshot(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    event_id = uuid.uuid4()
    snapshot = await repo.create_snapshot(event_id=event_id, fetched_at=datetime.now(UTC))
    await db_session.flush()

    enriched = await repo.create_enriched_snapshot(
        snapshot_id=snapshot.id,
        event_id=event_id,
        best_line=SAMPLE_BEST_LINE,
        consensus_line=SAMPLE_CONSENSUS,
        vig_free=SAMPLE_VIG_FREE,
        movement=SAMPLE_MOVEMENT,
    )
    await db_session.flush()

    assert enriched.id is not None
    assert enriched.snapshot_id == snapshot.id
    assert enriched.event_id == event_id

    # Verify JSONB round-trip
    from odds_engine.models.odds import EnrichedSnapshot

    result = await db_session.get(EnrichedSnapshot, enriched.id)
    assert result is not None
    assert result.best_line == SAMPLE_BEST_LINE
    assert result.consensus_line == SAMPLE_CONSENSUS
    assert result.vig_free == SAMPLE_VIG_FREE
    assert result.movement == SAMPLE_MOVEMENT


async def test_get_latest_enriched_returns_most_recent(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    event_id = uuid.uuid4()

    # Create two snapshots with different fetched_at times
    now = datetime.now(UTC)
    snap1 = await repo.create_snapshot(event_id=event_id, fetched_at=now - timedelta(minutes=30))
    snap2 = await repo.create_snapshot(event_id=event_id, fetched_at=now)
    await db_session.flush()

    await repo.create_enriched_snapshot(
        snapshot_id=snap1.id,
        event_id=event_id,
        best_line={"h2h": {"Team A": {"price": -120.0, "bookmaker": "draftkings"}}},
        consensus_line={},
        vig_free={},
        movement={},
    )
    await db_session.flush()

    enriched2 = await repo.create_enriched_snapshot(
        snapshot_id=snap2.id,
        event_id=event_id,
        best_line={"h2h": {"Team A": {"price": -115.0, "bookmaker": "fanduel"}}},
        consensus_line={},
        vig_free={},
        movement={},
    )
    await db_session.flush()

    latest = await repo.get_latest_enriched(event_id)
    assert latest is not None
    assert latest.id == enriched2.id
    # The latest should have the more recent best_line
    assert latest.best_line["h2h"]["Team A"]["price"] == -115.0


async def test_get_latest_enriched_returns_none_when_no_data(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    result = await repo.get_latest_enriched(uuid.uuid4())
    assert result is None


async def test_get_snapshot_history_ordered_desc(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    event_id = uuid.uuid4()
    now = datetime.now(UTC)

    snap1 = await repo.create_snapshot(event_id=event_id, fetched_at=now - timedelta(hours=2))
    await repo.create_snapshot(event_id=event_id, fetched_at=now - timedelta(hours=1))
    snap3 = await repo.create_snapshot(event_id=event_id, fetched_at=now)
    await db_session.flush()

    history = await repo.get_snapshot_history(event_id)
    assert len(history) == 3

    # Should be ordered descending by fetched_at
    fetched_times = [s.fetched_at for s in history]
    assert fetched_times == sorted(fetched_times, reverse=True)

    # Most recent first
    assert history[0].id == snap3.id
    assert history[2].id == snap1.id


async def test_get_snapshot_history_respects_limit_offset(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)
    event_id = uuid.uuid4()
    now = datetime.now(UTC)

    for i in range(5):
        await repo.create_snapshot(
            event_id=event_id, fetched_at=now - timedelta(hours=5 - i)
        )
    await db_session.flush()

    page1 = await repo.get_snapshot_history(event_id, limit=2, offset=0)
    page2 = await repo.get_snapshot_history(event_id, limit=2, offset=2)

    assert len(page1) == 2
    assert len(page2) == 2
    # Pages should not overlap
    page1_ids = {s.id for s in page1}
    page2_ids = {s.id for s in page2}
    assert page1_ids.isdisjoint(page2_ids)


async def test_record_api_usage(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)

    usage = await repo.record_api_usage(
        credits_used=3,
        credits_remaining=497,
        endpoint="odds",
        sport_key="basketball_ncaab",
    )
    await db_session.flush()

    assert usage.id is not None
    assert usage.credits_used == 3
    assert usage.credits_remaining == 497
    assert usage.endpoint == "odds"
    assert usage.sport_key == "basketball_ncaab"

    from odds_engine.models.odds import ApiUsage

    result = await db_session.get(ApiUsage, usage.id)
    assert result is not None
    assert result.credits_used == 3
    assert result.credits_remaining == 497


async def test_record_api_usage_without_sport_key(db_session: AsyncSession) -> None:
    repo = OddsRepository(db_session)

    usage = await repo.record_api_usage(
        credits_used=0,
        credits_remaining=500,
        endpoint="sports",
    )
    await db_session.flush()

    assert usage.sport_key is None
    assert usage.endpoint == "sports"
