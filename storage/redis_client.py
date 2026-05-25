"""
Redis client: pub/sub for alerts, caching, and event queuing.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import redis.asyncio as aioredis
import structlog

from config.settings import settings

logger = structlog.get_logger(__name__)

# Shared async pool — initialised lazily
_pool: Optional[aioredis.Redis] = None

# Channel names
CHANNEL_ALERTS = "lumira:alerts"
CHANNEL_DTI_UPDATE = "lumira:dti:update"
CHANNEL_ANOMALY = "lumira:anomaly"


async def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _pool


async def cache_set(key: str, value: Any, ttl_seconds: int = 300) -> None:
    r = await get_redis()
    await r.setex(key, ttl_seconds, json.dumps(value, default=str))


async def cache_get(key: str) -> Optional[Any]:
    r = await get_redis()
    raw = await r.get(key)
    return json.loads(raw) if raw else None


async def cache_delete(key: str) -> None:
    r = await get_redis()
    await r.delete(key)


async def publish(channel: str, payload: dict) -> None:
    r = await get_redis()
    await r.publish(channel, json.dumps(payload, default=str))
    logger.debug("redis.published", channel=channel)


async def enqueue_raw(item: dict) -> None:
    """Push a raw ingestion item onto the processing queue."""
    r = await get_redis()
    await r.rpush("lumira:raw_queue", json.dumps(item, default=str))


async def dequeue_raw(timeout: int = 5) -> Optional[dict]:
    """Blocking pop from the processing queue."""
    r = await get_redis()
    result = await r.blpop("lumira:raw_queue", timeout=timeout)
    if result:
        _, data = result
        return json.loads(data)
    return None


async def get_queue_length() -> int:
    r = await get_redis()
    return await r.llen("lumira:raw_queue")


async def store_dti_snapshot(district: str, score: float) -> None:
    """Keep the latest DTI score for each district in a Redis hash."""
    r = await get_redis()
    await r.hset("lumira:dti:latest", district, str(round(score, 2)))


async def get_all_dti_scores() -> dict[str, float]:
    r = await get_redis()
    raw = await r.hgetall("lumira:dti:latest")
    return {k: float(v) for k, v in raw.items()}
