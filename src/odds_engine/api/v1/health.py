"""Health check endpoint — no auth required."""

import contextlib

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from odds_engine.dependencies import get_cache_repo, get_db
from odds_engine.repositories.cache_repo import CacheRepository

router = APIRouter()


@router.get("/budget")
async def get_budget(cache: CacheRepository = Depends(get_cache_repo)) -> dict:
    """Return current credit budget usage."""
    return await cache.get_budget()


@router.get("/health")
async def health_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
    cache: CacheRepository = Depends(get_cache_repo),
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
        budget = await cache.get_budget()

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

    return {
        "status": overall,
        "database": db_status,
        "redis": redis_status,
        "budget": budget,
        "version": request.app.version,
    }
