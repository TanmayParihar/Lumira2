"""
NewsAPI ingester — fetches top headlines and everything endpoint.
Docs: https://newsapi.org/docs
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

NEWSAPI_BASE = "https://newsapi.org/v2"

_seen: Set[str] = set()


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


class NewsAPIIngester(BaseIngester):
    source_name = "NewsAPI"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.newsapi_key
        if not self.api_key:
            logger.warning("newsapi.no_key", msg="NewsAPI key not set — ingester disabled")

    async def _get(self, endpoint: str, params: dict) -> dict:
        params["apiKey"] = self.api_key
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{NEWSAPI_BASE}/{endpoint}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def fetch(self) -> List[RawItem]:
        if not self.api_key:
            return []

        items: List[RawItem] = []

        # 1. Top headlines — use sources-based query because country=in returns
        #    0 results on the free plan (India not supported for top-headlines).
        india_sources_queries = [
            "India violence OR attack OR explosion",
            "India protest OR riot OR clash",
            "India flood OR earthquake OR disaster",
            "India terrorism OR militant OR Naxal",
        ]
        for q in india_sources_queries:
            try:
                data = await self._get(
                    "top-headlines",
                    {
                        "q": q,
                        "language": settings.newsapi_language,
                        "pageSize": 20,
                    },
                )
                items.extend(self._parse_articles(data.get("articles", []), "top-headlines"))
            except Exception as e:
                logger.error("newsapi.headlines_failed", query=q, error=str(e))

        # 2. Everything search — omit the 'from' date filter.
        #    The free/developer tier delays articles by ~24 h, so filtering
        #    for the last 2 h consistently returns 0 results.
        everything_queries = [
            "India security incident attack shooting",
            "India protest demonstration crackdown",
            "India natural disaster emergency relief",
            "India border military Pakistan China",
            "India crime kidnapping arrest",
        ]
        for q in everything_queries:
            try:
                data = await self._get(
                    "everything",
                    {
                        "q": q,
                        "language": settings.newsapi_language,
                        "sortBy": "publishedAt",
                        "pageSize": 20,
                    },
                )
                items.extend(self._parse_articles(data.get("articles", []), "everything"))
            except Exception as e:
                logger.error("newsapi.everything_failed", query=q, error=str(e))

        logger.info("newsapi.fetch_complete", total=len(items))
        return items

    def _parse_articles(self, articles: list, endpoint: str) -> List[RawItem]:
        result = []
        for article in articles:
            url = article.get("url", "")
            h = _url_hash(url)
            if h in _seen:
                continue
            _seen.add(h)

            # Skip removed articles
            if article.get("title") == "[Removed]":
                continue

            pub_str = article.get("publishedAt", "")
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            except Exception:
                pub_dt = datetime.now(tz=timezone.utc)

            content = "\n\n".join(
                filter(
                    None,
                    [
                        article.get("title", ""),
                        article.get("description", ""),
                        article.get("content", ""),
                    ],
                )
            )

            result.append(
                RawItem(
                    source="NewsAPI",
                    media_type="text",
                    title=article.get("title", ""),
                    raw_content=content.strip(),
                    source_url=url,
                    published_at=pub_dt,
                    metadata={
                        "endpoint": endpoint,
                        "author": article.get("author"),
                        "source_name": article.get("source", {}).get("name"),
                    },
                )
            )
        return result
