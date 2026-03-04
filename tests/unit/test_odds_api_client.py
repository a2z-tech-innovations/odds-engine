"""Unit tests for OddsAPIClient — all I/O is mocked via fixture JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from odds_engine.clients.odds_api import OddsAPIClient, OddsAPIUsage
from odds_engine.exceptions import OddsAPIError
from odds_engine.schemas.odds_api import OddsAPIEvent, OddsAPISport

FIXTURES = Path(__file__).parent.parent / "fixtures" / "odds_api"

API_KEY = "test-key"
BASE_URL = "https://api.the-odds-api.com/v4"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_response(
    fixture_file: str,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a synthetic httpx.Response from a fixture JSON file."""
    data = json.loads((FIXTURES / fixture_file).read_text())
    return httpx.Response(
        status_code=status,
        json=data,
        headers=headers or {},
    )


def make_error_response(status: int, text: str = "") -> httpx.Response:
    """Build a synthetic httpx.Response with a plain-text body (for error paths)."""
    return httpx.Response(
        status_code=status,
        text=text,
    )


def make_empty_response(headers: dict[str, str] | None = None) -> httpx.Response:
    """Build a synthetic httpx.Response with an empty JSON array body."""
    return httpx.Response(
        status_code=200,
        json=[],
        headers=headers or {},
    )


