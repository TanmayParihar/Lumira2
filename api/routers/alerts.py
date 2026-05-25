"""Proximity alerts router."""
from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import AlertAcknowledge, AlertOut
from storage.database import get_db
from storage.models import Asset, Event, ProximityAlert

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=List[AlertOut])
async def list_alerts(
    acknowledged: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    session: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ProximityAlert)
        .where(ProximityAlert.acknowledged == acknowledged)
        .order_by(ProximityAlert.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    alerts = result.scalars().all()

    out = []
    for alert in alerts:
        asset = await session.get(Asset, alert.asset_id)
        out.append(
            AlertOut(
                id=alert.id,
                event_id=alert.event_id,
                asset_id=alert.asset_id,
                asset_name=asset.name if asset else None,
                asset_type=asset.asset_type if asset else None,
                distance_km=alert.distance_km,
                severity=alert.severity,
                acknowledged=alert.acknowledged,
                created_at=alert.created_at,
            )
        )
    return out


@router.post("/acknowledge")
async def acknowledge_alerts(
    body: AlertAcknowledge,
    session: AsyncSession = Depends(get_db),
):
    stmt = select(ProximityAlert).where(
        ProximityAlert.id.in_(body.alert_ids)
    )
    result = await session.execute(stmt)
    alerts = result.scalars().all()
    for alert in alerts:
        alert.acknowledged = True
    return {"acknowledged": len(alerts)}


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: UUID, session: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException

    alert = await session.get(ProximityAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    asset = await session.get(Asset, alert.asset_id)
    return AlertOut(
        id=alert.id,
        event_id=alert.event_id,
        asset_id=alert.asset_id,
        asset_name=asset.name if asset else None,
        asset_type=asset.asset_type if asset else None,
        distance_km=alert.distance_km,
        severity=alert.severity,
        acknowledged=alert.acknowledged,
        created_at=alert.created_at,
    )
