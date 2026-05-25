"""
Anomaly Detector — statistical outlier detection per district.

Method: rolling z-score on daily event counts.
  z = (today_count - mean_N_days) / std_N_days

If z > threshold (default 2.5), the district is flagged as anomalous
and an AnomalyRecord is written to the database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from storage.database import get_session
from storage.models import AnomalyRecord, Event

logger = structlog.get_logger(__name__)


async def _daily_count(
    district: str, day_start: datetime, session: AsyncSession
) -> int:
    day_end = day_start + timedelta(days=1)
    stmt = select(func.count(Event.id)).where(
        and_(
            Event.district == district,
            Event.ingested_at >= day_start,
            Event.ingested_at < day_end,
            Event.is_duplicate == False,  # noqa: E712
        )
    )
    result = await session.execute(stmt)
    return result.scalar() or 0


async def _get_baseline(
    district: str,
    lookback_days: int,
    session: AsyncSession,
) -> tuple[float, float, list[int]]:
    """
    Compute (mean, std, daily_counts) over the past lookback_days
    (excluding today).
    """
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = []
    for i in range(1, lookback_days + 1):
        day = today - timedelta(days=i)
        cnt = await _daily_count(district, day, session)
        counts.append(cnt)

    if not counts:
        return 0.0, 0.0, []

    mean = sum(counts) / len(counts)
    variance = sum((x - mean) ** 2 for x in counts) / len(counts)
    std = variance ** 0.5
    return mean, std, counts


async def detect_anomaly(
    district: str,
    state: str,
    session: AsyncSession,
) -> Optional[AnomalyRecord]:
    """
    Check if today's event count is anomalous for this district.
    Writes and returns an AnomalyRecord if anomaly detected, else None.
    """
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    current_count = await _daily_count(district, today, session)

    mean, std, baseline = await _get_baseline(
        district, settings.anomaly_lookback_days, session
    )

    if std < 0.5:
        # Too little variance — only flag if count is much higher than mean
        if current_count <= max(1, int(mean) + 2):
            return None
        z = float(current_count - mean) / max(0.5, std)
    else:
        z = (current_count - mean) / std

    if z < settings.anomaly_zscore_threshold:
        return None

    record = AnomalyRecord(
        district_name=district,
        state=state,
        zscore=round(z, 3),
        current_count=current_count,
        baseline_mean=round(mean, 3),
        baseline_std=round(std, 3),
        lookback_days=settings.anomaly_lookback_days,
        detected_at=datetime.now(timezone.utc),
    )
    session.add(record)
    await session.flush()

    logger.warning(
        "anomaly.detected",
        district=district,
        state=state,
        zscore=round(z, 2),
        today=current_count,
        baseline_mean=round(mean, 2),
    )
    return record


async def is_district_anomalous(district: str, session: AsyncSession) -> bool:
    """Check if there's a recent anomaly record for this district (last 24h)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    stmt = select(func.count(AnomalyRecord.id)).where(
        and_(
            AnomalyRecord.district_name == district,
            AnomalyRecord.detected_at >= cutoff,
        )
    )
    result = await session.execute(stmt)
    return (result.scalar() or 0) > 0


async def run_anomaly_detection_all() -> list[AnomalyRecord]:
    """Run anomaly detection for every active district."""
    from sqlalchemy import distinct

    anomalies = []
    async with get_session() as session:
        window = datetime.now(timezone.utc) - timedelta(days=settings.anomaly_lookback_days + 1)
        stmt = (
            select(distinct(Event.district), Event.state)
            .where(
                and_(
                    Event.district.isnot(None),
                    Event.ingested_at >= window,
                )
            )
        )
        result = await session.execute(stmt)
        rows = result.all()

        for district, state in rows:
            if district:
                record = await detect_anomaly(district, state or "", session)
                if record:
                    anomalies.append(record)

    logger.info("anomaly.scan_complete", flagged=len(anomalies))
    return anomalies
