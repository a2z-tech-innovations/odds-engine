from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from odds_engine.clients.odds_api import OddsAPIClient
from odds_engine.config import Settings, get_settings
from odds_engine.exceptions import BudgetExhaustedError, EventNotFoundError, OddsAPIError
from odds_engine.logging import configure_logging, get_logger
from odds_engine.models.database import create_engine, create_session_factory

logger = get_logger(__name__)


def _sport_group(sport_key: str) -> str:
    if sport_key.startswith(("tennis_atp_", "tennis_wta_")):
        return "Tennis"
    if sport_key.startswith("basketball_"):
        return "Basketball"
    return sport_key.split("_")[0].title()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings

    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    log = get_logger(__name__)

    # Database
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    log.info("database.connected")

    # Redis
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.ping()
    app.state.redis = redis
    log.info("redis.connected")

    # HTTP client + Odds API client
    http_client = httpx.AsyncClient(timeout=30.0)
    app.state.http_client = http_client
    app.state.odds_client = OddsAPIClient(
        api_key=settings.odds_api_key,
        base_url=settings.odds_api_base_url,
        http_client=http_client,
    )
    log.info("odds_client.created")

    # Scheduler
    if settings.scheduler_enabled:
        from odds_engine.repositories.cache_repo import CacheRepository
        from odds_engine.repositories.event_repo import EventRepository
        from odds_engine.repositories.odds_repo import OddsRepository
        from odds_engine.services.odds_service import OddsService
        from odds_engine.services.publisher import OddsPublisher
        from odds_engine.services.scheduler import BudgetManager, FetchScheduler, SportDiscovery

        _cache_repo = CacheRepository(redis)

        # Seed Redis budget counters from DB on startup so restarts don't reset them.
        # Redis counters are ephemeral; DB is the authoritative source of truth.
        async with app.state.session_factory() as _seed_session:
            _odds_repo_seed = OddsRepository(_seed_session)
            _db_daily = await _odds_repo_seed.get_daily_credits_used()
            _db_monthly = await _odds_repo_seed.get_monthly_credits_used()
        _redis_budget = await _cache_repo.get_budget()
        if _db_daily > _redis_budget["daily_used"]:
            from odds_engine.repositories.cache_repo import (
                seconds_until_midnight_utc,
                seconds_until_next_month,
            )
            await redis.set("budget:daily", _db_daily, ex=seconds_until_midnight_utc())
            log.info("budget.seeded_daily", daily_used=_db_daily)
        if _db_monthly > _redis_budget["monthly_used"]:
            await redis.set("budget:monthly", _db_monthly, ex=seconds_until_next_month())
            log.info("budget.seeded_monthly", monthly_used=_db_monthly)

        _budget_manager = BudgetManager(settings, _cache_repo)
        _sport_discovery = SportDiscovery(app.state.odds_client, _cache_repo)
        _fetch_scheduler = FetchScheduler(settings, _budget_manager, _sport_discovery)

        async def _fetch_job() -> None:
            sport_keys = await _fetch_scheduler.get_sports_to_fetch()
            if not sport_keys:
                log.info("scheduler.nothing_to_fetch")
                return
            publisher = OddsPublisher(_cache_repo)
            for sport_key in sport_keys:
                async with app.state.session_factory() as session:
                    try:
                        svc = OddsService(
                            client=app.state.odds_client,
                            event_repo=EventRepository(session),
                            odds_repo=OddsRepository(session),
                            cache=_cache_repo,
                            publisher=publisher,
                        )
                        result = await svc.fetch_and_store(
                            sport_key=sport_key,
                            sport_group=_sport_group(sport_key),
                        )
                        await session.commit()
                        log.info(
                            "scheduler.fetch_complete",
                            sport_key=sport_key,
                            events=result.events_fetched,
                            credits_used=result.credits_used,
                        )
                    except Exception as exc:
                        await session.rollback()
                        log.error("scheduler.fetch_error", sport_key=sport_key, error=str(exc))

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            _fetch_job,
            "interval",
            minutes=settings.pre_match_interval_minutes,
            id="pre_match_fetch",
            max_instances=1,
        )
        scheduler.add_job(_fetch_job, "date", id="startup_fetch")
        scheduler.start()
        app.state.scheduler = scheduler
        log.info("scheduler.started", interval_minutes=settings.pre_match_interval_minutes)

    yield

    # Shutdown
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown(wait=False)
        log.info("scheduler.stopped")
    await http_client.aclose()
    await redis.aclose()
    await engine.dispose()
    log.info("shutdown.complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title="Odds Engine",
        description="Sport-agnostic odds aggregation service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Exception handlers
    @app.exception_handler(EventNotFoundError)
    async def event_not_found_handler(request: Request, exc: EventNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(BudgetExhaustedError)
    async def budget_exhausted_handler(request: Request, exc: BudgetExhaustedError) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": "API credit budget exhausted. Serving from cache only."},
        )

    @app.exception_handler(OddsAPIError)
    async def odds_api_error_handler(request: Request, exc: OddsAPIError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    # Middleware
    from odds_engine.api.middleware import AuthMiddleware, RequestIDMiddleware

    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestIDMiddleware)

    # Routers
    from odds_engine.api.router import router

    app.include_router(router)

    return app


app = create_app()
