from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from odds_engine.clients.odds_api import OddsAPIClient
from odds_engine.config import Settings, get_settings
from odds_engine.exceptions import BudgetExhaustedError, EventNotFoundError, OddsAPIError
from odds_engine.logging import configure_logging, get_logger
from odds_engine.models.database import create_engine, create_session_factory

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

    yield

    # Shutdown
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
