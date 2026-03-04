"""API tests for /api/v1/events endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from odds_engine.dependencies import get_event_repo, get_event_service, get_odds_repo
from odds_engine.exceptions import EventNotFoundError
from odds_engine.models.enums import EventStatus
from odds_engine.schemas.enriched import EnrichedEventResponse


def make_enriched_event(**kwargs) -> EnrichedEventResponse:
    defaults = dict(
        event_id="ext_abc123",
        sport_key="basketball_ncaab",
        sport_group="Basketball",
        home_team="Duke Blue Devils",
        away_team="UNC Tar Heels",
        commence_time=datetime(2026, 3, 10, 19, 0, tzinfo=UTC),
        status="upcoming",
        snapshot_id=uuid.uuid4(),
        fetched_at=datetime.now(UTC),
        bookmakers={"draftkings": {"h2h": {"outcomes": [], "last_update": None}}},
        best_line={"h2h": {"Duke Blue Devils": {"price": -150.0, "bookmaker": "draftkings"}}},
        consensus={"h2h": {"Duke Blue Devils": {"price": -148.5}}},
        vig_free={"h2h": {"Duke Blue Devils": {"implied_prob": 0.597}}},
        movement={},
    )
    defaults.update(kwargs)
    return EnrichedEventResponse(**defaults)


@pytest.fixture
def mock_event_service(app):
    svc = AsyncMock()
    app.dependency_overrides[get_event_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_event_service, None)


@pytest.fixture
def mock_event_repo(app):
    repo = AsyncMock()
    app.dependency_overrides[get_event_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_event_repo, None)


@pytest.fixture
def mock_odds_repo(app):
    repo = AsyncMock()
    app.dependency_overrides[get_odds_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_odds_repo, None)


# ---------------------------------------------------------------------------
# GET /api/v1/events
# ---------------------------------------------------------------------------


async def test_get_events_returns_200_with_enriched_data(
    client: AsyncClient, mock_event_service
):
    event1 = make_enriched_event(event_id="ext_001")
    event2 = make_enriched_event(event_id="ext_002")
    mock_event_service.get_events.return_value = [event1, event2]

    response = await client.get("/api/v1/events")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["event_id"] == "ext_001"
    # Verify odds data is present in the response
    assert "bookmakers" in data[0]
    assert "best_line" in data[0]
    assert "consensus" in data[0]
    assert "vig_free" in data[0]


async def test_get_events_filters_passed_to_service(client: AsyncClient, mock_event_service):
    mock_event_service.get_events.return_value = []

    response = await client.get("/api/v1/events?sport_group=Basketball&status=upcoming")

    assert response.status_code == 200
    filters = mock_event_service.get_events.call_args[0][0]
    assert filters.sport_group == "Basketball"
    assert filters.status == EventStatus.upcoming


async def test_get_events_returns_empty_list(client: AsyncClient, mock_event_service):
    mock_event_service.get_events.return_value = []

    response = await client.get("/api/v1/events")

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /api/v1/events/{event_id}
# ---------------------------------------------------------------------------


async def test_get_event_by_id_returns_enriched_data(client: AsyncClient, mock_event_service):
    event = make_enriched_event(event_id="ext_abc123")
    mock_event_service.get_event.return_value = event

    response = await client.get("/api/v1/events/ext_abc123")

    assert response.status_code == 200
    data = response.json()
    assert data["event_id"] == "ext_abc123"
    assert "bookmakers" in data
    assert "best_line" in data
    mock_event_service.get_event.assert_called_once_with("ext_abc123")


async def test_get_event_by_id_returns_404_on_not_found(client: AsyncClient, mock_event_service):
    mock_event_service.get_event.side_effect = EventNotFoundError("ext_missing")

    response = await client.get("/api/v1/events/ext_missing")

    assert response.status_code == 404
    assert "ext_missing" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


async def test_get_events_requires_auth(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as unauthenticated_client:
        response = await unauthenticated_client.get("/api/v1/events")

    assert response.status_code == 401


async def test_health_endpoint_no_auth_required(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as unauthenticated_client:
        response = await unauthenticated_client.get("/api/v1/health")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/events/{event_id}/history
# ---------------------------------------------------------------------------


def make_snapshot_orm(event_uuid):
    snap = MagicMock()
    snap.id = uuid.uuid4()
    snap.event_id = event_uuid
    snap.fetched_at = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    snap.credits_used = 3
    return snap


async def test_get_event_history_returns_200(
    client: AsyncClient, mock_event_repo, mock_odds_repo
):
    db_event = MagicMock()
    db_event.id = uuid.uuid4()
    mock_event_repo.get_by_external_id.return_value = db_event
    mock_odds_repo.get_snapshot_history.return_value = [
        make_snapshot_orm(db_event.id),
        make_snapshot_orm(db_event.id),
    ]

    response = await client.get("/api/v1/events/ext_abc/history")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["credits_used"] == 3


async def test_get_event_history_returns_404_on_unknown_event(
    client: AsyncClient, mock_event_repo, mock_odds_repo
):
    mock_event_repo.get_by_external_id.return_value = None

    response = await client.get("/api/v1/events/ext_missing/history")

    assert response.status_code == 404
    mock_odds_repo.get_snapshot_history.assert_not_called()


async def test_get_event_history_forwards_limit_and_offset(
    client: AsyncClient, mock_event_repo, mock_odds_repo
):
    db_event = MagicMock()
    db_event.id = uuid.uuid4()
    mock_event_repo.get_by_external_id.return_value = db_event
    mock_odds_repo.get_snapshot_history.return_value = []

    await client.get("/api/v1/events/ext_abc/history?limit=5&offset=10")

    mock_odds_repo.get_snapshot_history.assert_awaited_once_with(
        event_id=db_event.id, limit=5, offset=10
    )


async def test_get_event_history_empty_returns_empty_list(
    client: AsyncClient, mock_event_repo, mock_odds_repo
):
    db_event = MagicMock()
    db_event.id = uuid.uuid4()
    mock_event_repo.get_by_external_id.return_value = db_event
    mock_odds_repo.get_snapshot_history.return_value = []

    response = await client.get("/api/v1/events/ext_abc/history")

    assert response.status_code == 200
    assert response.json() == []
