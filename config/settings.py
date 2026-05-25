"""
Lumira — centralised configuration via Pydantic v2 BaseSettings.
All values can be overridden through environment variables or a .env file.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────
    app_name: str = "Lumira Intelligence Pipeline"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"
    media_base_path: str = "./media"

    # ── PostgreSQL ───────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "lumira"
    postgres_password: str = "lumira_secret"
    postgres_db: str = "lumira"

    @computed_field  # type: ignore[misc]
    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def postgres_sync_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis ────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None

    @computed_field  # type: ignore[misc]
    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @computed_field  # type: ignore[misc]
    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @computed_field  # type: ignore[misc]
    @property
    def celery_result_backend(self) -> str:
        return self.redis_url

    # ── OpenSearch ───────────────────────────────────────────────────
    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_index_events: str = "lumira_events"
    opensearch_index_districts: str = "lumira_districts"

    # ── MinIO ────────────────────────────────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin123"
    minio_secure: bool = False
    minio_bucket_media: str = "lumira-media"
    minio_bucket_raw: str = "lumira-raw"

    # ── Nominatim ────────────────────────────────────────────────────
    nominatim_url: str = "http://localhost:8080"
    geocode_fallback_online: bool = True  # fall back to OSM public API

    # ── Ollama ───────────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"
    text_model: str = "qwen3.5:4b"
    vision_model: str = "qwen3-vl:4b"
    ollama_timeout_seconds: int = 120

    # ── Whisper ──────────────────────────────────────────────────────
    whisper_model_size: str = "base"       # tiny | base | small | medium | large-v3
    whisper_device: str = "cpu"            # cpu | cuda
    whisper_compute_type: str = "int8"     # int8 | float16 | float32

    # ── External API Keys ────────────────────────────────────────────
    newsapi_key: str = ""
    serper_api_key: str = ""
    telegram_api_id: Optional[str] = None
    telegram_api_hash: Optional[str] = None

    # ── Ingestion ────────────────────────────────────────────────────
    rss_feeds: List[str] = Field(
        default=[
            "https://feeds.feedburner.com/ndtvnews-india-news",
            "https://timesofindia.indiatimes.com/rss.cms",
            "https://www.thehindu.com/news/national/?service=rss",
            "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
            "https://indianexpress.com/section/india/feed/",
            "https://www.oneindia.com/rss/news-india-3.xml",
            "https://www.ndtv.com/rss/feeds?id=1",
            "https://zeenews.india.com/rss/india-national-news.xml",
        ]
    )
    rss_crawl_interval_minutes: int = 5

    newsapi_query: str = "India conflict protest violence disaster attack"
    newsapi_language: str = "en"
    newsapi_country: str = "in"
    newsapi_interval_minutes: int = 10

    serper_query: str = "India security incident violence protest explosion"
    serper_num_results: int = 20
    serper_interval_minutes: int = 10

    gdelt_interval_minutes: int = 15
    gdelt_country_code: str = "IND"

    radio_streams: List[str] = Field(default=[])   # list of stream URLs
    radio_chunk_duration_seconds: int = 300

    # ── Media processing ─────────────────────────────────────────────
    max_video_duration_seconds: int = 300
    video_frame_sample_interval: int = 30  # sample one frame every N seconds

    # ── Intelligence ─────────────────────────────────────────────────
    dti_window_hours: int = 24
    dti_update_interval_minutes: int = 30
    anomaly_zscore_threshold: float = 2.5
    anomaly_lookback_days: int = 30
    proximity_alert_default_km: float = 5.0
    event_fusion_similarity_threshold: float = 0.85
    event_dedup_window_hours: float = 2.0

    # ── Event classification labels ──────────────────────────────────
    event_types: List[str] = Field(
        default=[
            "VIOLENCE",
            "PROTEST",
            "ACCIDENT",
            "DISASTER",
            "CRIME",
            "POLITICAL",
            "MILITARY",
            "TERRORISM",
            "HEALTH",
            "INFRASTRUCTURE",
            "UNKNOWN",
        ]
    )


settings = Settings()
