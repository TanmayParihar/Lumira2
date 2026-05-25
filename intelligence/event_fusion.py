"""
Event Fusion: deduplication and correlation of events.

Two events are considered the SAME if:
  - They have the same event_type
  - Their locations are within 5 km of each other
  - They occurred within 2 hours of each other
  - Their text descriptions are sufficiently similar (Jaccard)

When a duplicate is found, the later event is marked duplicate_of
the earlier one. Related non-duplicate events within the same window
are assigned the same fusion_group_id.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from storage.database import get_session
from storage.models import Event

logger = structlog.get_logger(__name__)


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """Token-level Jaccard similarity between two strings."""
    if not text_a or not text_b:
        return 0.0
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) pairs."""
    from math import asin, cos, radians, sin, sqrt

    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


async def check_and_mark_duplicate(
    new_event: Event,
    session: AsyncSession,
) -> bool:
    """
    Compare new_event against recent events. If a strong duplicate is found,
    mark new_event.is_duplicate = True and return True.
    """
    window_start = datetime.now(timezone.utc) - timedelta(
        hours=settings.event_dedup_window_hours
    )

    stmt = select(Event).where(
        and_(
            Event.event_type == new_event.event_type,
            Event.ingested_at >= window_start,
            Event.is_duplicate == False,  # noqa: E712
            Event.id != new_event.id,
        )
    ).limit(50)

    result = await session.execute(stmt)
    candidates = result.scalars().all()

    for candidate in candidates:
        # Text similarity
        text_sim = _jaccard_similarity(
            new_event.description or "", candidate.description or ""
        )

        # Spatial proximity
        spatial_close = False
        if (
            new_event.latitude is not None
            and candidate.latitude is not None
        ):
            # Use stored lat/lon if we have them; PostGIS parses them back
            try:
                dist = _haversine_km(
                    new_event.latitude,
                    new_event.longitude,
                    candidate.latitude,
                    candidate.longitude,
                )
                spatial_close = dist < 5.0
            except Exception:
                pass

        if text_sim >= settings.event_fusion_similarity_threshold and spatial_close:
            new_event.is_duplicate = True
            new_event.duplicate_of = candidate.id
            logger.info(
                "fusion.duplicate_detected",
                new_id=str(new_event.id),
                original_id=str(candidate.id),
                similarity=round(text_sim, 3),
            )
            return True

    return False


async def assign_fusion_group(
    new_event: Event,
    session: AsyncSession,
) -> None:
    """
    Assign a fusion_group_id if this event is geographically and
    temporally related to existing events (even if not a strict duplicate).
    """
    if new_event.is_duplicate:
        return

    window_start = datetime.now(timezone.utc) - timedelta(hours=6)

    stmt = select(Event).where(
        and_(
            Event.event_type == new_event.event_type,
            Event.ingested_at >= window_start,
            Event.is_duplicate == False,  # noqa: E712
            Event.id != new_event.id,
            Event.district == new_event.district,
            Event.district.isnot(None),
        )
    ).limit(20)

    result = await session.execute(stmt)
    related = result.scalars().all()

    if not related:
        new_event.fusion_group_id = uuid.uuid4()
        return

    # Reuse the earliest related event's fusion group
    existing_groups = [r.fusion_group_id for r in related if r.fusion_group_id]
    if existing_groups:
        new_event.fusion_group_id = existing_groups[0]
    else:
        group_id = uuid.uuid4()
        new_event.fusion_group_id = group_id
        # Back-fill related events
        for r in related:
            r.fusion_group_id = group_id


async def run_fusion(event_id: str) -> None:
    """Entry point called after an event is written to DB."""
    async with get_session() as session:
        result = await session.execute(select(Event).where(Event.id == uuid.UUID(event_id)))
        event = result.scalar_one_or_none()
        if event is None:
            return

        await check_and_mark_duplicate(event, session)
        if not event.is_duplicate:
            await assign_fusion_group(event, session)
