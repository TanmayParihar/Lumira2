"""
Shared pytest fixtures.

Provides:
  - async_session   : in-memory SQLite session (no PostgreSQL needed)
  - mock_ollama     : patches httpx to return canned Ollama JSON
  - mock_nominatim  : patches geocoder to return fixed coords
  - test_event      : a pre-built Event ORM object
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from storage.models import Base, Event


# ── In-memory async DB (SQLite — no PostGIS, so geometry columns are skipped)
@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    # SQLite doesn't know Geometry — swap it out for Text during tests
    from unittest.mock import patch
    import geoalchemy2
    with patch.object(geoalchemy2, "Geometry", lambda *a, **kw: __import__("sqlalchemy").Text()):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session
    await engine.dispose()


# ── Canned Ollama response ────────────────────────────────────────────────
MOCK_LLM_RESPONSE = json.dumps({
    "event_type": "VIOLENCE",
    "severity": 4,
    "confidence": 0.88,
    "description": "Armed clashes reported near the Line of Control in Kupwara district.",
    "locations": [
        {"name": "Kupwara", "entity_type": "district"},
        {"name": "Jammu and Kashmir", "entity_type": "state"},
    ],
    "entities": {
        "people": [],
        "organizations": ["Indian Army"],
        "keywords": ["armed clash", "LoC", "firing"],
    },
    "language": "en",
})


@pytest.fixture
def mock_ollama():
    """Patch httpx.AsyncClient so Ollama calls return canned JSON instantly."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": MOCK_LLM_RESPONSE}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture
def mock_nominatim():
    """Patch geocoder to always return Kupwara coords."""
    from processing.schemas import GeocodedLocation
    geo = GeocodedLocation(
        input_name="Kupwara",
        resolved_name="Kupwara, Jammu and Kashmir, India",
        district="Kupwara",
        state="Jammu and Kashmir",
        country="India",
        latitude=34.5212,
        longitude=74.2604,
        confidence=0.9,
    )
    with patch("processing.geocoder.geocode", AsyncMock(return_value=geo)):
        with patch("processing.geocoder.geocode_locations", AsyncMock(return_value=geo)):
            yield geo


# ── Canned Event ──────────────────────────────────────────────────────────
@pytest.fixture
def test_event():
    return Event(
        id=uuid.uuid4(),
        source="RSS",
        source_url="https://example.com/news/1",
        title="Armed clashes near LoC in Kupwara",
        media_type="text",
        event_type="VIOLENCE",
        severity=4,
        confidence=0.88,
        description="Armed clashes reported near the Line of Control in Kupwara district.",
        keywords=["armed clash", "LoC", "firing"],
        language="en",
        location_name="Kupwara, Jammu and Kashmir",
        district="Kupwara",
        state="Jammu and Kashmir",
        latitude=34.5212,
        longitude=74.2604,
        event_time=datetime.now(timezone.utc),
        ingested_at=datetime.now(timezone.utc),
        is_duplicate=False,
    )
