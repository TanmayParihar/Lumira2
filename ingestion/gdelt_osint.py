"""
GDELT 2.0 Social OSINT ingester.
GDELT (https://www.gdeltproject.org/) monitors world events from news media.
We poll the 15-minute event files and filter for India (actor/location codes IND).

No API key required — fully open data.
"""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import datetime, timezone
from typing import List, Set

import httpx
import structlog

from config.settings import settings
from ingestion.base import BaseIngester, RawItem

logger = structlog.get_logger(__name__)

GDELT_LAST_UPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

# GDELT event CSV columns (GKG subset used)
# Full schema: http://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf
COL_EVENTID = 0
COL_DATE = 1
COL_ACTOR1 = 5
COL_ACTOR2 = 15
COL_EVENTCODE = 26
COL_GOLDSTEIN = 30
COL_NUMSOURCES = 31
COL_AVGTONE = 34
COL_GEO_TYPE = 35
COL_GEO_FULLNAME = 36
COL_GEO_COUNTRY = 37
COL_LAT = 39
COL_LON = 40
COL_SOURCEURL = 57

INDIA_GEO_CODE = "IN"
INDIA_ACTOR_CODES = {"IND", "IN"}

_seen: Set[str] = set()


def _to_event_type(goldstein: float, eventcode: str) -> str:
    """Map GDELT Goldstein scale and CAMEO event code to our event type."""
    code_prefix = eventcode[:2] if len(eventcode) >= 2 else ""
    # CAMEO codes: 14=protest, 18-19=assault/fight, 20=mass violence
    if code_prefix in ("18", "19", "20"):
        return "VIOLENCE"
    if code_prefix == "14":
        return "PROTEST"
    if code_prefix == "17":
        return "CRIME"
    if goldstein < -7:
        return "VIOLENCE"
    if goldstein < -4:
        return "PROTEST"
    return "POLITICAL"


async def _get_latest_file_url() -> str | None:
    """Fetch GDELT's lastupdate.txt to find the latest event zip URL."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(GDELT_LAST_UPDATE_URL)
            resp.raise_for_status()
            for line in resp.text.strip().splitlines():
                parts = line.split()
                if len(parts) >= 3 and "export.CSV.zip" in parts[2]:
                    return parts[2]
    except Exception as e:
        logger.error("gdelt.lastupdate_failed", error=str(e))
    return None


async def _download_events(url: str) -> list[list[str]]:
    """Download and unzip GDELT event CSV."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t")
            return list(reader)


class GDELTIngester(BaseIngester):
    source_name = "GDELT"

    async def fetch(self) -> List[RawItem]:
        url = await _get_latest_file_url()
        if not url:
            return []

        url_hash = hashlib.sha1(url.encode()).hexdigest()
        if url_hash in _seen:
            logger.debug("gdelt.already_processed", url=url)
            return []
        _seen.add(url_hash)

        try:
            rows = await _download_events(url)
        except Exception as e:
            logger.error("gdelt.download_failed", url=url, error=str(e))
            return []

        items: List[RawItem] = []
        for row in rows:
            if len(row) <= COL_SOURCEURL:
                continue

            # Filter to India events
            geo_country = row[COL_GEO_COUNTRY].strip().upper()
            actor1 = row[COL_ACTOR1][:3].upper() if len(row[COL_ACTOR1]) >= 3 else ""
            if geo_country != INDIA_GEO_CODE and actor1 not in INDIA_ACTOR_CODES:
                continue

            source_url = row[COL_SOURCEURL].strip()
            h = hashlib.sha1(source_url.encode()).hexdigest()
            if h in _seen:
                continue
            _seen.add(h)

            # Parse date
            date_str = row[COL_DATE][:8]
            try:
                pub_dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
            except Exception:
                pub_dt = datetime.now(tz=timezone.utc)

            try:
                goldstein = float(row[COL_GOLDSTEIN]) if row[COL_GOLDSTEIN] else 0.0
            except ValueError:
                goldstein = 0.0

            event_code = row[COL_EVENTCODE].strip()
            geo_name = row[COL_GEO_FULLNAME].strip()
            avg_tone = row[COL_AVGTONE].strip()

            try:
                lat = float(row[COL_LAT]) if row[COL_LAT] else None
                lon = float(row[COL_LON]) if row[COL_LON] else None
            except ValueError:
                lat, lon = None, None

            event_type = _to_event_type(goldstein, event_code)
            raw_content = (
                f"GDELT Event: {event_type}\n"
                f"Location: {geo_name}\n"
                f"Goldstein Scale: {goldstein}\n"
                f"CAMEO Code: {event_code}\n"
                f"Average Tone: {avg_tone}\n"
                f"Source: {source_url}"
            )

            items.append(
                RawItem(
                    source="GDELT",
                    media_type="text",
                    title=f"GDELT: {event_type} in {geo_name}",
                    raw_content=raw_content,
                    source_url=source_url,
                    published_at=pub_dt,
                    metadata={
                        "gdelt_event_code": event_code,
                        "goldstein": goldstein,
                        "geo_name": geo_name,
                        "lat": lat,
                        "lon": lon,
                        "event_type_hint": event_type,
                    },
                )
            )

        logger.info("gdelt.fetched", count=len(items), file_url=url)
        return items
