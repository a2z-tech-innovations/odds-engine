"""WebSocket endpoint for real-time odds updates via Redis pub/sub."""

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from odds_engine.logging import get_logger
from odds_engine.repositories.cache_repo import CacheRepository

router = APIRouter()


@router.websocket("/ws")
async def websocket_odds(
    websocket: WebSocket,
    api_key: str = Query(...),
    sport_group: str | None = Query(None),
) -> None:
    """
    WebSocket endpoint for real-time odds updates.

    Auth: api_key query param validated against app.state.settings.api_secret_key
    Filter: sport_group (optional) — if provided, subscribe to odds:updates:{sport_group}
            otherwise subscribe to odds:updates:all

    On connect:
    1. Validate api_key — close with code 4001 if invalid
    2. Accept the WebSocket connection
    3. Subscribe to the appropriate Redis pub/sub channel
    4. Loop: receive messages from Redis, forward to WebSocket
    5. On WebSocketDisconnect: unsubscribe and clean up

    Send JSON messages directly as received from Redis pub/sub.
    """
    expected_key = websocket.app.state.settings.api_secret_key
    if api_key != expected_key:
        await websocket.close(code=4001)
        return

    await websocket.accept()

    redis = websocket.app.state.redis
    cache = CacheRepository(redis)
    channel = f"odds:updates:{sport_group}" if sport_group else "odds:updates:all"

    log = get_logger(__name__)
    log.info("ws.connected", channel=channel)

    # Send current cached snapshot immediately so consumer doesn't wait up to 60 min
    if sport_group:
        cached_events = await cache.get_active_events(sport_group)
        if cached_events:
            for event in cached_events:
                await websocket.send_text(event.model_dump_json())
            log.debug("ws.initial_snapshot_sent", sport_group=sport_group, count=len(cached_events))

    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=2.0,
                )
            except TimeoutError:
                continue  # No message, loop again
            if message and message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        log.info("ws.disconnected", channel=channel)
    except Exception as e:
        log.error("ws.error", error=str(e))
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
