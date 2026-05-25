"""
Celery task definitions.

All tasks are intentionally synchronous at the Celery layer —
async pipelines are run via asyncio.run() inside each task.
The heavy async code lives in processing/ and intelligence/.
"""
from __future__ import annotations

import asyncio
import os
import sys

# Make sure the project root is importable in every forked worker process.
# This is needed when Celery is launched via `.venv/bin/celery` (not python -m)
# because the CWD is not automatically added to sys.path in forked subprocesses.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis
import structlog

from workers.celery_app import app

logger = structlog.get_logger(__name__)

_rdb = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

def _svc_enabled(name: str) -> bool:
    """Check Redis flag; default True if key absent or Redis unreachable."""
    try:
        return _rdb.get(f"lumira:svc:{name}") != "0"
    except Exception:
        return True


# ════════════════════════════════════════════════════════════════
#  INGESTION TASKS
# ════════════════════════════════════════════════════════════════

@app.task(name="workers.tasks.ingest_rss", bind=True, max_retries=2)
def ingest_rss(self):
    """Crawl all configured RSS feeds and queue items for processing."""
    if not _svc_enabled("rss"):
        logger.info("task.ingest_rss.skipped", reason="disabled_via_dashboard")
        return {"skipped": True}
    async def _run():
        from ingestion.rss_web import RSSIngester
        ingester = RSSIngester()
        items = await ingester.run()
        for item in items:
            await _save_and_queue(item)
        return len(items)

    count = asyncio.run(_run())
    logger.info("task.ingest_rss.done", count=count)
    return {"ingested": count}


@app.task(name="workers.tasks.ingest_newsapi", bind=True, max_retries=2)
def ingest_newsapi(self):
    if not _svc_enabled("newsapi"):
        logger.info("task.ingest_newsapi.skipped", reason="disabled_via_dashboard")
        return {"skipped": True}
    async def _run():
        from ingestion.newsapi_client import NewsAPIIngester
        ingester = NewsAPIIngester()
        items = await ingester.run()
        for item in items:
            await _save_and_queue(item)
        return len(items)

    count = asyncio.run(_run())
    logger.info("task.ingest_newsapi.done", count=count)
    return {"ingested": count}


@app.task(name="workers.tasks.ingest_serper", bind=True, max_retries=2)
def ingest_serper(self):
    if not _svc_enabled("serper"):
        logger.info("task.ingest_serper.skipped", reason="disabled_via_dashboard")
        return {"skipped": True}
    async def _run():
        from ingestion.serper_client import SerperIngester
        ingester = SerperIngester()
        items = await ingester.run()
        for item in items:
            await _save_and_queue(item)
        return len(items)

    count = asyncio.run(_run())
    logger.info("task.ingest_serper.done", count=count)
    return {"ingested": count}


@app.task(name="workers.tasks.ingest_gdelt", bind=True, max_retries=2)
def ingest_gdelt(self):
    if not _svc_enabled("gdelt"):
        logger.info("task.ingest_gdelt.skipped", reason="disabled_via_dashboard")
        return {"skipped": True}
    async def _run():
        from ingestion.gdelt_osint import GDELTIngester
        ingester = GDELTIngester()
        items = await ingester.run()
        for item in items:
            await _save_and_queue(item)
        return len(items)

    count = asyncio.run(_run())
    logger.info("task.ingest_gdelt.done", count=count)
    return {"ingested": count}


@app.task(name="workers.tasks.ingest_radio", bind=True, max_retries=1)
def ingest_radio(self):
    async def _run():
        from ingestion.radio_stream import RadioIngester
        ingester = RadioIngester()
        items = await ingester.run()
        for item in items:
            await _save_and_queue(item)
        return len(items)

    count = asyncio.run(_run())
    logger.info("task.ingest_radio.done", count=count)
    return {"ingested": count}


# ════════════════════════════════════════════════════════════════
#  PROCESSING TASKS
# ════════════════════════════════════════════════════════════════

@app.task(name="workers.tasks.process_raw_item", bind=True, max_retries=3)
def process_raw_item(self, raw_id: str):
    """Process a single raw ingestion record through the appropriate pipeline."""
    async def _run():
        return await _process_one(raw_id)

    result = asyncio.run(_run())
    logger.info("task.process_raw_item.done", raw_id=raw_id, event_id=result)
    return {"raw_id": raw_id, "event_id": result}


