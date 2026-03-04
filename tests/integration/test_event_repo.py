"""Integration tests for EventRepository.

Requires a real Postgres database (odds_engine_test).
The db_session fixture provides a transactional session that rolls back after each test.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from odds_engine.repositories.event_repo import EventRepository
from odds_engine.schemas.events import EventFilterParams

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kwargs(**overrides) -> dict:
    base = {
        "external_id": f"ext_{uuid.uuid4().hex[:12]}",
        "sport_key": "basketball_ncaab",
        "sport_group": "Basketball",
        "home_team": "Duke Blue Devils",
        "away_team": "UNC Tar Heels",
        "commence_time": datetime.now(UTC) + timedelta(hours=24),
        "status": "upcoming",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_upsert_creates_new_event(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)
    kwargs = _make_kwargs()

    event = await repo.upsert_event(**kwargs)
    await db_session.flush()

    fetched = await repo.get_by_external_id(kwargs["external_id"])
    assert fetched is not None
    assert fetched.external_id == kwargs["external_id"]
    assert fetched.sport_key == "basketball_ncaab"
    assert fetched.sport_group == "Basketball"
    assert fetched.home_team == "Duke Blue Devils"
    assert fetched.away_team == "UNC Tar Heels"
    assert fetched.status == "upcoming"
    assert fetched.id == event.id


async def test_upsert_updates_existing_event(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)
    kwargs = _make_kwargs()

    first = await repo.upsert_event(**kwargs)
    await db_session.flush()

    # Same external_id but different status
    updated_kwargs = {**kwargs, "status": "live"}
    second = await repo.upsert_event(**updated_kwargs)
    await db_session.flush()

    fetched = await repo.get_by_external_id(kwargs["external_id"])
    assert fetched is not None
    assert fetched.status == "live"
    # IDs should be the same row
    assert first.id == second.id


async def test_get_by_external_id_returns_none_for_missing(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)
    result = await repo.get_by_external_id("does_not_exist_xyz")
    assert result is None


async def test_get_by_id_returns_event(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)
    kwargs = _make_kwargs()
    event = await repo.upsert_event(**kwargs)
    await db_session.flush()

    fetched = await repo.get_by_id(event.id)
    assert fetched is not None
    assert fetched.id == event.id


async def test_get_by_id_returns_none_for_missing(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)
    result = await repo.get_by_id(uuid.uuid4())
    assert result is None


async def test_get_many_filters_by_sport_group(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)

    await repo.upsert_event(**_make_kwargs(sport_group="Basketball", sport_key="basketball_ncaab"))
    await repo.upsert_event(**_make_kwargs(sport_group="Basketball", sport_key="basketball_ncaab"))
    await repo.upsert_event(
        **_make_kwargs(sport_group="Tennis", sport_key="tennis_atp_indian_wells")
    )
    await db_session.flush()

    filters = EventFilterParams(sport_group="Basketball")
    results = await repo.get_many(filters)
    assert all(e.sport_group == "Basketball" for e in results)
    assert len(results) >= 2

    tennis_filters = EventFilterParams(sport_group="Tennis")
    tennis_results = await repo.get_many(tennis_filters)
    assert all(e.sport_group == "Tennis" for e in tennis_results)
    assert len(tennis_results) >= 1


async def test_get_many_filters_by_status(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)

    await repo.upsert_event(**_make_kwargs(status="upcoming"))
    await repo.upsert_event(**_make_kwargs(status="live"))
    await repo.upsert_event(**_make_kwargs(status="completed"))
    await db_session.flush()

    filters = EventFilterParams(status="live")  # type: ignore[arg-type]
    results = await repo.get_many(filters)
    assert all(e.status == "live" for e in results)
    assert len(results) >= 1


async def test_get_many_filters_by_commence_range(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)

    now = datetime.now(UTC)
    # Event in 1 hour
    await repo.upsert_event(**_make_kwargs(commence_time=now + timedelta(hours=1)))
    # Event in 48 hours
    await repo.upsert_event(**_make_kwargs(commence_time=now + timedelta(hours=48)))
    # Event in 96 hours
    await repo.upsert_event(**_make_kwargs(commence_time=now + timedelta(hours=96)))
    await db_session.flush()

    # Filter: only events between now and 50 hours from now
    filters = EventFilterParams(
        commence_from=now,
        commence_to=now + timedelta(hours=50),
    )
    results = await repo.get_many(filters)
    assert len(results) >= 2
    for event in results:
        assert event.commence_time >= now
        assert event.commence_time <= now + timedelta(hours=50)


async def test_get_many_returns_ordered_by_commence_time(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)
    now = datetime.now(UTC)

    await repo.upsert_event(
        **_make_kwargs(
            sport_key="basketball_ncaab",
            sport_group="Basketball",
            commence_time=now + timedelta(hours=5),
        )
    )
    await repo.upsert_event(
        **_make_kwargs(
            sport_key="basketball_ncaab",
            sport_group="Basketball",
            commence_time=now + timedelta(hours=1),
        )
    )
    await db_session.flush()

    filters = EventFilterParams(sport_group="Basketball")
    results = await repo.get_many(filters)
    times = [e.commence_time for e in results]
    assert times == sorted(times)


async def test_count_matches_get_many(db_session: AsyncSession) -> None:
    repo = EventRepository(db_session)

    sport_key = f"basketball_ncaab_{uuid.uuid4().hex[:6]}"
    await repo.upsert_event(**_make_kwargs(sport_key=sport_key, sport_group="Basketball"))
    await repo.upsert_event(**_make_kwargs(sport_key=sport_key, sport_group="Basketball"))
    await repo.upsert_event(**_make_kwargs(sport_key=sport_key, sport_group="Basketball"))
    await db_session.flush()

    filters = EventFilterParams(sport_key=sport_key)
    results = await repo.get_many(filters)
    count = await repo.count(filters)

    assert count == len(results)
    assert count == 3
