"""
Video feed ingester using yt-dlp.
Downloads short video clips (social media, news clips, YouTube shorts)
and queues them for frame sampling + audio transcription.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import structlog

from config.settings import settings
from ingestion.base import BaseIngester, RawItem
from storage.minio_client import upload_file

logger = structlog.get_logger(__name__)


def _run_ytdlp(url: str, output_path: str, max_duration: int) -> bool:
    """
    Download video using yt-dlp with duration guard.
    Returns True on success.
    """
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--max-filesize", "200M",
        "--match-filter", f"duration<{max_duration}",
        "--format", "best[ext=mp4]/best",
        "--output", output_path,
        "--quiet",
        "--no-warnings",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.warning("ytdlp.failed", url=url, stderr=result.stderr[:500])
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("ytdlp.timeout", url=url)
        return False
    except FileNotFoundError:
        logger.error("ytdlp.not_installed", msg="yt-dlp not found — install with: pip install yt-dlp")
        return False


class VideoFeedIngester(BaseIngester):
    """
    Ingest video clips from a list of URLs.
    Each entry: {"url": "...", "name": "...", "location_hint": "..."}
    """

    source_name = "Video"

    def __init__(
        self,
        video_urls: Optional[List] = None,
        max_duration: Optional[int] = None,
    ):
        raw = video_urls or []
        self.video_urls: list[dict] = []
        for item in raw:
            if isinstance(item, str):
                self.video_urls.append({"url": item, "name": item, "location_hint": ""})
            else:
                self.video_urls.append(item)
        self.max_duration = max_duration or settings.max_video_duration_seconds

    async def fetch(self) -> List[RawItem]:
        if not self.video_urls:
            logger.debug("video_feed.no_urls")
            return []

        items: List[RawItem] = []
        tmp_dir = Path(settings.media_base_path) / "tmp_video"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        for entry in self.video_urls:
            url = entry.get("url", "")
            if not url:
                continue

            tmp_file = tmp_dir / f"{uuid.uuid4()}.mp4"
            try:
                success = _run_ytdlp(url, str(tmp_file), self.max_duration)
                if not success or not tmp_file.exists():
                    continue

                # Upload to MinIO
                minio_path = upload_file(tmp_file, bucket=settings.minio_bucket_media)

                items.append(
                    RawItem(
                        source="Video",
                        media_type="video",
                        title=f"Video — {entry.get('name', url)}",
                        source_url=url,
                        media_path=minio_path,
                        published_at=datetime.now(tz=timezone.utc),
                        metadata={
                            "name": entry.get("name"),
                            "location_hint": entry.get("location_hint", ""),
                            "local_path": str(tmp_file),
                        },
                    )
                )
                logger.info("video_feed.downloaded", url=url, path=minio_path)
            except Exception as e:
                logger.error("video_feed.error", url=url, error=str(e))
            finally:
                if tmp_file.exists():
                    tmp_file.unlink(missing_ok=True)

        return items
