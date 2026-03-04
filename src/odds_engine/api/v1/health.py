"""Health check endpoint — no auth required."""

import contextlib

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from odds_engine.dependencies import get_cache_repo, get_db, get_odds_repo
from odds_engine.repositories.cache_repo import CacheRepository
from odds_engine.repositories.odds_repo import OddsRepository

router = APIRouter()


@router.get("/budget")
async def get_budget(
    request: Request,
    odds_repo: OddsRepository = Depends(get_odds_repo),
) -> dict:
    """Return current credit budget usage sourced from the api_usage DB table."""
    monthly_limit = request.app.state.settings.monthly_credit_limit
    daily_used = await odds_repo.get_daily_credits_used()
    monthly_used = await odds_repo.get_actual_monthly_credits_used(monthly_limit)
    return {"daily_used": daily_used, "monthly_used": monthly_used, "monthly_limit": monthly_limit}


@router.get("/health")
async def health_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
    cache: CacheRepository = Depends(get_cache_repo),
    odds_repo: OddsRepository = Depends(get_odds_repo),
) -> dict:
    """Return service health status. Never raises — catches all exceptions."""
    db_status = "ok"
    redis_status = "ok"
    monthly_limit = request.app.state.settings.monthly_credit_limit
    budget: dict = {"daily_used": 0, "monthly_used": 0, "monthly_limit": monthly_limit}

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        await cache.redis.ping()
    except Exception:
        redis_status = "error"

    last_fetch_at: str | None = None
    with contextlib.suppress(Exception):
        daily = await odds_repo.get_daily_credits_used()
        monthly = await odds_repo.get_actual_monthly_credits_used(monthly_limit)
        budget = {"daily_used": daily, "monthly_used": monthly, "monthly_limit": monthly_limit}
        ts = await odds_repo.get_last_fetch_time()
        if ts is not None:
            last_fetch_at = ts.isoformat()

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

    return {
        "status": overall,
        "database": db_status,
        "redis": redis_status,
        "budget": budget,
        "last_fetch_at": last_fetch_at,
        "version": request.app.version,
    }
