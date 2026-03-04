"""
Shared test fixtures.

Integration tests (tests/integration/) need real Postgres and Redis.
Set TEST_DATABASE_URL and TEST_REDIS_URL env vars, or they default to
localhost with the odds_engine_test database.

API tests use the FastAPI async test client with real services + test DB.
Unit tests need no fixtures from here.
"""

import os

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from odds_engine.config import Settings
from odds_engine.main import create_app
from odds_engine.models.database import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/odds_engine_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1")


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
    Provide a transactional session that rolls back after each test,
    so tests don't pollute each other.
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
def test_settings() -> Settings:
    return Settings(
        odds_api_key="test_key",
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        api_secret_key="test_secret",
        scheduler_enabled=False,
        log_format="console",
    )


@pytest_asyncio.fixture
async def app(test_settings: Settings) -> FastAPI:
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
