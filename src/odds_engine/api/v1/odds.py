"""Odds query endpoints."""

from fastapi import APIRouter, Depends, Query

from odds_engine.dependencies import get_odds_service
from odds_engine.services.odds_service import OddsService

router = APIRouter()


@router.get("/best")
async def get_best_lines(
    sport_group: str | None = Query(None),
    market: str | None = Query(None),
    service: OddsService = Depends(get_odds_service),
) -> list[dict]:
    """Return best available lines across all active events."""
    return await service.get_best_lines(sport_group=sport_group, market=market)
