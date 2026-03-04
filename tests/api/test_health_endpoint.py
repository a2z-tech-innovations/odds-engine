"""API tests for /api/v1/health and /api/v1/budget endpoints."""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from odds_engine.dependencies import get_cache_repo


@pytest.fixture
def mock_cache_repo(app):
    cache = AsyncMock()
    app.dependency_overrides[get_cache_repo] = lambda: cache
    yield cache
    app.dependency_overrides.pop(get_cache_repo, None)


# ---------------------------------------------------------------------------
# GET /api/v1/health
# ---------------------------------------------------------------------------


async def test_health_returns_ok_structure(client: AsyncClient, mock_cache_repo):
    mock_cache_repo.redis.ping = AsyncMock()
    mock_cache_repo.get_budget = AsyncMock(return_value={"daily_used": 9, "monthly_used": 9})

    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert "database" in data
    assert "redis" in data
    assert "budget" in data
    assert "version" in data


async def test_health_includes_budget_data(client: AsyncClient, mock_cache_repo):
    mock_cache_repo.redis.ping = AsyncMock()
    mock_cache_repo.get_budget = AsyncMock(
        return_value={"daily_used": 15, "monthly_used": 42}
    )

    response = await client.get("/api/v1/health")

    data = response.json()
    assert data["budget"]["daily_used"] == 15
    assert data["budget"]["monthly_used"] == 42


async def test_health_no_auth_required(app):
    """Health endpoint must be accessible without an API key."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as unauthenticated_client:
        response = await unauthenticated_client.get("/api/v1/health")

    assert response.status_code == 200


async def test_health_degraded_when_redis_down(client: AsyncClient, mock_cache_repo):
    mock_cache_repo.redis.ping = AsyncMock(side_effect=Exception("Redis down"))
    mock_cache_repo.get_budget = AsyncMock(return_value={"daily_used": 0, "monthly_used": 0})

    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["redis"] == "error"


# ---------------------------------------------------------------------------
# GET /api/v1/budget
# ---------------------------------------------------------------------------


async def test_budget_returns_credit_usage(client: AsyncClient, mock_cache_repo):
    mock_cache_repo.get_budget = AsyncMock(
        return_value={"daily_used": 9, "monthly_used": 9}
    )

    response = await client.get("/api/v1/budget")

    assert response.status_code == 200
    data = response.json()
    assert data["daily_used"] == 9
    assert data["monthly_used"] == 9


async def test_budget_requires_auth(app):
    """Budget endpoint must reject requests without a valid API key."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as unauthenticated_client:
        response = await unauthenticated_client.get("/api/v1/budget")

    assert response.status_code == 401
