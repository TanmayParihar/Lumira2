"""
Image feed ingester.
Polls configurable image URLs (e.g. CCTV snapshots, satellite feeds,
social media image endpoints) and queues them for vision processing.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
import structlog

from config.settings import settings
from ingestion.base import BaseIngester, RawItem
from storage.minio_client import upload_bytes

logger = structlog.get_logger(__name__)

_seen: set[str] = set()


class ImageFeedIngester(BaseIngester):
    """
    Ingest images from a list of URLs.

    Each entry in `feeds` is a dict:
        {
          "url": "https://...",
          "name": "Camera A",
          "location": "New Delhi"
        }
    """

    source_name = "Image"

    def __init__(self, feeds: Optional[List[Dict]] = None):
        # feeds can also be plain URL strings
        raw = feeds or []
        self.feeds: List[Dict] = []
        for item in raw:
            if isinstance(item, str):
                self.feeds.append({"url": item, "name": item, "location": ""})
            else:
                self.feeds.append(item)

    async def fetch(self) -> List[RawItem]:
        if not self.feeds:
            logger.debug("image_feed.no_feeds")
            return []

        items: List[RawItem] = []

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            for feed in self.feeds:
                url = feed.get("url", "")
                if not url:
                    continue
                try:
                    resp = await client.get(url, headers={"User-Agent": "LumiraBot/1.0"})
                    resp.raise_for_status()

                    content = resp.content
                    content_hash = hashlib.sha1(content).hexdigest()
                    if content_hash in _seen:
                        logger.debug("image_feed.duplicate", url=url)
                        continue
                    _seen.add(content_hash)

                    # Determine extension from content-type
                    ct = resp.headers.get("content-type", "image/jpeg")
                    ext_map = {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/gif": ".gif",
                        "image/webp": ".webp",
                    }
                    ext = ext_map.get(ct.split(";")[0].strip(), ".jpg")
                    object_name = f"images/{uuid.uuid4()}{ext}"

                    minio_path = upload_bytes(
                        content,
                        content_type=ct,
                        bucket=settings.minio_bucket_media,
                        object_name=object_name,
                    )

                    items.append(
                        RawItem(
                            source="Image",
                            media_type="image",
                            title=f"Image feed — {feed.get('name', url)}",
                            source_url=url,
                            media_path=minio_path,
                            published_at=datetime.now(tz=timezone.utc),
                            metadata={
                                "feed_name": feed.get("name"),
                                "location_hint": feed.get("location", ""),
                                "content_type": ct,
                                "bytes": len(content),
                            },
                        )
                    )
                    logger.info("image_feed.captured", url=url, bytes=len(content))

                except Exception as e:
                    logger.error("image_feed.error", url=url, error=str(e))

        return items
