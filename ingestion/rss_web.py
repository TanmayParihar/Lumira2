"""
RSS + lightweight web-page text ingester.
Crawls a configurable list of RSS feeds and de-duplicates by URL.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Set

import feedparser
import httpx
import structlog
from bs4 import BeautifulSoup

from config.settings import settings
from ingestion.base import BaseIngester, RawItem

logger = structlog.get_logger(__name__)

# In-memory bloom of seen URLs (per process lifetime)
_seen: Set[str] = set()


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def _parse_date(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    try:
        if hasattr(entry, "published"):
            return parsedate_to_datetime(entry.published).astimezone(timezone.utc)
        if hasattr(entry, "updated"):
            return parsedate_to_datetime(entry.updated).astimezone(timezone.utc)
    except Exception:
        pass
    return datetime.now(tz=timezone.utc)


def _extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


async def _fetch_article_text(url: str, timeout: int = 10) -> str:
    """Best-effort article text extraction."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url, headers={"User-Agent": "LumiraBot/1.0"})
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                return _extract_text_from_html(resp.text)[:4000]  # cap at 4k chars
    except Exception as e:
        logger.debug("rss.article_fetch_failed", url=url, error=str(e))
    return ""


class RSSIngester(BaseIngester):
    source_name = "RSS"

    def __init__(self, feeds: Optional[List[str]] = None, fetch_articles: bool = False):
        self.feeds = feeds or settings.rss_feeds
        self.fetch_articles = fetch_articles

    async def fetch(self) -> List[RawItem]:
        items: List[RawItem] = []

        for feed_url in self.feeds:
            try:
                feed = feedparser.parse(feed_url)
                if feed.bozo and not feed.entries:
                    logger.warning("rss.parse_error", url=feed_url, exc=str(feed.bozo_exception))
                    continue

                for entry in feed.entries[:20]:  # max 20 per feed
                    link = getattr(entry, "link", "") or ""
                    h = _url_hash(link)
                    if h in _seen:
                        continue
                    _seen.add(h)

                    # Build raw content from feed summary + optionally article body
                    summary = getattr(entry, "summary", "") or ""
                    summary = _extract_text_from_html(summary)

                    article_text = ""
                    if self.fetch_articles and link:
                        article_text = await _fetch_article_text(link)

                    raw_content = f"{getattr(entry, 'title', '')}\n\n{summary}"
                    if article_text:
                        raw_content = f"{raw_content}\n\n{article_text}"

                    items.append(
                        RawItem(
                            source="RSS",
                            media_type="text",
                            title=getattr(entry, "title", ""),
                            raw_content=raw_content.strip(),
                            source_url=link,
                            published_at=_parse_date(entry),
                            metadata={"feed_url": feed_url, "feed_title": feed.feed.get("title", "")},
                        )
                    )
            except Exception as e:
                logger.error("rss.feed_error", url=feed_url, error=str(e))

        return items
