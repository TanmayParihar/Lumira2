"""
Velocity Scorer — measures how fast a district's threat level is rising.

Velocity = (current_hour_rate - baseline_rate) / baseline_std
Normalised to [0, 1]: 0 = at/below baseline, 1 = 3+ std deviations above.

Baseline = rolling 7-day hourly event rate for that district.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Event

logger = structlog.get_logger(__name__)


async def _hourly_count(
    district: str, start: datetime, end: datetime, session: AsyncSession
) -> int:
    stmt = select(func.count(Event.id)).where(
        and_(
            Event.district == district,
            Event.ingested_at >= start,
            Event.ingested_at < end,
            Event.is_duplicate == False,  # noqa: E712
        )
    )
    result = await session.execute(stmt)
    return result.scalar() or 0


async def get_velocity_score(district: str, session: AsyncSession) -> float:
    """
    Return velocity as a value in [0, 1].
    0.0  = event rate at or below baseline
    1.0  = event rate ≥ 3 standard deviations above baseline
    """
    now = datetime.now(timezone.utc)
    current_hour_start = now.replace(minute=0, second=0, microsecond=0)
    current_count = await _hourly_count(district, current_hour_start, now, session)

    # Build 7-day baseline of hourly counts
    baseline_counts = []
    for days_back in range(1, 8):  # days 1-7 ago
        for hour in range(24):
            h_start = (now - timedelta(days=days_back)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )
            h_end = h_start + timedelta(hours=1)
            count = await _hourly_count(district, h_start, h_end, session)
            baseline_counts.append(float(count))

    if not baseline_counts or len(baseline_counts) < 3:
        return 0.0

    mean = sum(baseline_counts) / len(baseline_counts)
    variance = sum((x - mean) ** 2 for x in baseline_counts) / len(baseline_counts)
    std = variance ** 0.5

    if std < 0.01:
        # Essentially zero variance — any event above mean is elevated
        return min(1.0, float(current_count) / max(1.0, mean + 1))

    z = (current_count - mean) / std
    # Map z-score to [0, 1]: z=0 → 0.0, z=3 → 1.0
    return max(0.0, min(1.0, z / 3.0))


async def get_velocity_for_all_districts(session: AsyncSession) -> dict[str, float]:
    """Compute velocity scores for every district with recent activity."""
    from sqlalchemy import distinct

    now = datetime.now(timezone.utc)
    window = now - timedelta(hours=48)
    stmt = select(distinct(Event.district)).where(
        and_(
            Event.district.isnot(None),
            Event.ingested_at >= window,
        )
    )
    result = await session.execute(stmt)
    districts = [r[0] for r in result.all() if r[0]]

    scores = {}
    for d in districts:
        scores[d] = await get_velocity_score(d, session)

    return scores
