"""
Shared FastAPI dependency factories.

All application-wide singletons (engine, session factory, Redis pool) are
created once in the lifespan context and stored on app.state. Dependency
functions here retrieve them from state so they're available via Depends().
"""

from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from odds_engine.clients.odds_api import OddsAPIClient
from odds_engine.repositories.cache_repo import CacheRepository
from odds_engine.repositories.event_repo import EventRepository
from odds_engine.repositories.odds_repo import OddsRepository
from odds_engine.services.event_service import EventService
from odds_engine.services.odds_service import OddsService
from odds_engine.services.publisher import OddsPublisher


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def get_cache_repo(redis: Redis = Depends(get_redis)) -> CacheRepository:
    return CacheRepository(redis)


async def get_event_repo(db: AsyncSession = Depends(get_db)) -> EventRepository:
    return EventRepository(db)


async def get_odds_repo(db: AsyncSession = Depends(get_db)) -> OddsRepository:
    return OddsRepository(db)


async def get_odds_client(request: Request) -> OddsAPIClient:
    return request.app.state.odds_client


async def get_publisher(cache: CacheRepository = Depends(get_cache_repo)) -> OddsPublisher:
    return OddsPublisher(cache)


async def get_event_service(
    repo: EventRepository = Depends(get_event_repo),
    cache: CacheRepository = Depends(get_cache_repo),
    odds_repo: OddsRepository = Depends(get_odds_repo),
) -> EventService:
    return EventService(repo=repo, cache=cache, odds_repo=odds_repo)


async def get_odds_service(
    client: OddsAPIClient = Depends(get_odds_client),
    event_repo: EventRepository = Depends(get_event_repo),
    odds_repo: OddsRepository = Depends(get_odds_repo),
    cache: CacheRepository = Depends(get_cache_repo),
    publisher: OddsPublisher = Depends(get_publisher),
) -> OddsService:
    return OddsService(
        client=client,
        event_repo=event_repo,
        odds_repo=odds_repo,
        cache=cache,
        publisher=publisher,
    )
