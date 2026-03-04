"""Repository for OddsSnapshot, BookmakerOdds, EnrichedSnapshot, and ApiUsage persistence."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, insert, select
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

    async def get_bookmaker_odds_for_snapshot(self, snapshot_id: uuid.UUID) -> list[dict]:
        """Return flat bookmaker odds rows for a given snapshot as plain dicts."""
        stmt = select(BookmakerOdds).where(BookmakerOdds.snapshot_id == snapshot_id)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "bookmaker_key": r.bookmaker_key,
                "market_key": r.market_key,
                "outcome_name": r.outcome_name,
                "outcome_price": r.outcome_price,
                "outcome_point": r.outcome_point,
            }
            for r in rows
        ]

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

    async def get_monthly_credits_used(self) -> int:
        """Sum credits_used from api_usage for the current calendar month (UTC)."""
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        stmt = select(func.coalesce(func.sum(ApiUsage.credits_used), 0)).where(
            ApiUsage.recorded_at >= month_start
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get_daily_credits_used(self) -> int:
        """Sum credits_used from api_usage for today (UTC)."""
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = select(func.coalesce(func.sum(ApiUsage.credits_used), 0)).where(
            ApiUsage.recorded_at >= day_start
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get_actual_monthly_credits_used(self, monthly_limit: int = 500) -> int:
        """Derive actual monthly usage from the lowest credits_remaining ever recorded.

        Uses monthly_limit - min(credits_remaining) so we reflect what the Odds API
        actually charged, including any credits spent before this DB was set up.
        Falls back to summing credits_used if no api_usage rows exist.
        """
        stmt = select(func.min(ApiUsage.credits_remaining))
        result = await self._session.execute(stmt)
        min_remaining = result.scalar_one()
        if min_remaining is None:
            return 0
        return monthly_limit - int(min_remaining)

    async def get_latest_enriched_bulk(
        self, event_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, EnrichedSnapshot]:
        """Return the most recent EnrichedSnapshot per event for a list of event IDs.

        Returns a dict keyed by event_id. Events with no enriched snapshot are omitted.
        Uses DISTINCT ON (event_id) ordered by computed_at DESC — single query.
        """
        if not event_ids:
            return {}
        stmt = (
            select(EnrichedSnapshot)
            .where(EnrichedSnapshot.event_id.in_(event_ids))
            .distinct(EnrichedSnapshot.event_id)
            .order_by(EnrichedSnapshot.event_id, EnrichedSnapshot.computed_at.desc())
        )
        result = await self._session.execute(stmt)
        return {row.event_id: row for row in result.scalars().all()}

    async def get_last_fetch_time(self) -> datetime | None:
        """Return the recorded_at timestamp of the most recent api_usage row, or None."""
        stmt = select(func.max(ApiUsage.recorded_at))
        result = await self._session.execute(stmt)
        return result.scalar_one()

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
