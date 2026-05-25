"""
SQLAlchemy ORM models for Lumira.
PostGIS geometry columns used for spatial queries.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ────────────────────────────────────────────────────────────────────────────
# Raw ingestion queue
# ────────────────────────────────────────────────────────────────────────────
class RawIngestion(Base):
    """Stores every raw item exactly as ingested, before any processing."""

    __tablename__ = "raw_ingestion"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(50), nullable=False)   # RSS | NewsAPI | Serper | GDELT | Radio | Image | Video
    source_url = Column(Text)
    title = Column(Text)
    raw_content = Column(Text)
    media_type = Column(String(20), nullable=False)  # text | audio | image | video
    media_path = Column(Text)                         # MinIO object path (if binary)
    extra_meta = Column(JSONB, default={})
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())
    processed = Column(Boolean, default=False)
    processing_error = Column(Text)

    __table_args__ = (
        Index("idx_raw_processed", "processed"),
        Index("idx_raw_source", "source"),
        Index("idx_raw_ingested_at", "ingested_at"),
    )


# ────────────────────────────────────────────────────────────────────────────
# Processed events
# ────────────────────────────────────────────────────────────────────────────
class Event(Base):
    """A classified, geocoded event derived from a raw ingestion record."""

    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Source linkage
    raw_id = Column(UUID(as_uuid=True), ForeignKey("raw_ingestion.id"), nullable=True)
    source = Column(String(50), nullable=False)
    source_url = Column(Text)
    title = Column(Text)
    media_type = Column(String(20))
    media_path = Column(Text)

    # Classification
    event_type = Column(String(50))           # VIOLENCE | PROTEST | ACCIDENT | …
    severity = Column(Integer)                # 1–5
    confidence = Column(Float)                # 0.0–1.0
    description = Column(Text)
    entities = Column(JSONB, default={})      # {people: [], orgs: [], keywords: []}
    keywords = Column(ARRAY(String))
    language = Column(String(10))

    # Geocoding
    location_raw = Column(JSONB, default=[])  # raw NER output
    location_name = Column(String(200))       # primary resolved name
    district = Column(String(100))
    state = Column(String(100))
    country = Column(String(100), default="India")
    # Flat float columns for quick in-process use (mirrors PostGIS point)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    coordinates = Column(Geometry("POINT", srid=4326))  # PostGIS point

    # Timestamps
    event_time = Column(DateTime(timezone=True))           # when the event happened
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Fusion / dedup
    is_duplicate = Column(Boolean, default=False)
    duplicate_of = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=True)
    fusion_group_id = Column(UUID(as_uuid=True), nullable=True)

    # Alerts
    alerts = relationship("ProximityAlert", back_populates="event")

    __table_args__ = (
        Index("idx_event_time", "event_time"),
        Index("idx_event_type", "event_type"),
        Index("idx_event_district", "district"),
        Index("idx_event_state", "state"),
        Index("idx_event_ingested", "ingested_at"),
        Index("idx_event_severity", "severity"),
        Index("idx_event_coords", "coordinates", postgresql_using="gist"),
        Index("idx_fusion_group", "fusion_group_id"),
    )


# ────────────────────────────────────────────────────────────────────────────
# India administrative boundaries
# ────────────────────────────────────────────────────────────────────────────
class District(Base):
    """India district reference table with PostGIS boundary polygon."""

    __tablename__ = "districts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), nullable=False)
    state = Column(String(150), nullable=False)
    state_code = Column(String(10))
    lgd_code = Column(String(20))              # Local Government Directory code
    centroid = Column(Geometry("POINT", srid=4326))
    boundary = Column(Geometry("MULTIPOLYGON", srid=4326))

    # DTI scores
    dti_scores = relationship("DistrictThreatIndex", back_populates="district_ref")

    __table_args__ = (
        UniqueConstraint("name", "state", name="uq_district_name_state"),
        Index("idx_district_name", "name"),
        Index("idx_district_state", "state"),
        Index("idx_district_centroid", "centroid", postgresql_using="gist"),
        Index("idx_district_boundary", "boundary", postgresql_using="gist"),
    )


# ────────────────────────────────────────────────────────────────────────────
# District Threat Index
# ────────────────────────────────────────────────────────────────────────────
class DistrictThreatIndex(Base):
    """Computed threat score for a district at a given point in time."""

    __tablename__ = "district_threat_index"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    district_name = Column(String(150), nullable=False)
    state = Column(String(150), nullable=False)
    district_id = Column(UUID(as_uuid=True), ForeignKey("districts.id"), nullable=True)

    # Score components (all 0–100)
    dti_score = Column(Float, default=0.0)       # composite DTI
    frequency_score = Column(Float, default=0.0) # event count component
    severity_score = Column(Float, default=0.0)  # severity-weighted
    velocity_score = Column(Float, default=0.0)  # rate-of-change
    anomaly_bonus = Column(Float, default=0.0)   # anomaly bump

    event_count_24h = Column(Integer, default=0)
    avg_severity = Column(Float, default=0.0)
    is_anomaly = Column(Boolean, default=False)
    velocity = Column(Float, default=0.0)        # normalised z-score

    computed_at = Column(DateTime(timezone=True), server_default=func.now())

    district_ref = relationship("District", back_populates="dti_scores")

    __table_args__ = (
        Index("idx_dti_district", "district_name"),
        Index("idx_dti_computed_at", "computed_at"),
        Index("idx_dti_score", "dti_score"),
    )


# ────────────────────────────────────────────────────────────────────────────
# Assets (things to protect / monitor proximity for)
# ────────────────────────────────────────────────────────────────────────────
class Asset(Base):
    """Monitored asset whose proximity to events triggers alerts."""

    __tablename__ = "assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    asset_type = Column(String(100))   # embassy | base | facility | VIP | infra
    description = Column(Text)
    location_name = Column(String(200))
    coordinates = Column(Geometry("POINT", srid=4326), nullable=False)
    alert_radius_km = Column(Float, default=5.0)
    active = Column(Boolean, default=True)
    extra_meta = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    alerts = relationship("ProximityAlert", back_populates="asset")

    __table_args__ = (
        Index("idx_asset_active", "active"),
        Index("idx_asset_coords", "coordinates", postgresql_using="gist"),
    )


# ────────────────────────────────────────────────────────────────────────────
# Proximity Alerts
# ────────────────────────────────────────────────────────────────────────────
class ProximityAlert(Base):
    """Raised when an event occurs within an asset's alert radius."""

    __tablename__ = "proximity_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    distance_km = Column(Float)
    severity = Column(Integer)
    acknowledged = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    event = relationship("Event", back_populates="alerts")
    asset = relationship("Asset", back_populates="alerts")

    __table_args__ = (
        Index("idx_alert_event", "event_id"),
        Index("idx_alert_asset", "asset_id"),
        Index("idx_alert_ack", "acknowledged"),
        Index("idx_alert_created", "created_at"),
    )


# ────────────────────────────────────────────────────────────────────────────
# Anomaly records
# ────────────────────────────────────────────────────────────────────────────
class AnomalyRecord(Base):
    """Stores each detected statistical anomaly in district event rates."""

    __tablename__ = "anomaly_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    district_name = Column(String(150), nullable=False)
    state = Column(String(150))
    zscore = Column(Float)
    current_count = Column(Integer)
    baseline_mean = Column(Float)
    baseline_std = Column(Float)
    lookback_days = Column(Integer)
    detected_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_anomaly_district", "district_name"),
        Index("idx_anomaly_detected", "detected_at"),
    )
