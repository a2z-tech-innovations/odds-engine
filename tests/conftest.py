"""
Shared test fixtures and factory-boy factories.

Integration tests (tests/integration/) require the real Postgres/Redis server.
The test database is odds_engine_test (DB index 2 for Redis). These are created
automatically when integration tests first run against setup_db.

Unit tests need no fixtures from here — they work purely with factories and
JSON fixture files in tests/fixtures/odds_api/.

API tests use the FastAPI async test client with test DB and mocked or real services.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import factory
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from odds_engine.config import get_settings
from odds_engine.main import create_app
from odds_engine.models.database import Base
from odds_engine.models.enums import EventStatus, MarketKey
from odds_engine.models.events import Event
from odds_engine.models.odds import ApiUsage, BookmakerOdds, EnrichedSnapshot, OddsSnapshot

# ---------------------------------------------------------------------------
# Test database / Redis config — derived from real settings, separate DB
# ---------------------------------------------------------------------------

_settings = get_settings()

TEST_SETTINGS_KWARGS = dict(
    odds_api_key=_settings.odds_api_key,
    basestar_address=_settings.basestar_address,
    db_name="odds_engine_test",
    db_user=_settings.db_user,
    db_password=_settings.db_password,
    db_port=_settings.db_port,
    cache_user=_settings.cache_user,
    cache_password=_settings.cache_password,
    cache_port=_settings.cache_port,
    cache_db=2,  # DB index 2 reserved for tests
    api_secret_key="test_secret",
    scheduler_enabled=False,
    log_format="console",
)

TEST_DATABASE_URL = (
    f"postgresql+asyncpg://{_settings.db_user}:{_settings.db_password}"
    f"@{_settings.basestar_address}:{_settings.db_port}/odds_engine_test"
)
TEST_REDIS_URL = (
    f"redis://{_settings.cache_user}:{_settings.cache_password}"
    f"@{_settings.basestar_address}:{_settings.cache_port}/2"
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "odds_api"


# ---------------------------------------------------------------------------
# JSON fixture helpers
# ---------------------------------------------------------------------------


def load_fixture(filename: str) -> list | dict:
    return json.loads((FIXTURES_DIR / filename).read_text())


# ---------------------------------------------------------------------------
# factory-boy factories (in-memory — no DB session needed)
# These build valid ORM model instances for use in unit and integration tests.
# For integration tests, add the instance to the db_session and flush/commit.
# ---------------------------------------------------------------------------


class EventFactory(factory.Factory):
    class Meta:
        model = Event

    id = factory.LazyFunction(uuid.uuid4)
    external_id = factory.LazyFunction(lambda: f"ext_{uuid.uuid4().hex[:12]}")
    sport_key = "basketball_ncaab"
    sport_group = "Basketball"
    home_team = "Duke Blue Devils"
    away_team = "UNC Tar Heels"
    commence_time = factory.LazyFunction(lambda: datetime.now(UTC) + timedelta(hours=24))
    status = EventStatus.upcoming
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))
    updated_at = factory.LazyFunction(lambda: datetime.now(UTC))


class TennisEventFactory(EventFactory):
    sport_key = "tennis_atp_indian_wells"
    sport_group = "Tennis"
    home_team = "Novak Djokovic"
    away_team = "Carlos Alcaraz"


class OddsSnapshotFactory(factory.Factory):
    class Meta:
        model = OddsSnapshot

    id = factory.LazyFunction(uuid.uuid4)
    event_id = factory.LazyFunction(uuid.uuid4)
    fetched_at = factory.LazyFunction(lambda: datetime.now(UTC))
    credits_used = 3
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))


class BookmakerOddsFactory(factory.Factory):
    class Meta:
        model = BookmakerOdds

    id = factory.LazyFunction(uuid.uuid4)
    snapshot_id = factory.LazyFunction(uuid.uuid4)
    bookmaker_key = "draftkings"
    market_key = MarketKey.h2h
    outcome_name = "Duke Blue Devils"
    outcome_price = -150.0
    outcome_point = None
    last_update = factory.LazyFunction(lambda: datetime.now(UTC))
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))


class EnrichedSnapshotFactory(factory.Factory):
    class Meta:
        model = EnrichedSnapshot

    id = factory.LazyFunction(uuid.uuid4)
    snapshot_id = factory.LazyFunction(uuid.uuid4)
    event_id = factory.LazyFunction(uuid.uuid4)
    best_line = factory.LazyFunction(
        lambda: {
            "h2h": {
                "Duke Blue Devils": {"price": -145.0, "bookmaker": "betmgm"},
                "UNC Tar Heels": {"price": 125.0, "bookmaker": "bovada"},
            }
        }
    )
    consensus_line = factory.LazyFunction(
        lambda: {
            "h2h": {
                "Duke Blue Devils": {"price": -148.5},
                "UNC Tar Heels": {"price": 122.0},
            }
        }
    )
    vig_free = factory.LazyFunction(
        lambda: {
            "h2h": {
                "Duke Blue Devils": {"implied_prob": 0.597},
                "UNC Tar Heels": {"implied_prob": 0.403},
            }
        }
    )
    movement = factory.LazyFunction(lambda: {})
    computed_at = factory.LazyFunction(lambda: datetime.now(UTC))


class ApiUsageFactory(factory.Factory):
    class Meta:
        model = ApiUsage

    id = factory.LazyFunction(uuid.uuid4)
    credits_used = 3
    credits_remaining = 491
    sport_key = "basketball_ncaab"
    endpoint = "odds"
    recorded_at = factory.LazyFunction(lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def engine():
    return create_async_engine(TEST_DATABASE_URL, echo=False, pool_pre_ping=True)


@pytest_asyncio.fixture(scope="session")
async def setup_db(engine):
    """Create all tables once per test session, drop on teardown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(engine, setup_db) -> AsyncSession:
    """
    Transactional session that rolls back after each test.
    Tests don't pollute each other.
    """
    async with engine.connect() as conn:
        await conn.begin()
        session_factory = async_sessionmaker(conn, expire_on_commit=False)
        async with session_factory() as session:
            yield session
        await conn.rollback()


# ---------------------------------------------------------------------------
# Redis fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def redis_client() -> Redis:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


# ---------------------------------------------------------------------------
# FastAPI test app + HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings():
    from odds_engine.config import Settings

    return Settings(**TEST_SETTINGS_KWARGS)


@pytest_asyncio.fixture
async def app(test_settings) -> FastAPI:
    application = create_app(settings=test_settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test_secret"},
    ) as c:
        yield c
