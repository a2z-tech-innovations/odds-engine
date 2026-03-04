"""Redis cache repository — hot cache and pub/sub publishing."""

from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from odds_engine.schemas.enriched import EnrichedEventResponse
from odds_engine.schemas.odds_api import OddsAPISport


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


def _seconds_until_next_month() -> int:
    now = datetime.now(UTC)
    if now.month == 12:
        next_month = now.replace(
            year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    else:
        next_month = now.replace(
            month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return int((next_month - now).total_seconds())


class CacheRepository:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    # --- Event cache ---

    async def get_event(self, external_id: str) -> EnrichedEventResponse | None:
        """GET event:{external_id} → deserialize JSON → EnrichedEventResponse."""
        data = await self.redis.get(f"event:{external_id}")
        if data is None:
            return None
        return EnrichedEventResponse.model_validate_json(data)

    async def set_event(self, event: EnrichedEventResponse) -> None:
        """SET event:{external_id} <json> EX 300."""
        await self.redis.set(
            f"event:{event.event_id}",
            event.model_dump_json(),
            ex=300,
        )

    async def get_active_events(self, sport_group: str) -> list[EnrichedEventResponse] | None:
        """GET events:{sport_group}:active → deserialize JSON array. Returns None on cache miss."""
        data = await self.redis.get(f"events:{sport_group}:active")
        if data is None:
            return None
        import json

        raw_list = json.loads(data)
        return [EnrichedEventResponse.model_validate(item) for item in raw_list]

    async def set_active_events(
        self, sport_group: str, events: list[EnrichedEventResponse]
    ) -> None:
        """SET events:{sport_group}:active <json array> EX 300."""
        import json

        payload = json.dumps([json.loads(e.model_dump_json()) for e in events])
        await self.redis.set(f"events:{sport_group}:active", payload, ex=300)

    # --- Sports cache ---

    async def get_active_sports(self) -> list[OddsAPISport] | None:
        """GET sports:active → None on miss."""
        data = await self.redis.get("sports:active")
        if data is None:
            return None
        import json

        raw_list = json.loads(data)
        return [OddsAPISport.model_validate(item) for item in raw_list]

    async def set_active_sports(self, sports: list[OddsAPISport]) -> None:
        """SET sports:active <json> EX 3600."""
        import json

        payload = json.dumps([json.loads(s.model_dump_json()) for s in sports])
        await self.redis.set("sports:active", payload, ex=3600)

    # --- Budget tracking ---

    async def increment_daily_budget(self, credits: int) -> int:
        """INCRBY budget:daily {credits}. Sets expiry to midnight UTC if key is new."""
        new_total = await self.redis.incrby("budget:daily", credits)
        ttl = await self.redis.ttl("budget:daily")
        if ttl == -1:
            await self.redis.expire("budget:daily", _seconds_until_midnight_utc())
        return int(new_total)

    async def increment_monthly_budget(self, credits: int) -> int:
        """INCRBY budget:monthly {credits}. Sets expiry to 1st of next month UTC if key is new."""
        new_total = await self.redis.incrby("budget:monthly", credits)
        ttl = await self.redis.ttl("budget:monthly")
        if ttl == -1:
            await self.redis.expire("budget:monthly", _seconds_until_next_month())
        return int(new_total)

    async def get_budget(self) -> dict:
        """Returns {"daily_used": int, "monthly_used": int}. Missing keys → 0."""
        daily_raw = await self.redis.get("budget:daily")
        monthly_raw = await self.redis.get("budget:monthly")
        return {
            "daily_used": int(daily_raw) if daily_raw is not None else 0,
            "monthly_used": int(monthly_raw) if monthly_raw is not None else 0,
        }

    # --- Pub/Sub ---

    async def publish_odds_update(self, event: EnrichedEventResponse) -> None:
        """PUBLISH odds:updates:{sport_group} and odds:updates:all with enriched event JSON."""
        payload = event.model_dump_json()
        async with self.redis.pipeline() as pipe:
            await pipe.publish(f"odds:updates:{event.sport_group}", payload)
            await pipe.publish("odds:updates:all", payload)
            await pipe.execute()
