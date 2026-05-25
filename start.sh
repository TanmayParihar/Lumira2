#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Lumira — Start all services and workers
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOGS="$SCRIPT_DIR/logs"
mkdir -p "$LOGS"

# Load pyenv shims if present (needed so celery/uvicorn resolve correctly)
export PYENV_VERSION=3.12.7
if command -v pyenv &>/dev/null; then
    eval "$(pyenv init -)" 2>/dev/null || true
fi
source .venv/bin/activate 2>/dev/null || { echo "Run ./setup.sh first"; exit 1; }

# Ensure all project packages are importable by workers and API processes
# (required when launching via .venv/bin/* rather than `python -m`)
export PYTHONPATH="$SCRIPT_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}▶${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Lumira Intelligence Pipeline — Starting"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── PostgreSQL ────────────────────────────────────────────────────────────
if ! pg_isready -U lumira -d lumira -q 2>/dev/null; then
    ok "Starting PostgreSQL..."
    sudo systemctl start postgresql
fi
ok "PostgreSQL running"

# ── Redis ─────────────────────────────────────────────────────────────────
if ! redis-cli ping &>/dev/null; then
    ok "Starting Redis..."
    sudo systemctl start redis-server
fi
ok "Redis running"

# ── OpenSearch ────────────────────────────────────────────────────────────
OS_VERSION="2.12.0"
OS_BIN="$SCRIPT_DIR/services/opensearch/opensearch-$OS_VERSION/bin/opensearch"
OS_PID="$LOGS/opensearch.pid"

if [ -f "$OS_BIN" ]; then
    if ! curl -sf http://localhost:9200 &>/dev/null; then
        ok "Starting OpenSearch..."
        export OPENSEARCH_JAVA_OPTS="-Xms512m -Xmx1g"
        nohup "$OS_BIN" > "$LOGS/opensearch.log" 2>&1 &
        echo $! > "$OS_PID"
        # Wait for OpenSearch to be ready
        echo -n "  Waiting for OpenSearch"
        for i in $(seq 1 30); do
            sleep 2
            if curl -sf http://localhost:9200 &>/dev/null; then
                echo " ✓"
                break
            fi
            echo -n "."
        done
    fi
    ok "OpenSearch running on :9200"
else
    warn "OpenSearch binary not found — run ./setup.sh first"
fi

# ── MinIO ─────────────────────────────────────────────────────────────────
MINIO_BIN="$SCRIPT_DIR/services/minio/bin/minio"
MINIO_DATA="$SCRIPT_DIR/services/minio/data"
MINIO_PID="$LOGS/minio.pid"

if [ -f "$MINIO_BIN" ]; then
    if ! curl -sf http://localhost:9000/minio/health/live &>/dev/null; then
        ok "Starting MinIO..."
        export MINIO_ROOT_USER="${MINIO_ACCESS_KEY:-minioadmin}"
        export MINIO_ROOT_PASSWORD="${MINIO_SECRET_KEY:-minioadmin123}"
        nohup "$MINIO_BIN" server "$MINIO_DATA" --console-address ":9001" \
            > "$LOGS/minio.log" 2>&1 &
        echo $! > "$MINIO_PID"
        sleep 2
    fi
    ok "MinIO running on :9000 (console :9001)"
else
    warn "MinIO binary not found — run ./setup.sh first"
fi

# ── Ollama ────────────────────────────────────────────────────────────────
if command -v ollama &>/dev/null; then
    if ! curl -sf http://localhost:11434 &>/dev/null; then
        ok "Starting Ollama..."
        nohup ollama serve > "$LOGS/ollama.log" 2>&1 &
        sleep 3
    fi
    ok "Ollama running on :11434"
else
    warn "Ollama not found — run ./setup.sh first"
fi

# ── Celery worker + beat ──────────────────────────────────────────────────
CELERY_PID="$LOGS/celery.pid"
if [ -f "$CELERY_PID" ] && kill -0 "$(cat "$CELERY_PID")" 2>/dev/null; then
    ok "Celery worker already running (pid $(cat "$CELERY_PID"))"
else
    ok "Starting Celery worker + beat scheduler..."
    nohup "$SCRIPT_DIR/.venv/bin/celery" -A workers.celery_app worker \
        -B \
        --loglevel=info \
        --concurrency=4 \
        -Q celery,ingestion,processing,intelligence \
        > "$LOGS/celery.log" 2>&1 &
    echo $! > "$CELERY_PID"
fi

# ── FastAPI ───────────────────────────────────────────────────────────────
API_PID="$LOGS/api.pid"
if [ -f "$API_PID" ] && kill -0 "$(cat "$API_PID")" 2>/dev/null; then
    ok "API already running (pid $(cat "$API_PID"))"
else
    ok "Starting FastAPI..."
    nohup "$SCRIPT_DIR/.venv/bin/uvicorn" api.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --workers 2 \
        > "$LOGS/api.log" 2>&1 &
    echo $! > "$API_PID"
fi

sleep 2

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}All services started!${NC}"
echo ""
echo "  API           →  http://localhost:8000"
echo "  Swagger UI    →  http://localhost:8000/docs"
echo "  OpenSearch    →  http://localhost:9200"
echo "  MinIO console →  http://localhost:9001"
echo "  Ollama        →  http://localhost:11434"
echo ""
echo "  Logs:  ./logs/"
echo "  Stop:  ./stop.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
