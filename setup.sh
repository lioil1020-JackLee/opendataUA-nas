#!/bin/bash
# First-time install script for Synology NAS Container Manager.
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERR]${NC} $1"; exit 1; }

SERVICE_NAME="opendata-ua"
WEB_PORT="${WEB_PORT:-8188}"
OPCUA_PORT="${OPCUA_PORT:-48484}"
WAIT_SECONDS="${WAIT_SECONDS:-15}"

echo ""
echo "========================================="
echo "  OpenData Weather UA NAS setup"
echo "========================================="
echo ""

if ! command -v docker >/dev/null 2>&1; then
    error "docker command not found. Install Synology Container Manager and run this over SSH on the NAS."
fi
info "Docker is available: $(docker --version)"

if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif docker-compose --version >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    error "docker compose not found. Install or enable Container Manager."
fi
info "Compose command: $COMPOSE_CMD"

[ -f "docker-compose.yml" ] || error "docker-compose.yml not found. Run this script from the project root."
[ -f "Dockerfile" ] || error "Dockerfile not found."
[ -f "main.py" ] || error "main.py not found."
[ -d "server" ] || error "server directory not found."
[ -d "webui" ] || error "webui directory not found."

mkdir -p data
[ -f "data/config.json" ] || error "data/config.json not found. This project uses data/config.json as the only runtime config file."
info "Using config: data/config.json"

if [ ! -f ".env" ]; then
    cat > .env <<EOF
WEB_PORT=$WEB_PORT
OPCUA_PORT=$OPCUA_PORT
TZ=Asia/Taipei
EOF
    info "Created .env"
else
    info "Using existing .env"
fi

if [ -f ".env" ]; then
    ENV_WEB_PORT=$(grep -E '^WEB_PORT=' .env | tail -n 1 | cut -d '=' -f 2- || true)
    ENV_OPCUA_PORT=$(grep -E '^OPCUA_PORT=' .env | tail -n 1 | cut -d '=' -f 2- || true)
    if [ -n "$ENV_WEB_PORT" ]; then
        WEB_PORT="$ENV_WEB_PORT"
    fi
    if [ -n "$ENV_OPCUA_PORT" ]; then
        OPCUA_PORT="$ENV_OPCUA_PORT"
    fi
fi

info "Building Docker image..."
$COMPOSE_CMD down
$COMPOSE_CMD build --no-cache "$SERVICE_NAME"

info "Starting service..."
$COMPOSE_CMD up -d --force-recreate "$SERVICE_NAME"

info "Waiting ${WAIT_SECONDS}s for startup..."
sleep "$WAIT_SECONDS"

HTTP_CODE="000"
if command -v curl >/dev/null 2>&1; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${WEB_PORT}/api/health" 2>/dev/null || echo "000")
elif command -v wget >/dev/null 2>&1; then
    if wget -q -O /dev/null "http://127.0.0.1:${WEB_PORT}/api/health" 2>/dev/null; then
        HTTP_CODE="200"
    fi
else
    warn "curl/wget not found; skipping HTTP health check."
fi

if [ "$HTTP_CODE" = "200" ]; then
    info "Service is healthy: http://127.0.0.1:${WEB_PORT}"
else
    warn "Health check returned $HTTP_CODE. Check logs with: $COMPOSE_CMD logs -f $SERVICE_NAME"
fi

echo ""
echo "Common commands:"
echo "  View logs:     $COMPOSE_CMD logs -f $SERVICE_NAME"
echo "  Stop service:  $COMPOSE_CMD down"
echo "  Update app:    ./update.sh"
echo ""
echo "Web UI:  http://<NAS-IP>:${WEB_PORT}"
echo "OPC UA:  opc.tcp://<NAS-IP>:${OPCUA_PORT}"
echo ""
