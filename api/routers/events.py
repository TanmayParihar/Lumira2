"""Events router — list, filter, and retrieve processed events."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import EventList, EventOut
from storage.database import get_db
from storage.models import Event

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=EventList)
async def list_events(
    district: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    min_severity: Optional[int] = Query(None, ge=1, le=5),
    source: Optional[str] = Query(None),
    hours: int = Query(default=24, ge=1, le=720),
    include_duplicates: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    """List events with optional filters."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    filters = [Event.ingested_at >= since]

    if not include_duplicates:
        filters.append(Event.is_duplicate == False)  # noqa: E712
    if district:
        filters.append(Event.district == district)
    if state:
        filters.append(Event.state == state)
    if event_type:
        filters.append(Event.event_type == event_type.upper())
    if min_severity:
        filters.append(Event.severity >= min_severity)
    if source:
        filters.append(Event.source == source)

    count_q = select(func.count(Event.id)).where(and_(*filters))
    total = (await session.execute(count_q)).scalar() or 0

    stmt = (
        select(Event)
        .where(and_(*filters))
        .order_by(Event.ingested_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    events = result.scalars().all()

    return EventList(total=total, items=[_to_out(e) for e in events])


@router.get("/{event_id}", response_model=EventOut)
async def get_event(event_id: UUID, session: AsyncSession = Depends(get_db)):
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return _to_out(event)


@router.get("/{event_id}/related", response_model=EventList)
async def get_related_events(
    event_id: UUID,
    session: AsyncSession = Depends(get_db),
):
    """Return events in the same fusion group."""
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    if not event.fusion_group_id:
        return EventList(total=0, items=[])

    stmt = (
        select(Event)
        .where(
            and_(
                Event.fusion_group_id == event.fusion_group_id,
                Event.id != event_id,
            )
        )
        .order_by(Event.ingested_at.desc())
    )
    result = await session.execute(stmt)
    related = result.scalars().all()
    return EventList(total=len(related), items=[_to_out(e) for e in related])


def _to_out(event: Event) -> EventOut:
    lat, lon = _get_coords(event)
    return EventOut(
        id=event.id,
        source=event.source,
        source_url=event.source_url,
        title=event.title,
        media_type=event.media_type or "text",
        event_type=event.event_type,
        severity=event.severity,
        confidence=event.confidence,
        description=event.description,
        keywords=event.keywords,
        language=event.language,
        location_name=event.location_name,
        district=event.district,
        state=event.state,
        latitude=lat,
        longitude=lon,
        event_time=event.event_time,
        ingested_at=event.ingested_at,
        is_duplicate=event.is_duplicate or False,
        fusion_group_id=event.fusion_group_id,
    )


def _get_coords(event: Event):
    try:
        if event.coordinates:
            from geoalchemy2.shape import to_shape
            point = to_shape(event.coordinates)
            return point.y, point.x
    except Exception:
        pass
    return None, None
