#!/usr/bin/env python3
"""
Seed sample monitored assets (embassies, military bases, critical infrastructure).
These are publicly known locations for demonstration purposes.
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

SAMPLE_ASSETS = [
    # ── Embassies / High Commissions in Delhi ──────────────────────
    {"name": "US Embassy New Delhi", "type": "embassy", "lat": 28.5993, "lon": 77.1992, "loc": "Chanakyapuri, New Delhi", "radius": 2.0},
    {"name": "UK High Commission New Delhi", "type": "embassy", "lat": 28.5973, "lon": 77.2008, "loc": "Chanakyapuri, New Delhi", "radius": 2.0},
    {"name": "Chinese Embassy New Delhi", "type": "embassy", "lat": 28.5921, "lon": 77.1887, "loc": "Chanakyapuri, New Delhi", "radius": 2.0},
    # ── Major airports ─────────────────────────────────────────────
    {"name": "Indira Gandhi International Airport", "type": "infrastructure", "lat": 28.5562, "lon": 77.0999, "loc": "New Delhi", "radius": 5.0},
    {"name": "Chhatrapati Shivaji Maharaj International Airport", "type": "infrastructure", "lat": 19.0896, "lon": 72.8656, "loc": "Mumbai", "radius": 5.0},
    {"name": "Kempegowda International Airport", "type": "infrastructure", "lat": 13.1989, "lon": 77.7068, "loc": "Bengaluru", "radius": 5.0},
    {"name": "Chennai International Airport", "type": "infrastructure", "lat": 12.9941, "lon": 80.1709, "loc": "Chennai", "radius": 5.0},
    # ── Key government / Parliament ────────────────────────────────
    {"name": "Indian Parliament", "type": "government", "lat": 28.6177, "lon": 77.2081, "loc": "New Delhi", "radius": 3.0},
    {"name": "Rashtrapati Bhavan", "type": "government", "lat": 28.6143, "lon": 77.1993, "loc": "New Delhi", "radius": 3.0},
    {"name": "South Block / PMO", "type": "government", "lat": 28.6135, "lon": 77.2075, "loc": "New Delhi", "radius": 3.0},
    # ── Critical infrastructure ────────────────────────────────────
    {"name": "Mumbai Port", "type": "infrastructure", "lat": 18.9388, "lon": 72.8553, "loc": "Mumbai", "radius": 5.0},
    {"name": "ONGC Headquarters", "type": "infrastructure", "lat": 28.6330, "lon": 77.2219, "loc": "New Delhi", "radius": 3.0},
    # ── Line of Control / Border areas ────────────────────────────
    {"name": "Siachen Glacier Area", "type": "military", "lat": 35.2889, "lon": 76.9014, "loc": "Ladakh", "radius": 50.0},
    {"name": "Uri Sector (LoC)", "type": "military", "lat": 34.0814, "lon": 74.0470, "loc": "Baramulla, J&K", "radius": 30.0},
    {"name": "Wagah Border Crossing", "type": "military", "lat": 31.6049, "lon": 74.5727, "loc": "Amritsar, Punjab", "radius": 10.0},
]


async def seed() -> int:
    from geoalchemy2.elements import WKTElement
    from sqlalchemy import select

    from storage.database import get_session
    from storage.models import Asset

    count = 0
    async with get_session() as session:
        for a in SAMPLE_ASSETS:
            exists = await session.execute(
                select(Asset).where(Asset.name == a["name"])
            )
            if exists.scalar_one_or_none():
                continue

            asset = Asset(
                name=a["name"],
                asset_type=a["type"],
                location_name=a["loc"],
                coordinates=WKTElement(f"POINT({a['lon']} {a['lat']})", srid=4326),
                alert_radius_km=a["radius"],
                active=True,
            )
            session.add(asset)
            count += 1

    print(f"Seeded {count} assets")
    return count


if __name__ == "__main__":
    asyncio.run(seed())
