"""Top-level API router aggregation."""

from fastapi import APIRouter

from odds_engine.api.v1 import events, fetch, health, odds, sports

router = APIRouter(prefix="/api/v1")
router.include_router(events.router, prefix="/events", tags=["events"])
router.include_router(odds.router, prefix="/odds", tags=["odds"])
router.include_router(health.router, tags=["health", "budget"])
router.include_router(fetch.router, tags=["fetch"])
router.include_router(sports.router, prefix="/sports", tags=["sports"])
