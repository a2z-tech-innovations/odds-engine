"""WebSocket endpoint tests."""

import json
import time

import pytest
import redis as sync_redis
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from odds_engine.api.v1 import ws as ws_module
from odds_engine.config import Settings
from odds_engine.main import create_app
from tests.conftest import TEST_REDIS_URL, TEST_SETTINGS_KWARGS


def make_app() -> object:
    """Create a test app with the WebSocket router registered."""
    settings = Settings(**TEST_SETTINGS_KWARGS)
    application = create_app(settings=settings)
    # Include the WebSocket router at /api/v1 prefix
    application.include_router(ws_module.router, prefix="/api/v1")
    return application


def _connect_and_receive(test_client: TestClient, url: str) -> None:
    """Helper: connect to WebSocket and attempt to receive one message."""
    with test_client.websocket_connect(url) as ws:
        ws.receive_text()


def test_ws_rejects_invalid_api_key() -> None:
    """WebSocket connection with wrong api_key is closed with code 4001."""
    app = make_app()
    with TestClient(app) as test_client, pytest.raises(WebSocketDisconnect):
        _connect_and_receive(test_client, "/api/v1/ws?api_key=wrong_key")


def test_ws_accepts_valid_api_key_no_sport_group() -> None:
    """WebSocket connection with valid api_key and no sport_group is accepted."""
    app = make_app()
    with TestClient(app) as test_client, test_client.websocket_connect(
        "/api/v1/ws?api_key=test_secret"
    ) as ws:
        assert ws is not None


def test_ws_accepts_valid_api_key_with_sport_group() -> None:
    """WebSocket connection with valid api_key and sport_group filter is accepted."""
    app = make_app()
    with TestClient(app) as test_client, test_client.websocket_connect(
        "/api/v1/ws?api_key=test_secret&sport_group=Basketball"
    ) as ws:
        assert ws is not None


def test_ws_missing_api_key_rejected() -> None:
    """WebSocket connection without api_key query param is rejected (422 Unprocessable)."""
    app = make_app()
    with TestClient(app) as test_client, pytest.raises(WebSocketDisconnect):
        _connect_and_receive(test_client, "/api/v1/ws")


def test_ws_receives_published_message() -> None:
    """Message published to odds:updates:all is forwarded to connected WebSocket client."""
    app = make_app()
    payload = json.dumps({"event_id": "test123", "sport_group": "Basketball"})

    with TestClient(app) as test_client, test_client.websocket_connect(
        "/api/v1/ws?api_key=test_secret"
    ) as ws:
        # Allow a brief moment for subscription to be established
        time.sleep(0.1)

        r = sync_redis.Redis.from_url(TEST_REDIS_URL)
        r.publish("odds:updates:all", payload)
        r.close()

        data = ws.receive_text()
        assert "test123" in data


def test_ws_sport_group_filter_receives_matching_channel() -> None:
    """Message published to odds:updates:Basketball reaches Basketball-filtered subscriber."""
    app = make_app()
    payload = json.dumps({"event_id": "bball_event", "sport_group": "Basketball"})

    with TestClient(app) as test_client, test_client.websocket_connect(
        "/api/v1/ws?api_key=test_secret&sport_group=Basketball"
    ) as ws:
        time.sleep(0.1)

        r = sync_redis.Redis.from_url(TEST_REDIS_URL)
        r.publish("odds:updates:Basketball", payload)
        r.close()

        data = ws.receive_text()
        assert "bball_event" in data
