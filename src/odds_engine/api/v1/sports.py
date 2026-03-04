"""Sports listing endpoint."""

from fastapi import APIRouter, Depends

from odds_engine.dependencies import get_cache_repo
from odds_engine.repositories.cache_repo import CacheRepository
from odds_engine.schemas.odds_api import OddsAPISport

router = APIRouter()


@router.get("", response_model=list[OddsAPISport])
async def get_sports(
    cache: CacheRepository = Depends(get_cache_repo),
) -> list[OddsAPISport]:
    """Return active sport keys. Source: cached /sports data."""
    sports = await cache.get_active_sports()
    if sports is None:
        return []
    return sports
