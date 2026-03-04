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
    odds_repo: OddsRepository = Depends(get_odds_repo),
) -> dict:
    """Return current credit budget usage sourced from the api_usage DB table."""
    daily_used = await odds_repo.get_daily_credits_used()
    monthly_used = await odds_repo.get_monthly_credits_used()
    return {"daily_used": daily_used, "monthly_used": monthly_used}


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
    budget: dict = {"daily_used": 0, "monthly_used": 0}

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        await cache.redis.ping()
    except Exception:
        redis_status = "error"

    with contextlib.suppress(Exception):
        daily = await odds_repo.get_daily_credits_used()
        monthly = await odds_repo.get_monthly_credits_used()
        budget = {"daily_used": daily, "monthly_used": monthly}

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

    return {
        "status": overall,
        "database": db_status,
        "redis": redis_status,
        "budget": budget,
        "version": request.app.version,
    }
