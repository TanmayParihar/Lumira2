# Lumira Intelligence Pipeline

End-to-end OSINT intelligence pipeline for India — ingests multi-modal data,
classifies and geocodes events with local LLMs, scores districts by threat level,
and raises proximity alerts for monitored assets.

```
┌─────────────────────────────────────────────────────────────┐
│                    INGESTION LAYER                           │
│  RSS/Web │ NewsAPI │ Serper │ GDELT │ Radio │ Image │ Video  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│              PROCESSING LAYER  (all local)                   │
│  Text  → Qwen3.5-4B  → Location NER + Event Classification  │
│  Audio → Whisper     → Transcript → Text Pipeline            │
│  Image → Qwen3-VL-4B + PaddleOCR → Caption + OCR            │
│  Video → ffmpeg frames + audio strip → both pipelines        │
│                   Geocode via Nominatim                       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│              STORAGE LAYER  (self-hosted)                    │
│   PostgreSQL+PostGIS │ MinIO │ OpenSearch │ Redis            │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│              INTELLIGENCE LAYER                              │
│  Event Fusion │ District Threat Index │ Velocity Scoring     │
│  Anomaly Detection │ Asset Proximity Alerts                   │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│              REST API  (FastAPI + Swagger UI)                 │
│  /events  /threats/dti  /alerts  /assets  /pipeline/search   │
└─────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Min spec |
|---|---|
| OS | Ubuntu 22.04 / Debian 12 |
| RAM | 16 GB (32 GB recommended for GPU) |
| Disk | 60 GB free (models + data) |
| GPU | Optional — CUDA 12+ for faster inference |
| Python | 3.12+ |

---

## Quick Start

### 1. Install everything
```bash
chmod +x setup.sh start.sh stop.sh
./setup.sh
```

This installs: PostgreSQL+PostGIS, Redis, OpenSearch, MinIO, Ollama, ffmpeg, Python venv.

### 2. Add your API keys
```bash
nano .env
# Set NEWSAPI_KEY and SERPER_API_KEY
```

### 3. Pull AI models (one-time, ~8 GB)
```bash
make pull-models
```
Downloads `qwen3.5:4b` (text NER/classification) and `qwen3-vl:4b` (vision captioning) via Ollama.

### 4. Initialise database
```bash
make init
```
Creates all PostgreSQL tables, loads 150+ India districts, seeds 15 sample monitored assets.

### 5. Start the pipeline
```bash
make start
```

| Service | URL |
|---|---|
| **API + Swagger** | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 |
| OpenSearch | http://localhost:9200 |
| Celery Flower | `make flower` → http://localhost:5555 |

---

## Architecture Details

### Ingestion Layer

| Source | Interval | Notes |
|---|---|---|
| **RSS** (8 feeds) | 5 min | NDTV, ToI, Hindu, HT, IE, Zee News, OI |
| **NewsAPI** | 10 min | Top headlines + keyword search for India |
| **Serper** | 10 min | 7 Google search queries, India security terms |
| **GDELT** | 15 min | 15-min event files filtered to India (`IN`) |
| **Radio** | 5 min | HTTP/Icecast stream capture (configure URLs in .env) |
| **Image** | on-demand | Configurable feed URLs → MinIO → vision pipeline |
| **Video** | on-demand | yt-dlp download → frame sample + audio strip |

### Processing Layer

**Text pipeline** (`Qwen3.5-4B` via Ollama)
- Structured JSON prompt extracts: `event_type`, `severity` (1–5), `locations[]`, `entities`, `confidence`
- Event types: `VIOLENCE | PROTEST | ACCIDENT | DISASTER | CRIME | POLITICAL | MILITARY | TERRORISM | HEALTH | INFRASTRUCTURE | UNKNOWN`

**Audio pipeline** (`faster-whisper`)
- Transcribes audio → feeds into text pipeline
- Model size configurable: `tiny → large-v3`

**Image pipeline** (`Qwen3-VL-4B` + `PaddleOCR`)
- Vision caption via Ollama multimodal API
- OCR extracts visible text (signs, banners, licence plates)
- Combined output → text pipeline

**Video pipeline** (`ffmpeg` + both above)
- Extracts one frame every 30 seconds (configurable)
- Strips audio track
- Runs image pipeline on each frame + audio pipeline on track
- Returns highest-severity result

**Geocoder** (local Nominatim + OSM fallback)
- Resolves NER location names to `(lat, lon, district, state)`
- Caches results in-memory
- Stores PostGIS POINT for spatial queries

### Intelligence Layer

**Event Fusion**
- Deduplicates events with Jaccard similarity ≥ 0.85 + distance < 5 km + same event_type within 2h
- Groups related (non-duplicate) events into `fusion_group_id`

**District Threat Index (DTI)**
- Runs every 30 min across all active districts
- Formula:
  ```
  DTI = frequency_score (0-30)
      + severity_score  (0-40)   ← recency-weighted, half-life = 12h
      + velocity_score  (0-20)   ← z-score vs 7-day hourly baseline
      + anomaly_bonus   (0-10)
  ```
- Latest scores cached in Redis hash `lumira:dti:latest`

**Velocity Scorer**
- Computes z-score of current-hour event rate vs 7-day rolling baseline
- Normalised to [0, 1]

**Anomaly Detector**
- Daily event counts per district
- 30-day rolling mean/std
- z-score > 2.5 → anomaly flagged, `AnomalyRecord` written

**Proximity Alerts**
- Every new geocoded event checked against all active assets
- If event falls within `asset.alert_radius_km`, a `ProximityAlert` is created
- Alert published to Redis channel `lumira:alerts` for real-time push

---

## API Reference

Full interactive docs at **http://localhost:8000/docs**

```
GET  /events                  List events (filter by district/state/type/severity)
GET  /events/{id}             Single event
GET  /events/{id}/related     Fusion group members

