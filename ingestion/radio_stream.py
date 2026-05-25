"""
Internet radio stream ingester.
Captures N-second audio chunks from HTTP/ICECAST streams,
saves them to MinIO, and queues them for Whisper transcription.
"""
from __future__ import annotations

import asyncio
import io
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import httpx
import structlog

from config.settings import settings
from ingestion.base import BaseIngester, RawItem
from storage.minio_client import upload_bytes

logger = structlog.get_logger(__name__)


async def capture_stream_chunk(
    stream_url: str,
    duration_seconds: int = 300,
) -> Optional[bytes]:
    """
    Read `duration_seconds` worth of audio bytes from an HTTP audio stream.
    Returns raw audio bytes (MP3 / AAC / OGG — whatever the stream uses).
    """
    collected: list[bytes] = []
    deadline = time.monotonic() + duration_seconds

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=duration_seconds + 10)) as client:
            async with client.stream(
                "GET",
                stream_url,
                headers={"User-Agent": "LumiraBot/1.0", "Icy-MetaData": "1"},
                follow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    collected.append(chunk)
                    if time.monotonic() >= deadline:
                        break
    except Exception as e:
        logger.error("radio.capture_failed", url=stream_url, error=str(e))
        return None

    return b"".join(collected) if collected else None


class RadioIngester(BaseIngester):
    source_name = "Radio"

    def __init__(
        self,
        stream_urls: Optional[List[str]] = None,
        chunk_duration: int | None = None,
    ):
        self.stream_urls = stream_urls or settings.radio_streams
        self.chunk_duration = chunk_duration or settings.radio_chunk_duration_seconds

    async def fetch(self) -> List[RawItem]:
        if not self.stream_urls:
            logger.debug("radio.no_streams_configured")
            return []

        items: List[RawItem] = []

        async def _capture_one(url: str) -> None:
            audio_bytes = await capture_stream_chunk(url, self.chunk_duration)
            if not audio_bytes:
                return

            # Upload to MinIO
            object_name = f"radio/{uuid.uuid4()}.mp3"
            try:
                minio_path = upload_bytes(
                    audio_bytes,
                    content_type="audio/mpeg",
                    bucket=settings.minio_bucket_media,
                    object_name=object_name,
                )
            except Exception as e:
                logger.error("radio.minio_upload_failed", url=url, error=str(e))
                return

            items.append(
                RawItem(
                    source="Radio",
                    media_type="audio",
                    title=f"Radio capture — {url}",
                    source_url=url,
                    media_path=minio_path,
                    published_at=datetime.now(tz=timezone.utc),
                    metadata={
                        "stream_url": url,
                        "duration_seconds": self.chunk_duration,
                        "bytes": len(audio_bytes),
                    },
                )
            )
            logger.info("radio.captured", url=url, bytes=len(audio_bytes))

        await asyncio.gather(*[_capture_one(u) for u in self.stream_urls])
        return items
