"""Threats router — DTI scores, anomalies, velocity, map data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import DTIMapItem, DTIOut
from storage.database import get_db
from storage.models import AnomalyRecord, DistrictThreatIndex
from storage.redis_client import get_all_dti_scores

router = APIRouter(prefix="/threats", tags=["threats"])


@router.get("/dti", response_model=List[DTIOut])
async def get_latest_dti(
    state: Optional[str] = Query(None),
    min_score: float = Query(default=0.0, ge=0, le=100),
    limit: int = Query(default=100, le=500),
    session: AsyncSession = Depends(get_db),
):
    """
    Return the most recent DTI record per district.
    Uses a DISTINCT ON query for efficiency.
    """
    from sqlalchemy import text

    # Subquery: latest computed_at per district
    subq = (
        select(
            DistrictThreatIndex.district_name,
            func.max(DistrictThreatIndex.computed_at).label("latest"),
        )
        .group_by(DistrictThreatIndex.district_name)
        .subquery()
    )

    stmt = (
        select(DistrictThreatIndex)
        .join(
            subq,
            and_(
                DistrictThreatIndex.district_name == subq.c.district_name,
                DistrictThreatIndex.computed_at == subq.c.latest,
            ),
        )
        .where(DistrictThreatIndex.dti_score >= min_score)
        .order_by(DistrictThreatIndex.dti_score.desc())
        .limit(limit)
    )

    if state:
        stmt = stmt.where(DistrictThreatIndex.state == state)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [DTIOut.model_validate(r) for r in rows]


@router.get("/dti/map", response_model=List[DTIMapItem])
async def get_dti_map():
    """
    Fast Redis-backed DTI snapshot for map rendering.
    Returns district → score mapping.
    """
    scores = await get_all_dti_scores()
    return [
        DTIMapItem(
            district=k,
            state="",
            dti_score=v,
            is_anomaly=v >= 70,
            event_count=0,
        )
        for k, v in scores.items()
    ]


@router.get("/dti/{district}", response_model=DTIOut)
async def get_district_dti(
    district: str,
    session: AsyncSession = Depends(get_db),
):
    """Get the latest DTI record for a specific district."""
    stmt = (
        select(DistrictThreatIndex)
        .where(DistrictThreatIndex.district_name == district)
        .order_by(DistrictThreatIndex.computed_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"No DTI data for district: {district}")
    return DTIOut.model_validate(row)


@router.get("/anomalies", response_model=List[dict])
async def get_recent_anomalies(
    hours: int = Query(default=24, ge=1, le=168),
    session: AsyncSession = Depends(get_db),
):
    """Return recently detected anomalies."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(AnomalyRecord)
        .where(AnomalyRecord.detected_at >= since)
        .order_by(AnomalyRecord.detected_at.desc())
        .limit(100)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "district": r.district_name,
            "state": r.state,
            "zscore": r.zscore,
            "current_count": r.current_count,
            "baseline_mean": r.baseline_mean,
            "baseline_std": r.baseline_std,
            "detected_at": r.detected_at.isoformat() if r.detected_at else None,
        }
        for r in rows
    ]


@router.post("/dti/trigger")
async def trigger_dti_update():
    """Manually trigger a DTI recalculation."""
    from workers.tasks import intelligence_update_dti
    task = intelligence_update_dti.apply_async(queue="intelligence")
    return {"task_id": task.id, "status": "queued"}
