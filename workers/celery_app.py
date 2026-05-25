"""
Celery application instance + beat schedule.

Queues:
  ingestion   — data collection tasks (fast, I/O bound)
  processing  — AI processing tasks (slow, CPU/GPU bound)
  intelligence — scoring and alerting tasks

Beat schedule (periodic tasks):
  Every 5 min  : RSS + Serper + NewsAPI ingestion
  Every 15 min : GDELT ingestion
  Every 30 min : DTI score update
  Every 60 min : anomaly detection
  Continuous   : radio stream capture (if configured)
"""
from __future__ import annotations

import os
import sys

# Ensure the project root is on sys.path so task lazy imports resolve correctly
# when celery is launched via `.venv/bin/celery` (which doesn't add CWD automatically)
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from celery import Celery
from celery.schedules import crontab

from config.settings import settings

app = Celery("lumira")

app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Route tasks to appropriate queues
    task_routes={
        "workers.tasks.ingest_*": {"queue": "ingestion"},
        "workers.tasks.process_*": {"queue": "processing"},
        "workers.tasks.intelligence_*": {"queue": "intelligence"},
    },
    # Beat schedule
    beat_schedule={
        # ── Ingestion ──────────────────────────────────────────────
        "ingest-rss-every-5min": {
            "task": "workers.tasks.ingest_rss",
            "schedule": 300,  # every 5 minutes
            "options": {"queue": "ingestion"},
        },
        "ingest-newsapi-every-10min": {
            "task": "workers.tasks.ingest_newsapi",
            "schedule": 600,
            "options": {"queue": "ingestion"},
        },
        "ingest-serper-every-10min": {
            "task": "workers.tasks.ingest_serper",
            "schedule": 600,
            "options": {"queue": "ingestion"},
        },
        "ingest-gdelt-every-15min": {
            "task": "workers.tasks.ingest_gdelt",
            "schedule": 900,
            "options": {"queue": "ingestion"},
        },
        "ingest-radio-every-5min": {
            "task": "workers.tasks.ingest_radio",
            "schedule": 300,
            "options": {"queue": "ingestion"},
        },
        # ── Intelligence ───────────────────────────────────────────
        "update-dti-every-30min": {
            "task": "workers.tasks.intelligence_update_dti",
            "schedule": 1800,
            "options": {"queue": "intelligence"},
        },
        "anomaly-detection-every-hour": {
            "task": "workers.tasks.intelligence_anomaly_scan",
            "schedule": 3600,
            "options": {"queue": "intelligence"},
        },
    },
)

# Auto-discover tasks
app.autodiscover_tasks(["workers"])
