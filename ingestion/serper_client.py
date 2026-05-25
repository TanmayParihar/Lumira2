"""
Serper API ingester — Google Search results via Serper.dev.
Returns organic results + news results for India security queries.
Docs: https://serper.dev/
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Optional, Set

import httpx
import structlog

from config.settings import settings
from ingestion.base import BaseIngester, RawItem

logger = structlog.get_logger(__name__)

SERPER_BASE = "https://google.serper.dev"

_seen: Set[str] = set()


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


SEARCH_QUERIES = [
    "India violence attack today",
    "India protest riot latest news",
    "India terrorist attack bombing",
    "India natural disaster flood earthquake",
    "India border incident military",
    "India crime kidnapping shooting",
    "India industrial accident fire explosion",
]


class SerperIngester(BaseIngester):
    source_name = "Serper"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.serper_api_key
        if not self.api_key:
            logger.warning("serper.no_key", msg="Serper key not set — ingester disabled")

    async def _search(self, query: str, search_type: str = "search") -> dict:
        """Call Serper search or news endpoint."""
        endpoint = f"{SERPER_BASE}/{search_type}"
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "q": query,
            "gl": "in",        # country = India
            "hl": "en",
            "num": settings.serper_num_results,
            "tbs": "qdr:d",    # past 24 hours
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def fetch(self) -> List[RawItem]:
        if not self.api_key:
            return []

        items: List[RawItem] = []

        for query in SEARCH_QUERIES:
            try:
                # Organic search
                data = await self._search(query, "search")
                items.extend(self._parse_organic(data, query))

                # News search
                data = await self._search(query, "news")
                items.extend(self._parse_news(data, query))

            except Exception as e:
                logger.error("serper.query_failed", query=query, error=str(e))

        return items

    def _parse_organic(self, data: dict, query: str) -> List[RawItem]:
        results = []
        for r in data.get("organic", []):
            url = r.get("link", "")
            h = _url_hash(url)
            if h in _seen:
                continue
            _seen.add(h)

            content = "\n\n".join(filter(None, [r.get("title", ""), r.get("snippet", "")]))
            results.append(
                RawItem(
                    source="Serper",
                    media_type="text",
                    title=r.get("title", ""),
                    raw_content=content,
                    source_url=url,
                    published_at=datetime.now(tz=timezone.utc),
                    metadata={
                        "query": query,
                        "type": "organic",
                        "position": r.get("position"),
                    },
                )
            )
        return results

    def _parse_news(self, data: dict, query: str) -> List[RawItem]:
        results = []
        for r in data.get("news", []):
            url = r.get("link", "")
            h = _url_hash(url)
            if h in _seen:
                continue
            _seen.add(h)

            pub_dt = datetime.now(tz=timezone.utc)
            if r.get("date"):
                try:
                    # Serper returns relative dates like "2 hours ago"
                    # store as now() for simplicity; article text will have real date
                    pass
                except Exception:
                    pass

            content = "\n\n".join(filter(None, [r.get("title", ""), r.get("snippet", "")]))
            results.append(
                RawItem(
                    source="Serper",
                    media_type="text",
                    title=r.get("title", ""),
                    raw_content=content,
                    source_url=url,
                    published_at=pub_dt,
                    metadata={
                        "query": query,
                        "type": "news",
                        "source": r.get("source"),
                        "date_raw": r.get("date"),
                    },
                )
            )
        return results
