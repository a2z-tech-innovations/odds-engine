"""Repository for OddsSnapshot, BookmakerOdds, EnrichedSnapshot, and ApiUsage persistence."""

import uuid
from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from odds_engine.models.odds import ApiUsage, BookmakerOdds, EnrichedSnapshot, OddsSnapshot


class OddsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_snapshot(
        self,
        event_id: uuid.UUID,
        fetched_at: datetime,
        credits_used: int | None = None,
    ) -> OddsSnapshot:
        """Persist a new OddsSnapshot and return the ORM instance."""
        snapshot = OddsSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            fetched_at=fetched_at,
            credits_used=credits_used,
        )
        self._session.add(snapshot)
        await self._session.flush([snapshot])
        return snapshot

    async def create_bookmaker_odds_batch(self, rows: list[dict]) -> None:
        """Bulk-insert a list of bookmaker odds rows.

        Each dict must contain keys matching BookmakerOdds columns.
        No-op when rows is empty.
        """
        if not rows:
            return
        await self._session.execute(insert(BookmakerOdds), rows)

    async def create_enriched_snapshot(
        self,
        snapshot_id: uuid.UUID,
        event_id: uuid.UUID,
        best_line: dict,
        consensus_line: dict,
        vig_free: dict,
        movement: dict,
    ) -> EnrichedSnapshot:
        """Persist an EnrichedSnapshot and return the ORM instance."""
        enriched = EnrichedSnapshot(
            id=uuid.uuid4(),
            snapshot_id=snapshot_id,
            event_id=event_id,
            best_line=best_line,
            consensus_line=consensus_line,
            vig_free=vig_free,
            movement=movement,
        )
        self._session.add(enriched)
        await self._session.flush([enriched])
        return enriched

    async def get_latest_enriched(self, event_id: uuid.UUID) -> EnrichedSnapshot | None:
        """Return the most recent EnrichedSnapshot for an event, ordered by computed_at DESC."""
        stmt = (
            select(EnrichedSnapshot)
            .where(EnrichedSnapshot.event_id == event_id)
            .order_by(EnrichedSnapshot.computed_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_snapshot_history(
        self,
        event_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[OddsSnapshot]:
        """Return OddsSnapshots for an event ordered by fetched_at DESC."""
        stmt = (
            select(OddsSnapshot)
            .where(OddsSnapshot.event_id == event_id)
            .order_by(OddsSnapshot.fetched_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def record_api_usage(
        self,
        credits_used: int,
        credits_remaining: int,
        endpoint: str,
        sport_key: str | None = None,
    ) -> ApiUsage:
        """Persist an API usage record and return the ORM instance."""
        usage = ApiUsage(
            id=uuid.uuid4(),
            credits_used=credits_used,
            credits_remaining=credits_remaining,
            endpoint=endpoint,
            sport_key=sport_key,
        )
        self._session.add(usage)
        await self._session.flush([usage])
        return usage
