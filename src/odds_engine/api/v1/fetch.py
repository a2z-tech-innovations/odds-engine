"""Manual odds fetch trigger endpoint."""

from fastapi import APIRouter, Depends, HTTPException

from odds_engine.dependencies import get_cache_repo, get_odds_service
from odds_engine.exceptions import BudgetExhaustedError
from odds_engine.repositories.cache_repo import CacheRepository
from odds_engine.schemas.odds import ManualFetchRequest, ManualFetchResponse
from odds_engine.services.odds_service import OddsService

router = APIRouter()


@router.post("/fetch", response_model=ManualFetchResponse, status_code=200)
async def manual_fetch(
    body: ManualFetchRequest,
    service: OddsService = Depends(get_odds_service),
    cache: CacheRepository = Depends(get_cache_repo),
) -> ManualFetchResponse:
    """Trigger a manual odds fetch for a specific sport key. Respects budget limits."""
    # Derive sport_group from active sports cache
    sports = await cache.get_active_sports()
    sport_group = body.sport_key  # fallback: use sport_key as group
    if sports:
        for s in sports:
            if s.key == body.sport_key:
                sport_group = s.group
                break

    try:
        return await service.fetch_and_store(body.sport_key, sport_group)
    except BudgetExhaustedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
