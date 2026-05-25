"""
District Threat Index (DTI) calculator.

DTI = frequency_score (0-30)
    + severity_score  (0-40)
    + velocity_score  (0-20)
    + anomaly_bonus   (0-10)
    ────────────────────────
    Total             0-100

Frequency:  event count in last 24h, capped at 15 events = 30pts
Severity:   recency-weighted mean severity × 8 (max 5×8=40pts)
Velocity:   normalised z-score from velocity_scorer, mapped to 0-20
Anomaly:    +10 if statistical anomaly is flagged for the district
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from math import exp
from typing import Optional

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from storage.database import get_session
from storage.models import DistrictThreatIndex, Event
from storage.redis_client import store_dti_snapshot

logger = structlog.get_logger(__name__)

HALF_LIFE_HOURS = 12.0
DECAY_LAMBDA = 0.6931 / HALF_LIFE_HOURS  # ln(2) / half-life


def _recency_weight(event_time: datetime) -> float:
    """Exponential decay based on hours since the event."""
    hours_ago = max(
        0.0,
        (datetime.now(timezone.utc) - event_time).total_seconds() / 3600,
    )
    return exp(-DECAY_LAMBDA * hours_ago)


async def _get_district_events(
    district: str, window_hours: int, session: AsyncSession
) -> list[Event]:
    window_start = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    stmt = select(Event).where(
        and_(
            Event.district == district,
            Event.ingested_at >= window_start,
            Event.is_duplicate == False,  # noqa: E712
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def _calculate_velocity_score(district: str, session: AsyncSession) -> float:
    """Return velocity as 0-1 float (imported from velocity_scorer)."""
    try:
        from intelligence.velocity_scorer import get_velocity_score
        return await get_velocity_score(district, session)
    except Exception:
        return 0.0


async def _check_anomaly(district: str, session: AsyncSession) -> bool:
    """Return True if this district has an active anomaly."""
    try:
        from intelligence.anomaly_detector import is_district_anomalous
        return await is_district_anomalous(district, session)
    except Exception:
        return False


async def calculate_dti(district: str, state: str, session: AsyncSession) -> DistrictThreatIndex:
    """Compute and store the DTI for a single district."""
    events = await _get_district_events(district, settings.dti_window_hours, session)

    event_count = len(events)

    # ── Frequency score (0-30) ───────────────────────────────────────────
    frequency_score = min(30.0, event_count * 2.0)

    # ── Severity score (0-40) ────────────────────────────────────────────
    if events:
        weighted_severities = []
        for ev in events:
            sev = ev.severity or 1
            rw = _recency_weight(ev.ingested_at)
            weighted_severities.append(sev * rw)
        avg_weighted_severity = sum(weighted_severities) / len(weighted_severities)
        severity_score = min(40.0, avg_weighted_severity * 8.0)
        avg_severity = sum(e.severity or 1 for e in events) / len(events)
    else:
        severity_score = 0.0
        avg_severity = 0.0

    # ── Velocity score (0-20) ─────────────────────────────────────────────
    velocity_norm = await _calculate_velocity_score(district, session)
    velocity_score = min(20.0, max(0.0, velocity_norm * 20.0))

    # ── Anomaly bonus (0-10) ──────────────────────────────────────────────
    is_anomaly = await _check_anomaly(district, session)
    anomaly_bonus = 10.0 if is_anomaly else 0.0

    dti_score = frequency_score + severity_score + velocity_score + anomaly_bonus

    record = DistrictThreatIndex(
        district_name=district,
        state=state,
        dti_score=round(dti_score, 2),
        frequency_score=round(frequency_score, 2),
        severity_score=round(severity_score, 2),
        velocity_score=round(velocity_score, 2),
        anomaly_bonus=anomaly_bonus,
        event_count_24h=event_count,
        avg_severity=round(avg_severity, 2),
        is_anomaly=is_anomaly,
        velocity=round(velocity_norm, 3),
        computed_at=datetime.now(timezone.utc),
    )
    session.add(record)
    await session.flush()

    # Cache in Redis for instant dashboard reads
    await store_dti_snapshot(district, dti_score)

    logger.info(
        "dti.calculated",
        district=district,
        state=state,
        dti=round(dti_score, 2),
        events=event_count,
        anomaly=is_anomaly,
    )
    return record


async def update_all_district_scores() -> None:
    """
    Recalculate DTI for every district that has events in the last 48h.
    Also recalculates any district that previously had a non-zero score.
    """
    from sqlalchemy import distinct

    async with get_session() as session:
        window = datetime.now(timezone.utc) - timedelta(hours=48)

        # Districts with recent events
        stmt = select(
            distinct(Event.district), Event.state
        ).where(
            and_(
                Event.district.isnot(None),
                Event.ingested_at >= window,
            )
        )
        result = await session.execute(stmt)
        rows = result.all()

        for district, state in rows:
            if district and state:
                await calculate_dti(district, state or "", session)

    logger.info("dti.update_complete", districts_updated=len(rows))
