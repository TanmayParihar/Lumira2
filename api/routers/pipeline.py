"""Pipeline control & status router + live test endpoints for the dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import PipelineStatus, SearchRequest, SearchResponse
from storage.database import get_db
from storage.models import Event, ProximityAlert
from storage.opensearch_client import search_events
from storage.redis_client import get_queue_length

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/status", response_model=PipelineStatus)
async def get_pipeline_status(session: AsyncSession = Depends(get_db)):
    """Health check across all services + live pipeline metrics."""
    from config.settings import settings

    # PostgreSQL
    try:
        await session.execute(select(func.now()))
        pg_ok = True
    except Exception:
        pg_ok = False

    # Redis
    try:
        from storage.redis_client import get_redis
        r = await get_redis()
        await r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    # OpenSearch
    try:
        from storage.opensearch_client import get_opensearch
        client = get_opensearch()
        info = await client.info()
        os_ok = True
    except Exception:
        os_ok = False

    # MinIO
    try:
        from storage.minio_client import get_minio
        get_minio().list_buckets()
        minio_ok = True
    except Exception:
        minio_ok = False

    # Ollama
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.ollama_url}/api/tags")
            ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False

    # Queue depth
    try:
        queue_depth = await get_queue_length()
    except Exception:
        queue_depth = -1

    # Event counts
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    total_events = (
        await session.execute(select(func.count(Event.id)))
    ).scalar() or 0
    events_24h = (
        await session.execute(
            select(func.count(Event.id)).where(Event.ingested_at >= since_24h)
        )
    ).scalar() or 0
    active_alerts = (
        await session.execute(
            select(func.count(ProximityAlert.id)).where(
                ProximityAlert.acknowledged == False  # noqa: E712
            )
        )
    ).scalar() or 0

    return PipelineStatus(
        postgres=pg_ok,
        redis=redis_ok,
        opensearch=os_ok,
        minio=minio_ok,
        ollama_text_model=ollama_ok,
        queue_depth=queue_depth,
        total_events=total_events,
        events_last_24h=events_24h,
        active_alerts=active_alerts,
    )


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest):
    """Full-text event search via OpenSearch."""
    result = await search_events(
        query=body.query,
        district=body.district,
        state=body.state,
        event_type=body.event_type,
        min_severity=body.min_severity,
        limit=body.limit,
        offset=body.offset,
    )
    return SearchResponse(total=result["total"], results=result["results"])


@router.post("/ingest/trigger")
async def trigger_ingestion():
    """Manually fire all ingestion tasks immediately."""
    from workers.tasks import ingest_gdelt, ingest_newsapi, ingest_rss, ingest_serper

    tasks = {
        "rss": ingest_rss.apply_async(queue="ingestion").id,
        "newsapi": ingest_newsapi.apply_async(queue="ingestion").id,
        "serper": ingest_serper.apply_async(queue="ingestion").id,
        "gdelt": ingest_gdelt.apply_async(queue="ingestion").id,
    }
    return {"triggered": tasks}


# ═══════════════════════════════════════════════════════════════════════════
#  Live pipeline test endpoints — used by the Streamlit dashboard
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/test/text")
async def test_text_pipeline(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze a text string through the full text pipeline.
    Body: {"text": "..."}
    Returns: TextAnalysisResult + geocoded location.
    """
    from processing.geocoder import geocode_locations
    from processing.text_pipeline import analyze_text

    text = body.get("text", "").strip()
    if not text:
        return {"error": "No text provided"}

    try:
        result = await analyze_text(text)
        geo = await geocode_locations(result.locations)
        return {
            "event_type": result.event_type,
            "severity": result.severity,
            "confidence": result.confidence,
            "description": result.description,
            "language": result.language,
            "locations": [loc.model_dump() for loc in result.locations],
            "entities": result.entities.model_dump(),
            "geocoded": geo.model_dump() if geo else None,
        }
    except Exception as e:
        return {"error": str(e)}


@router.post("/test/image")
async def test_image_pipeline(file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Analyze an uploaded image through the vision + OCR pipeline.
    Returns: caption, OCR text, and text analysis.
    """
    from processing.image_pipeline import analyze_image
    from processing.text_pipeline import analyze_text

    try:
        image_bytes = await file.read()
        img_result = await analyze_image(image_bytes)
        text_result = await analyze_text(img_result.combined_text) if img_result.combined_text else None
        return {
            "caption": img_result.caption,
            "ocr_text": img_result.ocr_text,
            "combined_text": img_result.combined_text,
            "analysis": {
                "event_type": text_result.event_type,
                "severity": text_result.severity,
                "confidence": text_result.confidence,
                "description": text_result.description,
            } if text_result else None,
        }
    except Exception as e:
        return {"error": str(e)}


@router.post("/test/audio")
async def test_audio_pipeline(file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Transcribe an uploaded audio file and run text analysis on the transcript.
    Returns: transcript, language, duration, and text analysis.
    """
    from processing.audio_pipeline import process_audio

    try:
        audio_bytes = await file.read()
        transcription, text_result = await process_audio(audio_bytes=audio_bytes)
        return {
            "transcript": transcription.text,
            "language": transcription.language,
            "language_probability": transcription.language_probability,
            "duration_seconds": transcription.duration_seconds,
            "analysis": {
                "event_type": text_result.event_type,
                "severity": text_result.severity,
                "confidence": text_result.confidence,
                "description": text_result.description,
            } if transcription.text else None,
        }
    except Exception as e:
        return {"error": str(e)}
