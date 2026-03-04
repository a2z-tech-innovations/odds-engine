"""
Shared FastAPI dependency factories.

All application-wide singletons (engine, session factory, Redis pool) are
created once in the lifespan context and stored on app.state. Dependency
functions here retrieve them from state so they're available via Depends().
"""

from collections.abc import AsyncGenerator

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        yield session


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis
