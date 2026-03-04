"""API tests for /api/v1/events endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from odds_engine.dependencies import get_event_service
from odds_engine.exceptions import EventNotFoundError
from odds_engine.models.enums import EventStatus
from odds_engine.schemas.events import EventListResponse, EventResponse


def make_event_response(**kwargs) -> EventResponse:
    defaults = dict(
        id=uuid.uuid4(),
        external_id="ext_abc123",
        sport_key="basketball_ncaab",
        sport_group="Basketball",
        home_team="Duke Blue Devils",
        away_team="UNC Tar Heels",
        commence_time=datetime(2026, 3, 10, 19, 0, tzinfo=UTC),
        status=EventStatus.upcoming,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return EventResponse(**defaults)


@pytest.fixture
def mock_event_service(app):
    svc = AsyncMock()
    app.dependency_overrides[get_event_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_event_service, None)


# ---------------------------------------------------------------------------
# GET /api/v1/events
# ---------------------------------------------------------------------------


async def test_get_events_returns_200(client: AsyncClient, mock_event_service):
    event1 = make_event_response(external_id="ext_001")
    event2 = make_event_response(external_id="ext_002")
    mock_event_service.get_events.return_value = EventListResponse(
        events=[event1, event2], total=2
    )

    response = await client.get("/api/v1/events")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["events"]) == 2


async def test_get_events_filters_passed_to_service(client: AsyncClient, mock_event_service):
    mock_event_service.get_events.return_value = EventListResponse(events=[], total=0)

    response = await client.get("/api/v1/events?sport_group=Basketball&status=upcoming")

    assert response.status_code == 200
    call_args = mock_event_service.get_events.call_args
    filters = call_args[0][0]
    assert filters.sport_group == "Basketball"
    assert filters.status == EventStatus.upcoming


# ---------------------------------------------------------------------------
# GET /api/v1/events/{event_id}
# ---------------------------------------------------------------------------


async def test_get_event_by_id_returns_200(client: AsyncClient, mock_event_service):
    event = make_event_response(external_id="ext_abc123")
    mock_event_service.get_event.return_value = event

    response = await client.get("/api/v1/events/ext_abc123")

    assert response.status_code == 200
    data = response.json()
    assert data["external_id"] == "ext_abc123"
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


@pytest.fixture
def mock_odds_repo(app):
    from odds_engine.dependencies import get_odds_repo

    repo = AsyncMock()
    app.dependency_overrides[get_odds_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_odds_repo, None)


def make_snapshot_orm(event_uuid):
    """Return a minimal ORM-like object for OddsSnapshotResponse construction."""
    snap = MagicMock()
    snap.id = uuid.uuid4()
    snap.event_id = event_uuid
    snap.fetched_at = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    snap.credits_used = 3
    return snap


async def test_get_event_history_returns_200(
    client: AsyncClient, mock_event_service, mock_odds_repo
):
    event = make_event_response(external_id="ext_abc")
    mock_event_service.get_event.return_value = event
    mock_odds_repo.get_snapshot_history.return_value = [
        make_snapshot_orm(event.id),
        make_snapshot_orm(event.id),
    ]

    response = await client.get("/api/v1/events/ext_abc/history")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["credits_used"] == 3


async def test_get_event_history_returns_404_on_unknown_event(
    client: AsyncClient, mock_event_service, mock_odds_repo
):
    mock_event_service.get_event.side_effect = EventNotFoundError("ext_missing")

    response = await client.get("/api/v1/events/ext_missing/history")

    assert response.status_code == 404
    mock_odds_repo.get_snapshot_history.assert_not_called()


async def test_get_event_history_forwards_limit_and_offset(
    client: AsyncClient, mock_event_service, mock_odds_repo
):
    event = make_event_response(external_id="ext_abc")
    mock_event_service.get_event.return_value = event
    mock_odds_repo.get_snapshot_history.return_value = []

    await client.get("/api/v1/events/ext_abc/history?limit=5&offset=10")

    mock_odds_repo.get_snapshot_history.assert_awaited_once_with(
        event_id=event.id, limit=5, offset=10
    )


async def test_get_event_history_empty_returns_empty_list(
    client: AsyncClient, mock_event_service, mock_odds_repo
):
    event = make_event_response(external_id="ext_abc")
    mock_event_service.get_event.return_value = event
    mock_odds_repo.get_snapshot_history.return_value = []

    response = await client.get("/api/v1/events/ext_abc/history")

    assert response.status_code == 200
    assert response.json() == []
