# ═══════════════════════════════════════════════════════════════════════════
#  Lumira Intelligence Pipeline — Makefile
# ═══════════════════════════════════════════════════════════════════════════
SHELL := /bin/bash

.PHONY: help setup pull-models init init-db-only load-districts seed-assets \
        start stop restart api worker flower \
        ingest-rss ingest-all update-dti \
        status logs logs-api logs-worker \
        shell test lint clean clean-all

# Direct venv binary paths — no `source` needed
PYTHON  := .venv/bin/python
PIP     := .venv/bin/pip
CELERY  := .venv/bin/celery
UVICORN := .venv/bin/uvicorn
PYTEST  := .venv/bin/pytest
RUFF    := .venv/bin/ruff

# Project root must be on PYTHONPATH so Celery workers and uvicorn can find
# all project packages without needing `pip install -e .`
export PYTHONPATH := $(shell pwd)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ── Environment ───────────────────────────────────────────────────────────
setup: ## Install all system dependencies + Python packages
	@bash setup.sh

.env:
	@cp .env.example .env
	@echo "Created .env — edit it to add your API keys"

pull-models: ## Pull Qwen3.5-4B and Qwen3-VL-4B via Ollama
	@echo "Pulling text model (qwen3.5:4b)..."
	ollama pull qwen3.5:4b
	@echo "Pulling vision model (qwen3-vl:4b)..."
	ollama pull qwen3-vl:4b
	@echo "Models ready."

# ── Database ──────────────────────────────────────────────────────────────
init: .env ## Create DB tables, load India districts, seed assets
	$(PYTHON) scripts/init_db.py --seed-assets

init-db-only: ## Create tables only (no seed data)
	$(PYTHON) scripts/init_db.py

load-districts: ## (Re)load India district reference data
	$(PYTHON) scripts/load_india_districts.py

seed-assets: ## Seed sample monitored assets
	$(PYTHON) scripts/seed_assets.py

# ── Services ──────────────────────────────────────────────────────────────
start: .env ## Start all services (API + Celery + OpenSearch + MinIO + Ollama)
	@bash start.sh

stop: ## Stop all Lumira-managed services
	@bash stop.sh

restart: stop start ## Restart all services

# ── Individual process launchers (for development) ────────────────────────
api: .env ## Start FastAPI dev server only (auto-reload)
	$(UVICORN) api.main:app --host 0.0.0.0 --port 8000 --reload

worker: .env ## Start Celery worker + beat only
	$(CELERY) -A workers.celery_app worker \
		-B --loglevel=info --concurrency=4 \
		-Q celery,ingestion,processing,intelligence

flower: .env ## Start Celery Flower monitoring UI (http://localhost:5555)
	$(CELERY) -A workers.celery_app flower --port=5555

# ── Manual ingestion triggers ─────────────────────────────────────────────
ingest-rss: ## Trigger RSS ingestion now
	$(CELERY) -A workers.celery_app call workers.tasks.ingest_rss

ingest-all: ## Trigger all ingestion tasks now
	$(CELERY) -A workers.celery_app call workers.tasks.ingest_rss
	$(CELERY) -A workers.celery_app call workers.tasks.ingest_newsapi
	$(CELERY) -A workers.celery_app call workers.tasks.ingest_serper
	$(CELERY) -A workers.celery_app call workers.tasks.ingest_gdelt

update-dti: ## Force DTI recalculation now
	$(CELERY) -A workers.celery_app call workers.tasks.intelligence_update_dti

# ── Monitoring ────────────────────────────────────────────────────────────
status: ## Show status of all services
	@echo "=== Services ==="
	@pg_isready -U lumira -d lumira 2>&1 | head -1 && echo "PostgreSQL: ✓" || echo "PostgreSQL: ✗"
	@redis-cli ping 2>/dev/null | grep -q PONG && echo "Redis: ✓"     || echo "Redis: ✗"
	@curl -sf http://localhost:9200 > /dev/null && echo "OpenSearch: ✓" || echo "OpenSearch: ✗"
	@curl -sf http://localhost:9000/minio/health/live > /dev/null && echo "MinIO: ✓" || echo "MinIO: ✗"
	@curl -sf http://localhost:11434 > /dev/null && echo "Ollama: ✓"    || echo "Ollama: ✗"
	@curl -sf http://localhost:8000/health > /dev/null && echo "API: ✓"  || echo "API: ✗"

logs: ## Tail all logs
	@tail -f logs/*.log

logs-api: ## Tail API log
	@tail -f logs/api.log

logs-worker: ## Tail Celery worker log
	@tail -f logs/celery.log

# ── Dev utilities ─────────────────────────────────────────────────────────
shell: ## Launch Python REPL with project context
	$(PYTHON) -i -c "import asyncio; from config.settings import settings; print('Lumira shell ready')"

test: ## Run tests
	$(PYTEST) tests/ -v --tb=short

lint: ## Lint with ruff
	$(RUFF) check . --fix

clean: ## Remove logs, pycache, temp files
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf logs/*.log media/tmp_video/ .pytest_cache/
	@echo "Cleaned."

clean-all: clean ## Also remove venv and downloaded services
	@rm -rf .venv services/
	@echo "Full clean done."
