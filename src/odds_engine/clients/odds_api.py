"""Async HTTP client wrapping The Odds API v4."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from odds_engine.exceptions import OddsAPIError
from odds_engine.schemas.odds_api import OddsAPIEvent, OddsAPISport

if TYPE_CHECKING:
    from odds_engine.config import Settings


@dataclass
class OddsAPIUsage:
    credits_used: int
    credits_remaining: int


class OddsAPIClient:
    def __init__(self, api_key: str, base_url: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Translate HTTP error status codes into domain exceptions."""
        if response.status_code == 200:
            return
        if response.status_code == 401:
            raise OddsAPIError(401, "Invalid API key")
        if response.status_code == 422:
            raise OddsAPIError(422, response.text)
        if response.status_code == 429:
            raise OddsAPIError(429, "Rate limit exceeded")
        if response.is_error:
            raise OddsAPIError(response.status_code, response.text)

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        """Execute a GET request, injecting the API key and handling transport errors."""
        full_params = {"apiKey": self._api_key}
        if params:
            full_params.update(params)
        url = f"{self._base_url}{path}"
        try:
            response = await self._http_client.get(url, params=full_params)
        except httpx.TimeoutException as exc:
            raise OddsAPIError(0, "Request timeout") from exc
        self._raise_for_status(response)
        return response

    @staticmethod
    def _parse_usage(response: httpx.Response) -> OddsAPIUsage:
        return OddsAPIUsage(
            credits_used=int(response.headers.get("x-requests-last", 0)),
            credits_remaining=int(response.headers.get("x-requests-remaining", 0)),
        )

    async def get_sports(self, active_only: bool = True) -> list[OddsAPISport]:
        """GET /sports — free endpoint; returns active (or all) sport keys."""
        params: dict[str, str] = {}
        if not active_only:
            params["all"] = "true"
        response = await self._get("/sports", params)
        return [OddsAPISport.model_validate(item) for item in response.json()]

    async def get_events(self, sport_key: str) -> list[OddsAPIEvent]:
        """GET /sports/{sport_key}/events — free endpoint; bookmakers list is always empty."""
        response = await self._get(f"/sports/{sport_key}/events")
        return [OddsAPIEvent.model_validate(item) for item in response.json()]

    async def get_odds(
        self,
        sport_key: str,
        markets: list[str] | None = None,
        regions: str = "us",
    ) -> tuple[list[OddsAPIEvent], OddsAPIUsage]:
        """GET /sports/{sport_key}/odds — costs credits; returns (events, usage)."""
        if markets is None:
            markets = ["h2h", "spreads", "totals"]

        params = {
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": "american",
        }
        response = await self._get(f"/sports/{sport_key}/odds", params)
        events = [OddsAPIEvent.model_validate(item) for item in response.json()]
        usage = self._parse_usage(response)
        return events, usage


def create_odds_api_client(settings: Settings) -> OddsAPIClient:
    """Factory for DI: create an OddsAPIClient backed by a real httpx.AsyncClient.

    The caller is responsible for managing the httpx client lifecycle (e.g. closing it
    in a FastAPI lifespan context manager).
    """
    http_client = httpx.AsyncClient(timeout=30.0)
    return OddsAPIClient(
        api_key=settings.odds_api_key,
        base_url=settings.odds_api_base_url,
        http_client=http_client,
    )
