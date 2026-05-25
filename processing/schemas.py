"""
Pydantic schemas for processing pipeline outputs.
These are internal data contracts between processing stages.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class LocationEntity(BaseModel):
    name: str
    entity_type: str = "unknown"   # city | district | state | landmark | region


class ExtractedEntities(BaseModel):
    people: List[str] = Field(default_factory=list)
    organizations: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)


class TextAnalysisResult(BaseModel):
    """Output from the Qwen2.5-7B text pipeline."""
    event_type: str = "UNKNOWN"
    severity: int = Field(default=1, ge=1, le=5)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    description: str = ""
    locations: List[LocationEntity] = Field(default_factory=list)
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    language: str = "en"
    raw_llm_output: str = ""


class GeocodedLocation(BaseModel):
    """Output from the Nominatim geocoder."""
    input_name: str
    resolved_name: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = "India"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    confidence: float = 0.0


class TranscriptionResult(BaseModel):
    """Output from faster-whisper audio pipeline."""
    text: str
    language: str = "en"
    language_probability: float = 0.0
    duration_seconds: float = 0.0
    segments: List[Dict[str, Any]] = Field(default_factory=list)


class ImageAnalysisResult(BaseModel):
    """Output from Qwen2-VL + PaddleOCR image pipeline."""
    caption: str = ""
    ocr_text: str = ""
    combined_text: str = ""
    objects_detected: List[str] = Field(default_factory=list)
    raw_vision_output: str = ""


class ProcessedItem(BaseModel):
    """Fully processed item ready for the intelligence layer."""
    raw_id: Optional[str] = None
    source: str
    source_url: str = ""
    title: str = ""
    media_type: str = "text"
    media_path: str = ""

    # From text analysis
    event_type: str = "UNKNOWN"
    severity: int = 1
    confidence: float = 0.5
    description: str = ""
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    keywords: List[str] = Field(default_factory=list)
    language: str = "en"

    # Geocoding
    location_raw: List[LocationEntity] = Field(default_factory=list)
    location_name: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Timestamps
    event_time: Optional[datetime] = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(from_attributes=True)
