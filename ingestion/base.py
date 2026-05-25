"""
Base ingester interface. All ingesters return a list of RawItem dicts
that are stored in the raw_ingestion table and queued for processing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class RawItem:
    """Canonical raw item passed between ingestion and processing."""

    source: str                           # RSS | NewsAPI | Serper | GDELT | Radio | Image | Video
    media_type: str                       # text | audio | image | video
    raw_content: str = ""                 # text payload (or empty for binary)
    title: str = ""
    source_url: str = ""
    media_path: str = ""                  # MinIO path for binary media
    metadata: Dict[str, Any] = field(default_factory=dict)
    published_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "media_type": self.media_type,
            "raw_content": self.raw_content,
            "title": self.title,
            "source_url": self.source_url,
            "media_path": self.media_path,
            "metadata": self.metadata,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }


class BaseIngester(ABC):
    """Abstract base ingester."""

    source_name: str = "unknown"

    @abstractmethod
    async def fetch(self) -> List[RawItem]:
        """Fetch new items from the source."""
        ...

    async def run(self) -> List[RawItem]:
        """fetch() with error swallowing for scheduled use."""
        import structlog
        logger = structlog.get_logger(self.__class__.__name__)
        try:
            items = await self.fetch()
            logger.info("ingester.fetched", source=self.source_name, count=len(items))
            return items
        except Exception as exc:
            logger.error("ingester.error", source=self.source_name, error=str(exc))
            return []
