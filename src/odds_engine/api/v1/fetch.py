"""Manual odds fetch trigger endpoint."""

from fastapi import APIRouter, Depends, HTTPException

from odds_engine.dependencies import get_cache_repo, get_odds_service
from odds_engine.exceptions import BudgetExhaustedError, OddsAPIError
from odds_engine.logging import get_logger
from odds_engine.repositories.cache_repo import CacheRepository
from odds_engine.schemas.odds import ManualFetchRequest, ManualFetchResponse
from odds_engine.services.odds_service import OddsService
from odds_engine.sport_groups import sport_group as _sport_group

router = APIRouter()
logger = get_logger(__name__)


@router.post("/fetch", response_model=ManualFetchResponse, status_code=200)
async def manual_fetch(
    body: ManualFetchRequest,
    service: OddsService = Depends(get_odds_service),
    cache: CacheRepository = Depends(get_cache_repo),
) -> ManualFetchResponse:
    """Trigger a manual odds fetch for a specific sport key. Respects budget limits."""
    # Validate sport key against cached active sports list before spending credits.
    # The sports cache is populated on startup and refreshed by the daily fetch job.
    active_sports = await cache.get_active_sports()
    if active_sports is not None:
        active_keys = {s.key for s in active_sports}
        if body.sport_key not in active_keys:
            raise HTTPException(
                status_code=404,
                detail=f"Sport key '{body.sport_key}' is not currently active.",
            )

    try:
        return await service.fetch_and_store(body.sport_key, _sport_group(body.sport_key))
    except BudgetExhaustedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except OddsAPIError as exc:
        logger.error(
            "manual_fetch.odds_api_error",
            sport_key=body.sport_key,
            status_code=exc.status_code,
            detail=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
