"""Event listing and detail endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from odds_engine.dependencies import get_event_repo, get_event_service, get_odds_repo
from odds_engine.exceptions import EventNotFoundError
from odds_engine.models.enums import EventStatus
from odds_engine.repositories.event_repo import EventRepository
from odds_engine.repositories.odds_repo import OddsRepository
from odds_engine.schemas.enriched import EnrichedEventResponse
from odds_engine.schemas.events import EventFilterParams
from odds_engine.schemas.odds import OddsSnapshotResponse
from odds_engine.services.event_service import EventService

router = APIRouter()


@router.get("", response_model=list[EnrichedEventResponse])
async def get_events(
    sport_group: str | None = Query(None),
    sport_key: str | None = Query(None),
    status: EventStatus | None = Query(None),
    commence_from: datetime | None = Query(None),
    commence_to: datetime | None = Query(None),
    service: EventService = Depends(get_event_service),
) -> list[EnrichedEventResponse]:
    """Return a filtered list of enriched events with full odds data."""
    filters = EventFilterParams(
        sport_group=sport_group,
        sport_key=sport_key,
        status=status,
        commence_from=commence_from,
        commence_to=commence_to,
    )
    return await service.get_events(filters)


@router.get("/{event_id}", response_model=EnrichedEventResponse)
async def get_event(
    event_id: str,
    service: EventService = Depends(get_event_service),
) -> EnrichedEventResponse:
    """Return a single enriched event by its external Odds API ID."""
    try:
        return await service.get_event(event_id)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{event_id}/history", response_model=list[OddsSnapshotResponse])
async def get_event_history(
    event_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    event_repo: EventRepository = Depends(get_event_repo),
    odds_repo: OddsRepository = Depends(get_odds_repo),
) -> list[OddsSnapshotResponse]:
    """Return historical odds snapshots for an event, ordered by fetched_at DESC."""
    db_event = await event_repo.get_by_external_id(event_id)
    if db_event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    snapshots = await odds_repo.get_snapshot_history(
        event_id=db_event.id,
        limit=limit,
        offset=offset,
    )
    return [
        OddsSnapshotResponse(
            snapshot_id=snap.id,
            event_id=snap.event_id,
            fetched_at=snap.fetched_at,
            credits_used=snap.credits_used,
            bookmakers=[],
        )
        for snap in snapshots
    ]
