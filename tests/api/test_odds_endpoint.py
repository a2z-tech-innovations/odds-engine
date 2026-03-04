"""API tests for /api/v1/odds, /api/v1/fetch, and /api/v1/sports endpoints."""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from odds_engine.dependencies import get_cache_repo, get_odds_service
from odds_engine.exceptions import BudgetExhaustedError
from odds_engine.schemas.odds import ManualFetchResponse
from odds_engine.schemas.odds_api import OddsAPISport


@pytest.fixture
def mock_odds_service(app):
    svc = AsyncMock()
    app.dependency_overrides[get_odds_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_odds_service, None)


@pytest.fixture
def mock_cache_repo(app):
    cache = AsyncMock()
    app.dependency_overrides[get_cache_repo] = lambda: cache
    yield cache
    app.dependency_overrides.pop(get_cache_repo, None)


# ---------------------------------------------------------------------------
# GET /api/v1/odds/best
# ---------------------------------------------------------------------------


async def test_get_best_lines_returns_200(client: AsyncClient, mock_odds_service):
    mock_odds_service.get_best_lines.return_value = [
        {
            "event_id": "ext_001",
            "home_team": "Duke",
            "away_team": "UNC",
            "sport_key": "basketball_ncaab",
            "best_line": {"h2h": {"Duke": {"price": -150, "bookmaker": "draftkings"}}},
        }
    ]

    response = await client.get("/api/v1/odds/best")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["event_id"] == "ext_001"


async def test_get_best_lines_accepts_sport_group_filter(client: AsyncClient, mock_odds_service):
    mock_odds_service.get_best_lines.return_value = []

    response = await client.get("/api/v1/odds/best?sport_group=Basketball&market=h2h")

    assert response.status_code == 200
    mock_odds_service.get_best_lines.assert_called_once_with(
        sport_group="Basketball", market="h2h"
    )


# ---------------------------------------------------------------------------
# POST /api/v1/fetch
# ---------------------------------------------------------------------------


async def test_post_fetch_returns_200(client: AsyncClient, mock_odds_service, mock_cache_repo):
    mock_cache_repo.get_active_sports.return_value = None
    mock_odds_service.fetch_and_store.return_value = ManualFetchResponse(
        sport_key="basketball_ncaab",
        events_fetched=5,
        credits_used=3,
    )

    response = await client.post(
        "/api/v1/fetch",
        json={"sport_key": "basketball_ncaab"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["sport_key"] == "basketball_ncaab"
    assert data["events_fetched"] == 5
    assert data["credits_used"] == 3


async def test_post_fetch_returns_429_on_budget_exhausted(
    client: AsyncClient, mock_odds_service, mock_cache_repo
):
    mock_cache_repo.get_active_sports.return_value = None
    mock_odds_service.fetch_and_store.side_effect = BudgetExhaustedError(
        "Daily budget exceeded"
    )

    response = await client.post(
        "/api/v1/fetch",
        json={"sport_key": "basketball_ncaab"},
    )

    assert response.status_code == 429


async def test_post_fetch_derives_sport_group_from_cache(
    client: AsyncClient, mock_odds_service, mock_cache_repo
):
    sport = OddsAPISport(
        key="basketball_ncaab",
        group="Basketball",
        title="NCAAB",
        description="College Basketball",
        active=True,
        has_outrights=False,
    )
    mock_cache_repo.get_active_sports.return_value = [sport]
    mock_odds_service.fetch_and_store.return_value = ManualFetchResponse(
        sport_key="basketball_ncaab",
        events_fetched=2,
        credits_used=3,
    )

    response = await client.post(
        "/api/v1/fetch",
        json={"sport_key": "basketball_ncaab"},
    )

    assert response.status_code == 200
    mock_odds_service.fetch_and_store.assert_called_once_with("basketball_ncaab", "Basketball")


# ---------------------------------------------------------------------------
# GET /api/v1/sports
# ---------------------------------------------------------------------------


async def test_get_sports_returns_200(client: AsyncClient, mock_cache_repo):
    sport = OddsAPISport(
        key="basketball_ncaab",
        group="Basketball",
        title="NCAAB",
        description="College Basketball",
        active=True,
        has_outrights=False,
    )
    mock_cache_repo.get_active_sports.return_value = [sport]

    response = await client.get("/api/v1/sports")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["key"] == "basketball_ncaab"
    assert data[0]["group"] == "Basketball"


async def test_get_sports_returns_empty_on_cache_miss(client: AsyncClient, mock_cache_repo):
    mock_cache_repo.get_active_sports.return_value = None

    response = await client.get("/api/v1/sports")

    assert response.status_code == 200
    assert response.json() == []
