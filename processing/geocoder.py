"""
Geocoding module using local Nominatim with online fallback.

Resolves location names extracted by the NER pipeline into
(lat, lon, district, state) tuples for PostGIS storage.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from processing.schemas import GeocodedLocation, LocationEntity

logger = structlog.get_logger(__name__)

# In-memory geocode cache
_cache: dict[str, Optional[GeocodedLocation]] = {}

INDIA_BOUNDS = {
    "viewbox": "68.1766451354,7.96553477623,97.4025614766,35.4940095078",
    "bounded": "1",
}


async def _nominatim_search(name: str, base_url: str) -> Optional[dict]:
    """Query Nominatim and return the best result dict."""
    params = {
        "q": f"{name}, India",
        "format": "jsonv2",
        "limit": "3",
        "addressdetails": "1",
        **INDIA_BOUNDS,
    }
    headers = {"User-Agent": "LumiraIntelligence/1.0"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/search", params=params, headers=headers
            )
            resp.raise_for_status()
            results = resp.json()
            if results:
                return results[0]
    except Exception as e:
        logger.debug("geocoder.nominatim_failed", url=base_url, name=name, error=str(e))
    return None


def _extract_admin(address: dict) -> tuple[Optional[str], Optional[str]]:
    """Extract district and state from Nominatim address dict."""
    district = (
        address.get("county")
        or address.get("state_district")
        or address.get("district")
        or address.get("suburb")
    )
    state = address.get("state")
    return district, state


async def geocode(name: str) -> Optional[GeocodedLocation]:
    """
    Resolve a place name to coordinates + admin hierarchy.
    Tries local Nominatim first, falls back to public OSM API.
    """
    if not name or not name.strip():
        return None

    cache_key = name.strip().lower()
    if cache_key in _cache:
        return _cache[cache_key]

    result: Optional[dict] = None

    # 1. Try local Nominatim
    try:
        result = await _nominatim_search(name, settings.nominatim_url)
    except Exception:
        pass

    # 2. Fallback to public OSM API
    if result is None and settings.geocode_fallback_online:
        await asyncio.sleep(1)  # OSM rate limit: 1 req/sec
        try:
            result = await _nominatim_search(
                name, "https://nominatim.openstreetmap.org"
            )
        except Exception:
            pass

    if result is None:
        _cache[cache_key] = None
        return None

    address = result.get("address", {})
    district, state = _extract_admin(address)

    geo = GeocodedLocation(
        input_name=name,
        resolved_name=result.get("display_name", name),
        district=district,
        state=state,
        country=address.get("country", "India"),
        latitude=float(result["lat"]) if result.get("lat") else None,
        longitude=float(result["lon"]) if result.get("lon") else None,
        confidence=min(1.0, float(result.get("importance", 0.5))),
    )
    _cache[cache_key] = geo
    return geo


async def geocode_locations(
    locations: list[LocationEntity],
) -> Optional[GeocodedLocation]:
    """
    Try each extracted location entity and return the best resolved one.
    Priority: district > city > state > landmark
    """
    priority_order = ["district", "city", "state", "region", "landmark", "unknown"]
    sorted_locs = sorted(
        locations,
        key=lambda l: priority_order.index(l.entity_type)
        if l.entity_type in priority_order
        else len(priority_order),
    )

    for loc in sorted_locs:
        geo = await geocode(loc.name)
        if geo and geo.latitude and geo.longitude:
            return geo

    return None
