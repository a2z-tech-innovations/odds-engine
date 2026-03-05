from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

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
from odds_engine.sport_groups import sport_group as _sport_group

logger = get_logger(__name__)


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

        async def _warm_cache() -> None:
            """Load the latest enriched snapshots from DB into Redis (no API call)."""
            from odds_engine.repositories.event_repo import EventRepository as ER
            from odds_engine.schemas.enriched import EnrichedEventResponse
            from odds_engine.schemas.events import EventFilterParams

            async with app.state.session_factory() as session:
                event_repo = ER(session)
                odds_repo = OddsRepository(session)
                publisher = OddsPublisher(_cache_repo)

                db_events = await event_repo.get_many(EventFilterParams(), limit=500)
                enriched_events: list[EnrichedEventResponse] = []
                for db_event in db_events:
                    enriched_snap = await odds_repo.get_latest_enriched(db_event.id)
                    if enriched_snap is None:
                        continue
                    enriched_events.append(EnrichedEventResponse(
                        event_id=db_event.external_id,
                        sport_key=db_event.sport_key,
                        sport_group=db_event.sport_group,
                        home_team=db_event.home_team,
                        away_team=db_event.away_team,
                        commence_time=db_event.commence_time,
                        status=db_event.status.value if hasattr(db_event.status, "value") else str(db_event.status),
                        snapshot_id=enriched_snap.snapshot_id,
                        fetched_at=enriched_snap.computed_at,
                        bookmakers=enriched_snap.bookmakers,
                        best_line=enriched_snap.best_line,
                        consensus=enriched_snap.consensus_line,
                        vig_free=enriched_snap.vig_free,
                        movement=enriched_snap.movement,
                    ))

                await publisher.publish_batch(enriched_events)

            log.info("cache.warmed", events=len(enriched_events))

        async def _refresh_sports_cache() -> None:
            """Fetch active sports from Odds API and populate sports:active in Redis."""
            try:
                sports = await app.state.odds_client.get_sports(active_only=True)
                await _cache_repo.set_active_sports(sports)
                log.info("startup.sports_cached", count=len(sports))
            except Exception as exc:
                log.error("startup.sports_cache_failed", error=str(exc))

        async def _startup_job() -> None:
            """On startup: warm cache from DB, refresh sports list, then fetch if stale."""
            from datetime import timedelta

            await _warm_cache()
            await _refresh_sports_cache()

            async with app.state.session_factory() as session:
                last_fetch = await OddsRepository(session).get_last_fetch_time()

            if last_fetch is None or (datetime.now(UTC) - last_fetch) > timedelta(hours=4):
                log.info("startup.data_stale_fetching", last_fetch=str(last_fetch))
                await _fetch_job()
            else:
                log.info("startup.data_fresh", last_fetch=str(last_fetch))

        from apscheduler.triggers.cron import CronTrigger

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            _fetch_job,
            CronTrigger(
                hour=settings.fetch_cron_hour,
                minute=settings.fetch_cron_minute,
                timezone=settings.fetch_cron_timezone,
            ),
            id="daily_fetch",
            max_instances=1,
        )
        scheduler.add_job(_startup_job, "date", id="startup_job")
        scheduler.start()
        app.state.scheduler = scheduler
        log.info(
            "scheduler.started",
            cron=f"{settings.fetch_cron_minute:02d} {settings.fetch_cron_hour:02d} * * *",
            timezone=settings.fetch_cron_timezone,
        )

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
        logger.error(
            "odds_api_error",
            path=request.url.path,
            status_code=exc.status_code,
            detail=str(exc),
        )
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
