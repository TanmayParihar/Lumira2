"""
Proximity Alert Engine.

When a new event is geocoded, this module checks whether it falls
within the alert_radius_km of any active asset. If so, it creates
a ProximityAlert row and publishes it to Redis pub/sub.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import get_session
from storage.models import Asset, Event, ProximityAlert
from storage.redis_client import CHANNEL_ALERTS, publish

logger = structlog.get_logger(__name__)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


async def _get_asset_coords(asset: Asset) -> tuple[float, float] | None:
    """Extract (lat, lon) from an Asset's PostGIS geometry."""
    if asset.coordinates is None:
        return None
    try:
        from geoalchemy2.shape import to_shape
        point = to_shape(asset.coordinates)
        return point.y, point.x  # (lat, lon)
    except Exception:
        return None


async def check_proximity_for_event(
    event_id: str,
    session: AsyncSession,
) -> List[ProximityAlert]:
    """
    Check all active assets against a newly ingested event.
    Creates ProximityAlert records for every breach and publishes to Redis.
    """
    event = await session.get(Event, uuid.UUID(event_id))
    if event is None or event.is_duplicate:
        return []

    if not event.latitude or not event.longitude:
        return []

    # Get all active assets
    stmt = select(Asset).where(Asset.active == True)  # noqa: E712
    result = await session.execute(stmt)
    assets = result.scalars().all()

    alerts: List[ProximityAlert] = []

    for asset in assets:
        asset_coords = await _get_asset_coords(asset)
        if asset_coords is None:
            continue

        asset_lat, asset_lon = asset_coords
        dist_km = _haversine_km(
            event.latitude, event.longitude, asset_lat, asset_lon
        )

        if dist_km <= asset.alert_radius_km:
            alert = ProximityAlert(
                event_id=event.id,
                asset_id=asset.id,
                distance_km=round(dist_km, 3),
                severity=event.severity,
                acknowledged=False,
                created_at=datetime.now(timezone.utc),
            )
            session.add(alert)
            alerts.append(alert)

            logger.warning(
                "proximity_alert.created",
                event_id=event_id,
                asset=asset.name,
                asset_type=asset.asset_type,
                distance_km=round(dist_km, 2),
                severity=event.severity,
            )

    if alerts:
        await session.flush()
        # Publish to Redis so the API can push to connected clients
        await publish(
            CHANNEL_ALERTS,
            {
                "event_id": event_id,
                "event_type": event.event_type,
                "severity": event.severity,
                "location": event.location_name,
                "alerts": [
                    {
                        "alert_id": str(a.id),
                        "asset_id": str(a.asset_id),
                        "distance_km": a.distance_km,
                    }
                    for a in alerts
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    return alerts


async def run_proximity_check(event_id: str) -> int:
    """Entry point for Celery task."""
    async with get_session() as session:
        alerts = await check_proximity_for_event(event_id, session)
    return len(alerts)