async def _process_one(raw_id: str) -> Optional[str]:
    """Core processing logic — returns event_id or None on failure."""
    from sqlalchemy import select

    from intelligence.event_fusion import run_fusion
    from intelligence.proximity_alerts import run_proximity_check
    from processing.audio_pipeline import process_audio
    from processing.geocoder import geocode_locations
    from processing.image_pipeline import process_image
    from processing.text_pipeline import analyze_text
    from processing.video_pipeline import process_video
    from storage.database import get_worker_session
    from storage.models import Event, RawIngestion
    from storage.opensearch_client import index_event

    async with get_worker_session() as session:
        raw = await session.get(RawIngestion, uuid.UUID(raw_id))
        if raw is None or raw.processed:
            return None

        media_type = raw.media_type

        try:
            if media_type == "text":
                text_result = await analyze_text(raw.raw_content or raw.title or "")
                transcription_text = None

            elif media_type == "audio":
                transcription, text_result = await process_audio(minio_path=raw.media_path)
                transcription_text = transcription.text

            elif media_type == "image":
                _, text_result = await process_image(minio_path=raw.media_path)
                transcription_text = None

            elif media_type == "video":
                _, transcription, text_result = await process_video(minio_path=raw.media_path)
                transcription_text = transcription.text

            else:
                logger.warning("tasks.unknown_media_type", media_type=media_type)
                raw.processed = True
                raw.processing_error = f"Unknown media_type: {media_type}"
                return None

            # Geocode
            geo = await geocode_locations(text_result.locations)

            event = Event(
                raw_id=raw.id,
                source=raw.source,
                source_url=raw.source_url or "",
                title=raw.title or "",
                media_type=media_type,
                media_path=raw.media_path or "",
                event_type=text_result.event_type,
                severity=text_result.severity,
                confidence=text_result.confidence,
                description=transcription_text or text_result.description,
                entities={
                    "people": text_result.entities.people,
                    "organizations": text_result.entities.organizations,
                    "keywords": text_result.entities.keywords,
                },
                keywords=text_result.entities.keywords[:20],
                language=text_result.language,
                location_raw=[loc.dict() for loc in text_result.locations],
                location_name=geo.resolved_name if geo else None,
                district=geo.district if geo else None,
                state=geo.state if geo else None,
                event_time=raw.ingested_at,
                ingested_at=datetime.now(timezone.utc),
            )

            # Store lat/lon in both flat columns and PostGIS geometry
            if geo and geo.latitude is not None:
                event.latitude = geo.latitude
                event.longitude = geo.longitude
                from geoalchemy2.elements import WKTElement
                event.coordinates = WKTElement(
                    f"POINT({geo.longitude} {geo.latitude})", srid=4326
                )

            session.add(event)
            raw.processed = True
            await session.flush()

            event_id = str(event.id)

            # Index in OpenSearch
            doc = {
                "id": event_id,
                "source": event.source,
                "source_url": event.source_url,
                "title": event.title,
                "description": event.description,
                "event_type": event.event_type,
                "severity": event.severity,
                "confidence": event.confidence,
                "keywords": event.keywords or [],
                "language": event.language,
                "location_name": event.location_name,
                "district": event.district,
                "state": event.state,
                "coordinates": {"lat": event.latitude, "lon": event.longitude} if event.latitude else None,
                "event_time": event.event_time.isoformat() if event.event_time else None,
                "ingested_at": event.ingested_at.isoformat(),
                "is_duplicate": event.is_duplicate,
                "media_type": event.media_type,
            }
            await index_event(doc)

        except Exception as e:
            logger.error("tasks.process_failed", raw_id=raw_id, error=str(e))
            raw.processing_error = str(e)
            return None

    # Post-processing: fusion + proximity (outside the main session)
    if event_id:
        await run_fusion(event_id)
        await run_proximity_check(event_id)

    return event_id


# ════════════════════════════════════════════════════════════════
#  INTELLIGENCE TASKS
# ════════════════════════════════════════════════════════════════

@app.task(name="workers.tasks.intelligence_update_dti")
def intelligence_update_dti():
    async def _run():
        from intelligence.threat_index import update_all_district_scores
        await update_all_district_scores()

    asyncio.run(_run())
    logger.info("task.dti_update.done")
    return {"status": "ok"}


@app.task(name="workers.tasks.intelligence_anomaly_scan")
def intelligence_anomaly_scan():
    async def _run():
        from intelligence.anomaly_detector import run_anomaly_detection_all
        records = await run_anomaly_detection_all()
        return len(records)

    count = asyncio.run(_run())
    logger.info("task.anomaly_scan.done", anomalies=count)
    return {"anomalies_detected": count}


# ════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════

async def _save_and_queue(item) -> str:
    """Persist a RawItem to the DB, then dispatch a processing task."""
    from storage.database import get_worker_session
    from storage.models import RawIngestion

    async with get_worker_session() as session:
        raw = RawIngestion(
            source=item.source,
            source_url=item.source_url,
            title=item.title,
            raw_content=item.raw_content,
            media_type=item.media_type,
            media_path=item.media_path,
            extra_meta=item.metadata,
            ingested_at=item.published_at or datetime.now(timezone.utc),
        )
        session.add(raw)
        await session.flush()
        raw_id = str(raw.id)

    # Fire-and-forget Celery task
    process_raw_item.apply_async(args=[raw_id], queue="processing")
    return raw_id