GET  /threats/dti             Latest DTI scores (all districts)
GET  /threats/dti/map         Fast Redis snapshot for map rendering
GET  /threats/dti/{district}  Single district DTI
GET  /threats/anomalies       Recent anomaly records
POST /threats/dti/trigger     Force DTI recalculation

GET  /alerts                  Unacknowledged proximity alerts
POST /alerts/acknowledge      Bulk acknowledge alerts
GET  /alerts/{id}             Single alert

GET  /assets                  List monitored assets
POST /assets                  Create asset
PATCH /assets/{id}            Update asset
DELETE /assets/{id}           Deactivate asset

GET  /pipeline/status         Health check + live metrics
POST /pipeline/search         Full-text OpenSearch query
POST /pipeline/ingest/trigger Fire all ingestion tasks immediately
```

---

## Configuration Reference (`.env`)

```bash
# API Keys (required for NewsAPI / Serper ingestion)
NEWSAPI_KEY=your_key
SERPER_API_KEY=your_key

# Model selection
TEXT_MODEL=qwen3.5:4b          # default; qwen3.5:1.7b for lower RAM
VISION_MODEL=qwen3-vl:4b       # default; llava:7b as fallback
WHISPER_MODEL_SIZE=base        # tiny|base|small|medium|large-v3
WHISPER_DEVICE=cpu             # cpu|cuda

# Intelligence tuning
DTI_WINDOW_HOURS=24
ANOMALY_ZSCORE_THRESHOLD=2.5
PROXIMITY_ALERT_DEFAULT_KM=5.0

# Radio streams (optional)
# RADIO_STREAMS=["https://stream.url/live.mp3"]
```

---

## Useful Commands

```bash
make status          # Check all services
make logs            # Tail all logs
make logs-worker     # Watch processing pipeline
make ingest-all      # Trigger all ingestion immediately
make update-dti      # Force DTI recalculation
make flower          # Open Celery monitoring UI
make shell           # Python REPL with project loaded
make test            # Run tests
```

---

## Hardware Guide

| Mode | RAM | GPU | Models |
|---|---|---|---|
| **Minimal** | 8 GB | None | `qwen3.5:1.7b`, Whisper `tiny` |
| **Standard** | 16 GB | None | `qwen3.5:4b`, Whisper `base` |
| **Full** | 32 GB | 8 GB VRAM | `qwen3.5:4b` + `qwen3-vl:4b`, Whisper `large-v3` |

For GPU: install NVIDIA drivers + CUDA 12, then `make start` picks them up automatically via Ollama.

---

## Nominatim (local geocoding)

The pipeline uses the public OSM Nominatim API by default (no setup needed).
For production / air-gapped use, run a local Nominatim with India data:

```bash
# One-time India data import (~2h, ~20 GB disk)
wget https://download.geofabrik.de/asia/india-latest.osm.pbf
nominatim import --osm-file india-latest.osm.pbf --threads 4

# Then set in .env:
NOMINATIM_URL=http://localhost:8080
GEOCODE_FALLBACK_ONLINE=false
```
