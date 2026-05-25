"""
OpenSearch client: full-text indexing and geo/keyword search over events.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

import structlog
from opensearchpy import AsyncOpenSearch, NotFoundError, RequestError

from config.settings import settings

logger = structlog.get_logger(__name__)

_client: Optional[AsyncOpenSearch] = None


async def close() -> None:
    """Close the shared client (call at app shutdown to suppress aiohttp warnings)."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None

# Index mapping for events
EVENT_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "source": {"type": "keyword"},
            "source_url": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "standard"},
            "description": {"type": "text", "analyzer": "standard"},
            "event_type": {"type": "keyword"},
            "severity": {"type": "integer"},
            "confidence": {"type": "float"},
            "keywords": {"type": "keyword"},
            "language": {"type": "keyword"},
            "location_name": {"type": "text"},
            "district": {"type": "keyword"},
            "state": {"type": "keyword"},
            "coordinates": {"type": "geo_point"},
            "event_time": {"type": "date"},
            "ingested_at": {"type": "date"},
            "is_duplicate": {"type": "boolean"},
            "media_type": {"type": "keyword"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
}


def _make_client() -> AsyncOpenSearch:
    return AsyncOpenSearch(
        hosts=[{"host": settings.opensearch_host, "port": settings.opensearch_port}],
        http_compress=True,
        use_ssl=False,
        verify_certs=False,
    )


def get_opensearch() -> AsyncOpenSearch:
    """Return the shared AsyncOpenSearch client (used by FastAPI / long-lived loops)."""
    global _client
    if _client is None:
        _client = _make_client()
    return _client


async def ensure_index() -> None:
    """Create the events index with mapping if it doesn't exist."""
    client = get_opensearch()
    try:
        exists = await client.indices.exists(index=settings.opensearch_index_events)
        if not exists:
            await client.indices.create(
                index=settings.opensearch_index_events, body=EVENT_MAPPING
            )
            logger.info("opensearch.index_created", index=settings.opensearch_index_events)
    except RequestError as e:
        logger.error("opensearch.index_error", error=str(e))


async def index_event(event_doc: dict) -> None:
    """Index a single event document.

    Uses a fresh client instance so this function is safe to call from Celery
    tasks where each asyncio.run() creates a new event loop (the shared
    `_client` would have its aiohttp session bound to a dead loop).
    """
    # Always create a fresh client here — cheap for indexing, avoids the
    # "Event loop is closed" error that happens when the shared client's
    # aiohttp session was opened in a previous asyncio.run() call.
    client = _make_client()
    doc_id = str(event_doc.get("id", ""))
    try:
        await client.index(
            index=settings.opensearch_index_events,
            id=doc_id,
            body=event_doc,
        )
    except Exception as e:
        logger.error("opensearch.index_event_failed", id=doc_id, error=str(e))
    finally:
        await client.close()


async def search_events(
    query: str,
    district: Optional[str] = None,
    state: Optional[str] = None,
    event_type: Optional[str] = None,
    min_severity: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Full-text + filter search across indexed events."""
    client = get_opensearch()

    must: list[dict] = []
    filter_clauses: list[dict] = []

    if query:
        must.append(
            {
                "multi_match": {
                    "query": query,
                    "fields": ["title^2", "description", "keywords", "location_name"],
                }
            }
        )

    if district:
        filter_clauses.append({"term": {"district": district}})
    if state:
        filter_clauses.append({"term": {"state": state}})
    if event_type:
        filter_clauses.append({"term": {"event_type": event_type}})
    if min_severity:
        filter_clauses.append({"range": {"severity": {"gte": min_severity}}})

    body: dict[str, Any] = {
        "query": {
            "bool": {
                "must": must or [{"match_all": {}}],
                "filter": filter_clauses,
            }
        },
        "sort": [{"ingested_at": {"order": "desc"}}],
        "from": offset,
        "size": limit,
    }

    try:
        result = await client.search(
            index=settings.opensearch_index_events, body=body
        )
        hits = result["hits"]
        return {
            "total": hits["total"]["value"],
            "results": [h["_source"] for h in hits["hits"]],
        }
    except NotFoundError:
        return {"total": 0, "results": []}
    except Exception as e:
        logger.error("opensearch.search_failed", error=str(e))
        return {"total": 0, "results": []}


async def geo_search_events(
    lat: float,
    lon: float,
    radius_km: float = 10.0,
    limit: int = 50,
) -> list[dict]:
    """Proximity search: events within radius_km of (lat, lon)."""
    client = get_opensearch()
    body = {
        "query": {
            "bool": {
                "filter": [
                    {
                        "geo_distance": {
                            "distance": f"{radius_km}km",
                            "coordinates": {"lat": lat, "lon": lon},
                        }
                    }
                ]
            }
        },
        "sort": [{"ingested_at": {"order": "desc"}}],
        "size": limit,
    }
    try:
        result = await client.search(
            index=settings.opensearch_index_events, body=body
        )
        return [h["_source"] for h in result["hits"]["hits"]]
    except Exception as e:
        logger.error("opensearch.geo_search_failed", error=str(e))
        return []
