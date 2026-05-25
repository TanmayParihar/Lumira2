"""API request/response schemas (Pydantic v2)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Events ────────────────────────────────────────────────────────────────

class EventOut(BaseModel):
    id: UUID
    source: str
    source_url: Optional[str] = None
    title: Optional[str] = None
    media_type: str
    event_type: Optional[str] = None
    severity: Optional[int] = None
    confidence: Optional[float] = None
    description: Optional[str] = None
    keywords: Optional[List[str]] = None
    language: Optional[str] = None
    location_name: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    event_time: Optional[datetime] = None
    ingested_at: Optional[datetime] = None
    is_duplicate: bool = False
    fusion_group_id: Optional[UUID] = None

    class Config:
        from_attributes = True


class EventList(BaseModel):
    total: int
    items: List[EventOut]


class EventFilter(BaseModel):
    district: Optional[str] = None
    state: Optional[str] = None
    event_type: Optional[str] = None
    min_severity: Optional[int] = Field(None, ge=1, le=5)
    source: Optional[str] = None
    from_dt: Optional[datetime] = None
    to_dt: Optional[datetime] = None
    limit: int = Field(default=50, le=200)
    offset: int = Field(default=0, ge=0)


# ── DTI ───────────────────────────────────────────────────────────────────

class DTIOut(BaseModel):
    district_name: str
    state: str
    dti_score: float
    frequency_score: float
    severity_score: float
    velocity_score: float
    anomaly_bonus: float
    event_count_24h: int
    avg_severity: float
    is_anomaly: bool
    velocity: float
    computed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DTIMapItem(BaseModel):
    district: str
    state: str
    dti_score: float
    is_anomaly: bool
    event_count: int


# ── Alerts ────────────────────────────────────────────────────────────────

class AlertOut(BaseModel):
    id: UUID
    event_id: UUID
    asset_id: UUID
    asset_name: Optional[str] = None
    asset_type: Optional[str] = None
    distance_km: Optional[float] = None
    severity: Optional[int] = None
    acknowledged: bool
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AlertAcknowledge(BaseModel):
    alert_ids: List[UUID]


# ── Assets ────────────────────────────────────────────────────────────────

class AssetCreate(BaseModel):
    name: str
    asset_type: Optional[str] = None
    description: Optional[str] = None
    location_name: Optional[str] = None
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    alert_radius_km: float = Field(default=5.0, gt=0, le=500)
    extra_meta: Optional[Dict[str, Any]] = None


class AssetOut(BaseModel):
    id: UUID
    name: str
    asset_type: Optional[str] = None
    description: Optional[str] = None
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    alert_radius_km: float
    active: bool
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── Pipeline status ───────────────────────────────────────────────────────

class PipelineStatus(BaseModel):
    postgres: bool
    redis: bool
    opensearch: bool
    minio: bool
    ollama_text_model: bool
    queue_depth: int
    total_events: int
    events_last_24h: int
    active_alerts: int


# ── Search ────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    district: Optional[str] = None
    state: Optional[str] = None
    event_type: Optional[str] = None
    min_severity: Optional[int] = None
    limit: int = Field(default=20, le=100)
    offset: int = 0


class SearchResponse(BaseModel):
    total: int
    results: List[Dict[str, Any]]
