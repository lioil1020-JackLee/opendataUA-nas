#!/bin/bash
# Update script for source changes. Use --build after Dockerfile or dependency changes.
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERR]${NC} $1"; exit 1; }

SERVICE_NAME="opendata-ua"
REBUILD=0
WAIT_SECONDS="${WAIT_SECONDS:-8}"

for arg in "$@"; do
    case "$arg" in
        --build|--rebuild)
            REBUILD=1
            ;;
        --help|-h)
            echo "Usage: ./update.sh [--build]"
            echo ""
            echo "Default: recreate the container. Source files are mounted by docker-compose.yml."
            echo "--build : rebuild image first; use after Dockerfile or dependency changes."
            exit 0
            ;;
        *)
            error "Unknown option: $arg"
            ;;
    esac
done

echo ""
echo "========================================="
echo "  OpenData Weather UA update"
echo "========================================="
echo ""

if ! command -v docker >/dev/null 2>&1; then
    error "docker command not found. Run this on the NAS with Container Manager installed."
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif docker-compose --version >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    error "docker compose not found."
fi

[ -f "docker-compose.yml" ] || error "docker-compose.yml not found. Run this script from the project root."
[ -f "Dockerfile" ] || error "Dockerfile not found."
[ -d "data" ] || mkdir -p data
[ -f "data/config.json" ] || error "data/config.json not found. This project uses data/config.json as the only runtime config file."
info "Using config: data/config.json"

if [ "$REBUILD" = "1" ]; then
    warn "Rebuilding Docker image."
    $COMPOSE_CMD build "$SERVICE_NAME"
fi

info "Recreating service..."
$COMPOSE_CMD up -d --force-recreate "$SERVICE_NAME"

info "Waiting ${WAIT_SECONDS}s for startup..."
sleep "$WAIT_SECONDS"

WEB_PORT="8188"
if [ -f ".env" ]; then
    ENV_WEB_PORT=$(grep -E '^WEB_PORT=' .env | tail -n 1 | cut -d '=' -f 2- || true)
    if [ -n "$ENV_WEB_PORT" ]; then
        WEB_PORT="$ENV_WEB_PORT"
    fi
fi

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
echo "  Normal update:  ./update.sh"
echo "  Rebuild image:  ./update.sh --build"
echo "  View logs:      $COMPOSE_CMD logs -f $SERVICE_NAME"
echo ""
