"""Manual odds fetch trigger endpoint."""

from fastapi import APIRouter, Depends, HTTPException

from odds_engine.dependencies import get_odds_service
from odds_engine.exceptions import BudgetExhaustedError
from odds_engine.schemas.odds import ManualFetchRequest, ManualFetchResponse
from odds_engine.services.odds_service import OddsService
from odds_engine.sport_groups import sport_group as _sport_group

router = APIRouter()


@router.post("/fetch", response_model=ManualFetchResponse, status_code=200)
async def manual_fetch(
    body: ManualFetchRequest,
    service: OddsService = Depends(get_odds_service),
) -> ManualFetchResponse:
    """Trigger a manual odds fetch for a specific sport key. Respects budget limits."""
    try:
        return await service.fetch_and_store(body.sport_key, _sport_group(body.sport_key))
    except BudgetExhaustedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
