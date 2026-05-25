#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Lumira Intelligence Pipeline — Native Setup Script
#  Supports pyenv-managed Python (preferred) and system Python.
#  Tested on Ubuntu 22.04 / 24.04 with pyenv.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR ]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Lumira Intelligence Pipeline — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Resolve Python 3.12 binary ─────────────────────────────────────────
info "Resolving Python 3.12..."

PYTHON_BIN=""

# Priority 1: pyenv has 3.12 installed → use it directly
if command -v pyenv &>/dev/null; then
    PYENV_312="$(pyenv root)/versions/3.12.7/bin/python3.12"
    # Also try any 3.12.x version
    PYENV_312_ANY="$(pyenv root)/versions/$(pyenv versions --bare | grep '^3\.12' | tail -1)/bin/python3.12"
    for candidate in "$PYENV_312" "$PYENV_312_ANY"; do
        if [ -x "$candidate" ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    done

    if [ -n "$PYTHON_BIN" ]; then
        # Pin this project to 3.12 so pyenv shims resolve correctly
        pyenv local 3.12.7 2>/dev/null || true
        success "Using pyenv Python: $PYTHON_BIN ($("$PYTHON_BIN" --version))"
    fi
fi

# Priority 2: python3.12 binary somewhere on PATH
if [ -z "$PYTHON_BIN" ] && command -v python3.12 &>/dev/null; then
    PYTHON_BIN="$(command -v python3.12)"
    success "Using system python3.12: $PYTHON_BIN"
fi

# Priority 3: system python3 — verify it's actually 3.12
if [ -z "$PYTHON_BIN" ]; then
    SYS_PY="$(command -v python3)"
    SYS_VER="$("$SYS_PY" -c 'import sys; print(sys.version_info[:2])')"
    if [ "$SYS_VER" = "(3, 12)" ]; then
        PYTHON_BIN="$SYS_PY"
        success "Using system python3 (3.12): $PYTHON_BIN"
    fi
fi

# Bail if nothing found
if [ -z "$PYTHON_BIN" ]; then
    error "Python 3.12 not found.\n  pyenv install: pyenv install 3.12.7 && pyenv local 3.12.7\n  Or system: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv"
fi

# ── 2. System packages (non-Python only) ──────────────────────────────────
info "Installing system packages (ffmpeg, PostgreSQL, Redis, Java, build tools)..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq-dev libpq5 \
    curl wget git \
    postgresql postgresql-contrib \
    redis-server \
    openjdk-17-jre-headless \
    build-essential libssl-dev libffi-dev \
    libsndfile1 libsndfile1-dev \
    > /dev/null

# Install PostGIS for whatever PostgreSQL major version is active
PG_VER=$(pg_lsclusters -h 2>/dev/null | awk '{print $1}' | sort -rn | head -1 \
         || psql --version 2>/dev/null | grep -oP '\d+' | head -1 \
         || echo "17")
info "Detected PostgreSQL $PG_VER — installing postgresql-${PG_VER}-postgis-3..."
sudo apt-get install -y --no-install-recommends \
    "postgresql-${PG_VER}-postgis-3" \
    "postgresql-${PG_VER}-postgis-3-scripts" \
    > /dev/null 2>&1 || \
sudo apt-get install -y --no-install-recommends postgis > /dev/null 2>&1 || \
warn "PostGIS package not found for PG $PG_VER — you may need to install it manually"

success "System packages installed"

# ── 3. Python virtual environment ─────────────────────────────────────────
info "Creating Python virtual environment (.venv) with $("$PYTHON_BIN" --version)..."
"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel setuptools -q
success "Virtual environment ready"

# ── 4. Python dependencies ────────────────────────────────────────────────
info "Installing Python dependencies (this may take a few minutes)..."
pip install -r requirements.txt -q
success "Python dependencies installed"

# ── 5. PostgreSQL setup ───────────────────────────────────────────────────
info "Configuring PostgreSQL..."
sudo systemctl enable postgresql --quiet
sudo systemctl start postgresql

sudo -u postgres psql -tc "SELECT 1 FROM pg_user WHERE usename='lumira'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER lumira WITH PASSWORD 'lumira_secret';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='lumira'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE lumira OWNER lumira;"
sudo -u postgres psql -d lumira -c "CREATE EXTENSION IF NOT EXISTS postgis;" > /dev/null
sudo -u postgres psql -d lumira -c "CREATE EXTENSION IF NOT EXISTS postgis_topology;" > /dev/null 2>&1 || true
success "PostgreSQL + PostGIS configured  (user: lumira / lumira_secret)"

# ── 6. Redis setup ────────────────────────────────────────────────────────
info "Configuring Redis..."
sudo systemctl enable redis-server --quiet
sudo systemctl start redis-server
success "Redis started"

# ── 7. OpenSearch ─────────────────────────────────────────────────────────
info "Setting up OpenSearch 2.12.0..."
OS_VERSION="2.12.0"
OS_DIR="$SCRIPT_DIR/services/opensearch"
OS_BINARY="$OS_DIR/opensearch-$OS_VERSION"

mkdir -p "$OS_DIR"

if [ ! -d "$OS_BINARY" ]; then
    info "Downloading OpenSearch $OS_VERSION (~600 MB)..."
    wget -q "https://artifacts.opensearch.org/releases/bundle/opensearch/$OS_VERSION/opensearch-$OS_VERSION-linux-x64.tar.gz" \
         -O "$OS_DIR/opensearch.tar.gz"
    tar -xzf "$OS_DIR/opensearch.tar.gz" -C "$OS_DIR"
    rm "$OS_DIR/opensearch.tar.gz"
fi

cat > "$OS_BINARY/config/opensearch.yml" << 'OSYML'
plugins.security.disabled: true
discovery.type: single-node
network.host: 127.0.0.1
http.port: 9200
OSYML

success "OpenSearch ready at $OS_BINARY"

# ── 8. MinIO ──────────────────────────────────────────────────────────────
info "Setting up MinIO..."
MINIO_DIR="$SCRIPT_DIR/services/minio"
mkdir -p "$MINIO_DIR/bin" "$MINIO_DIR/data"

if [ ! -f "$MINIO_DIR/bin/minio" ]; then
    info "Downloading MinIO..."
    wget -q "https://dl.min.io/server/minio/release/linux-amd64/minio" \
         -O "$MINIO_DIR/bin/minio"
    chmod +x "$MINIO_DIR/bin/minio"
fi
success "MinIO binary ready"

# ── 9. Ollama ─────────────────────────────────────────────────────────────
info "Installing Ollama..."
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
    success "Ollama installed"
else
    success "Ollama already installed ($(ollama --version 2>/dev/null || echo 'version unknown'))"
fi

# ── 10. Environment file ──────────────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    warn ".env created from .env.example — fill in NEWSAPI_KEY and SERPER_API_KEY"
else
    info ".env already exists — skipping"
fi

# ── 11. Directories ───────────────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/media/tmp_video" "$SCRIPT_DIR/logs"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Setup complete!${NC}"
echo ""
echo "  Next steps:"
echo "  1. Edit .env and add your NEWSAPI_KEY and SERPER_API_KEY"
echo "  2. Run:  make pull-models    (downloads Qwen3.5-4B + Qwen3-VL-4B)"
echo "  3. Run:  make init           (creates DB tables + seeds data)"
echo "  4. Run:  make start          (starts all services + workers)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