def build_client(mock_get_return: httpx.Response) -> OddsAPIClient:
    """Return an OddsAPIClient whose underlying http_client.get is pre-mocked."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(return_value=mock_get_return)
    return OddsAPIClient(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sports_returns_active_sports() -> None:
    """get_sports() should parse the /sports fixture and return a list of OddsAPISport."""
    client = build_client(make_response("sports.json"))

    sports = await client.get_sports(active_only=True)

    assert isinstance(sports, list)
    assert len(sports) > 0
    assert all(isinstance(s, OddsAPISport) for s in sports)

    # The fixture contains basketball_ncaab — verify it is present
    keys = [s.key for s in sports]
    assert "basketball_ncaab" in keys

    # Verify the API key is forwarded in the request params
    http_client = client._http_client
    call_kwargs = http_client.get.call_args
    assert call_kwargs is not None
    assert call_kwargs.kwargs["params"]["apiKey"] == API_KEY


@pytest.mark.asyncio
async def test_get_sports_includes_all_param_when_not_active_only() -> None:
    """When active_only=False, the 'all' query param must be forwarded."""
    client = build_client(make_response("sports.json"))

    await client.get_sports(active_only=False)

    http_client = client._http_client
    call_kwargs = http_client.get.call_args
    assert call_kwargs.kwargs["params"].get("all") == "true"


@pytest.mark.asyncio
async def test_get_events_returns_events_without_bookmakers() -> None:
    """get_events() should parse the /events fixture; bookmakers list must be empty."""
    client = build_client(make_response("events_basketball_ncaab.json"))

    events = await client.get_events("basketball_ncaab")

    assert isinstance(events, list)
    assert len(events) > 0
    assert all(isinstance(e, OddsAPIEvent) for e in events)
    # /events endpoint never includes bookmaker data
    assert all(e.bookmakers == [] for e in events)

    # Verify correct URL path is used
    http_client = client._http_client
    call_kwargs = http_client.get.call_args
    assert "/sports/basketball_ncaab/events" in call_kwargs.args[0]


@pytest.mark.asyncio
async def test_get_odds_returns_events_and_usage() -> None:
    """get_odds() should parse events and extract usage from response headers."""
    headers_data = json.loads((FIXTURES / "odds_basketball_ncaab_headers.json").read_text())
    client = build_client(
        make_response(
            "odds_basketball_ncaab.json",
            headers={k: str(v) for k, v in headers_data.items()},
        )
    )

    events, usage = await client.get_odds("basketball_ncaab")

    # Events are parsed correctly
    assert isinstance(events, list)
    assert len(events) > 0
    assert all(isinstance(e, OddsAPIEvent) for e in events)
    # At least one event should have bookmaker data
    assert any(len(e.bookmakers) > 0 for e in events)

    # Usage is parsed from headers
    assert isinstance(usage, OddsAPIUsage)
    assert usage.credits_used == 3
    assert usage.credits_remaining == 497

    # Verify correct URL path and required params
    http_client = client._http_client
    call_kwargs = http_client.get.call_args
    assert "/sports/basketball_ncaab/odds" in call_kwargs.args[0]
    params = call_kwargs.kwargs["params"]
    assert params["oddsFormat"] == "american"
    assert params["regions"] == "us"
    assert "h2h" in params["markets"]
    assert "bookmakers" not in params


@pytest.mark.asyncio
async def test_get_odds_uses_default_markets_and_region() -> None:
    """When no markets/regions args provided, defaults must be used."""
    headers_data = {"x-requests-used": "3", "x-requests-remaining": "497"}
    client = build_client(make_response("odds_basketball_ncaab.json", headers=headers_data))

    await client.get_odds("basketball_ncaab")

    params = client._http_client.get.call_args.kwargs["params"]
    assert params["markets"] == "h2h,spreads,totals"
    assert params["regions"] == "us"
    assert "bookmakers" not in params


@pytest.mark.asyncio
async def test_get_odds_raises_on_401() -> None:
    """get_odds() must raise OddsAPIError with status_code=401 on auth failure."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(return_value=make_error_response(401))
    client = OddsAPIClient(api_key="bad-key", base_url=BASE_URL, http_client=http_client)

    with pytest.raises(OddsAPIError) as exc_info:
        await client.get_odds("basketball_ncaab")

    assert exc_info.value.status_code == 401
    assert "Invalid API key" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_odds_raises_on_422() -> None:
    """get_odds() must raise OddsAPIError with status_code=422 on unprocessable entity."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(return_value=make_error_response(422, "invalid sport key"))
    client = OddsAPIClient(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)

    with pytest.raises(OddsAPIError) as exc_info:
        await client.get_odds("bad_sport_key")

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_get_odds_raises_on_429() -> None:
    """get_odds() must raise OddsAPIError with status_code=429 on rate limit."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(return_value=make_error_response(429))
    client = OddsAPIClient(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)

    with pytest.raises(OddsAPIError) as exc_info:
        await client.get_odds("basketball_ncaab")

    assert exc_info.value.status_code == 429
    assert "Rate limit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_odds_raises_on_unexpected_error_status() -> None:
    """get_odds() must raise OddsAPIError for any unexpected non-2xx status."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(return_value=make_error_response(503, "service unavailable"))
    client = OddsAPIClient(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)

    with pytest.raises(OddsAPIError) as exc_info:
        await client.get_odds("basketball_ncaab")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_get_odds_raises_on_timeout() -> None:
    """get_odds() must raise OddsAPIError(0, ...) when the request times out."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    client = OddsAPIClient(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)

    with pytest.raises(OddsAPIError) as exc_info:
        await client.get_odds("basketball_ncaab")

    assert exc_info.value.status_code == 0
    assert "timeout" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_get_odds_empty_response_returns_empty_list() -> None:
    """When the Odds API returns [], get_odds() must return ([], usage) without error."""
    usage_headers = {"x-requests-used": "3", "x-requests-remaining": "494"}
    client = build_client(make_empty_response(headers=usage_headers))

    events, usage = await client.get_odds("basketball_ncaab")

    assert events == []
    assert isinstance(usage, OddsAPIUsage)
    assert usage.credits_used == 3
    assert usage.credits_remaining == 494


@pytest.mark.asyncio
async def test_get_odds_missing_usage_headers_defaults_to_zero() -> None:
    """When usage headers are absent, OddsAPIUsage values default to 0."""
    client = build_client(make_empty_response())  # no headers

    events, usage = await client.get_odds("basketball_ncaab")

    assert events == []
    assert usage.credits_used == 0
    assert usage.credits_remaining == 0
