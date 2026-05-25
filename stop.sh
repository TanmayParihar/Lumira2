#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Lumira — Stop all services and workers
# ═══════════════════════════════════════════════════════════════════════════
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS="$SCRIPT_DIR/logs"

RED='\033[0;31m'; NC='\033[0m'
stop_pid() {
    local name=$1 pid_file=$2
    if [ -f "$pid_file" ]; then
        PID=$(cat "$pid_file")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" && echo -e "${RED}■${NC} $name stopped (pid $PID)"
        fi
        rm -f "$pid_file"
    fi
}

echo ""
echo "Stopping Lumira services..."

stop_pid "FastAPI"     "$LOGS/api.pid"
stop_pid "Celery"      "$LOGS/celery.pid"
stop_pid "MinIO"       "$LOGS/minio.pid"
stop_pid "OpenSearch"  "$LOGS/opensearch.pid"

# Ollama — pkill since it manages its own daemon
pkill -f "ollama serve" 2>/dev/null && echo -e "${RED}■${NC} Ollama stopped" || true

echo ""
echo "All Lumira processes stopped."
echo "(PostgreSQL and Redis are system services — use 'sudo systemctl stop' to stop them.)"
echo ""
