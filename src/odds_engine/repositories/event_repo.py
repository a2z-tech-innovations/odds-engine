"""Repository for Event persistence and querying."""

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from odds_engine.models.events import Event
from odds_engine.schemas.events import EventFilterParams


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_event(
        self,
        external_id: str,
        sport_key: str,
        sport_group: str,
        home_team: str,
        away_team: str,
        commence_time: datetime,
        status: str,
    ) -> Event:
        """Insert a new event or update an existing one matched by external_id."""
        new_id = uuid.uuid4()
        stmt = (
            pg_insert(Event)
            .values(
                id=new_id,
                external_id=external_id,
                sport_key=sport_key,
                sport_group=sport_group,
                home_team=home_team,
                away_team=away_team,
                commence_time=commence_time,
                status=status,
            )
            .on_conflict_do_update(
                index_elements=["external_id"],
                set_={
                    "sport_key": sport_key,
                    "sport_group": sport_group,
                    "home_team": home_team,
                    "away_team": away_team,
                    "commence_time": commence_time,
                    "status": status,
                    "updated_at": func.now(),
                },
            )
            .returning(Event)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one()
        return row

    async def set_opening_line(self, event_id: uuid.UUID, best_line: dict) -> None:
        """Set opening_line only if it has not been set yet (first fetch guard)."""
        stmt = (
            update(Event)
            .where(Event.id == event_id, Event.opening_line == {})
            .values(opening_line=best_line)
        )
        await self._session.execute(stmt)

    async def get_by_external_id(self, external_id: str) -> Event | None:
        """Fetch an event by its Odds API external_id."""
        stmt = (
            select(Event)
            .where(Event.external_id == external_id)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, event_id: uuid.UUID) -> Event | None:
        """Fetch an event by its internal UUID primary key."""
        stmt = (
            select(Event)
            .where(Event.id == event_id)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_many(
        self,
        filters: EventFilterParams,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        """Return a filtered, paginated list of events ordered by commence_time ASC."""
        stmt = select(Event)
        stmt = _apply_filters(stmt, filters)
        stmt = stmt.order_by(Event.commence_time.asc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, filters: EventFilterParams) -> int:
        """Return the total number of events matching the given filters."""
        stmt = select(func.count()).select_from(Event)
        stmt = _apply_filters(stmt, filters)
        result = await self._session.execute(stmt)
        return result.scalar_one()


def _apply_filters(stmt, filters: EventFilterParams):
    """Apply EventFilterParams conditions to a SELECT statement."""
    if filters.sport_group is not None:
        stmt = stmt.where(Event.sport_group == filters.sport_group)
    if filters.sport_key is not None:
        stmt = stmt.where(Event.sport_key == filters.sport_key)
    if filters.status is not None:
        stmt = stmt.where(Event.status == filters.status)
    if filters.commence_from is not None:
        stmt = stmt.where(Event.commence_time >= filters.commence_from)
    if filters.commence_to is not None:
        stmt = stmt.where(Event.commence_time <= filters.commence_to)
    return stmt
