#!/usr/bin/env bash
# ============================================================================
# ⚡ zeus-docker.sh — Zeus Framework Docker helper
# ============================================================================
# Usage:
#   bash scripts/zeus-docker.sh <command>
#
# Commands:
#   setup      Copy .env.docker.example → .env (first time setup)
#   build      Build the Zeus Docker image
#   up         Start Redis + PostgreSQL in background
#   run        Start an interactive Zeus agent session
#   shell      Open a bash shell inside the Zeus container
#   logs       Tail logs from all services
#   status     Show container health + service status
#   down       Stop all services
#   reset      Stop + remove all containers AND data volumes (destructive!)
#   upgrade    Pull latest Zeus changes + rebuild image
# ============================================================================

set -euo pipefail

ZEUS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ZEUS_DIR"

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC}  $*"; }
info() { echo -e "${CYAN}→${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }

cmd="${1:-help}"

# ── Helpers ───────────────────────────────────────────────────────────────────

check_env() {
    if [[ ! -f ".env" ]]; then
        fail ".env not found. Run first:\n  bash scripts/zeus-docker.sh setup"
    fi
    source .env 2>/dev/null || true
    if [[ "${OPENROUTER_API_KEY:-YOUR_OPENROUTER_API_KEY_HERE}" == "YOUR_OPENROUTER_API_KEY_HERE" ]]; then
        fail "OPENROUTER_API_KEY not set in .env\n  Edit .env and add your key from https://openrouter.ai/keys"
    fi
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        fail "Docker not found. Install Docker: https://docs.docker.com/get-docker/"
    fi
    if ! docker info &>/dev/null; then
        fail "Docker daemon is not running. Start Docker Desktop or 'sudo systemctl start docker'."
    fi
}

# ── Commands ──────────────────────────────────────────────────────────────────

cmd_setup() {
    echo ""
    echo -e "${BOLD}${CYAN}⚡ Zeus Docker Setup${NC}"
    echo ""
    if [[ -f ".env" ]]; then
        warn ".env already exists — skipping copy"
    else
        cp .env.docker.example .env
        ok ".env created from template"
    fi
    echo ""
    echo -e "  ${BOLD}Next:${NC} Edit .env and set your OpenRouter API key:"
    echo ""
    echo -e "  ${CYAN}nano .env${NC}   # or your preferred editor"
    echo ""
    echo -e "  Get a free key at: ${CYAN}https://openrouter.ai/keys${NC}"
    echo ""
    echo -e "  Then run:"
    echo -e "  ${CYAN}bash scripts/zeus-docker.sh build${NC}"
    echo ""
}

cmd_build() {
    check_docker
    echo ""
    info "Building Zeus Docker image (this takes ~5 min on first build)..."
    echo ""
    docker compose build zeus
    ok "Zeus image built successfully"
    echo ""
    echo -e "  Now start the services:"
    echo -e "  ${CYAN}bash scripts/zeus-docker.sh up${NC}"
    echo ""
}

cmd_up() {
    check_docker
    check_env
    echo ""
    info "Starting Redis + PostgreSQL..."
    docker compose up -d redis postgres
    echo ""
    info "Waiting for services to be healthy..."
    local retries=30
    for i in $(seq 1 $retries); do
        redis_ok=$(docker compose ps --format json redis 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('Health','')=='healthy' else '')" 2>/dev/null || echo "")
        pg_ok=$(docker compose ps --format json postgres 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('Health','')=='healthy' else '')" 2>/dev/null || echo "")
        if [[ "$redis_ok" == "ok" && "$pg_ok" == "ok" ]]; then
            break
        fi
        printf "."
        sleep 2
    done
    echo ""
    echo ""
    docker compose ps
    echo ""
    ok "Services are running"
    echo ""
    echo -e "  Start Zeus:"
    echo -e "  ${CYAN}bash scripts/zeus-docker.sh run${NC}"
    echo ""
}

cmd_run() {
    check_docker
    check_env
    echo ""
    echo -e "${BOLD}${CYAN}⚡ Starting Zeus Agent${NC}"
    echo -e "${NC}"

    # Start services if not already up
    if ! docker compose ps redis 2>/dev/null | grep -q "running\|Up"; then
        info "Starting background services first..."
        docker compose up -d redis postgres
        sleep 3
    fi

    docker compose run --rm zeus
}

cmd_shell() {
    check_docker
    check_env
    info "Opening shell in Zeus container..."
    docker compose run --rm --entrypoint bash zeus
}

cmd_logs() {
    check_docker
    docker compose logs -f --tail=50 "${2:-}"
}

cmd_status() {
    check_docker
    echo ""
    echo -e "${BOLD}Zeus Stack Status${NC}"
    echo ""
    docker compose ps
    echo ""
    # Quick health pings
    if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
        ok "Redis: healthy"
    else
        warn "Redis: not responding"
    fi
    if docker compose exec -T postgres pg_isready -U hermes -d hermes_vectors 2>/dev/null | grep -q "accepting"; then
        ok "PostgreSQL: healthy"
    else
        warn "PostgreSQL: not responding"
    fi
    echo ""
}

cmd_down() {
    check_docker
    info "Stopping Zeus services..."
    docker compose down
    ok "All services stopped (data volumes preserved)"
    echo ""
}

cmd_reset() {
    check_docker
    echo ""
    warn "This will DELETE all Zeus data volumes (memory, sessions, database)."
    read -rp "  Are you sure? [y/N] " confirm
    echo ""
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        docker compose down -v --remove-orphans
        ok "All containers and volumes removed"
    else
        info "Cancelled"
    fi
    echo ""
}

cmd_upgrade() {
    check_docker
    echo ""
    info "Pulling latest Zeus changes..."
    git pull --ff-only 2>/dev/null || git fetch origin && git reset --hard origin/main
    ok "Repo updated"
    echo ""
    info "Rebuilding Zeus image..."
    docker compose build --no-cache zeus
    ok "Image rebuilt"
    echo ""
    echo -e "  Restart with: ${CYAN}bash scripts/zeus-docker.sh run${NC}"
    echo ""
}

cmd_help() {
    echo ""
    echo -e "${BOLD}⚡ Zeus Docker Helper${NC}"
    echo ""
    echo "  Usage: bash scripts/zeus-docker.sh <command>"
    echo ""
    echo "  Commands:"
    echo ""
    echo -e "  ${CYAN}setup${NC}     Copy .env template (first time)"
    echo -e "  ${CYAN}build${NC}     Build Zeus Docker image"
    echo -e "  ${CYAN}up${NC}        Start Redis + PostgreSQL services"
    echo -e "  ${CYAN}run${NC}       Start interactive Zeus agent session"
    echo -e "  ${CYAN}shell${NC}     Open bash shell inside Zeus container"
    echo -e "  ${CYAN}logs${NC}      Tail logs from all services"
    echo -e "  ${CYAN}status${NC}    Show service health"
    echo -e "  ${CYAN}down${NC}      Stop all services (keep data)"
    echo -e "  ${CYAN}reset${NC}     Stop + delete all data volumes (destructive!)"
    echo -e "  ${CYAN}upgrade${NC}   Pull latest Zeus + rebuild image"
    echo ""
    echo "  Quick start:"
    echo ""
    echo -e "  ${GREEN}bash scripts/zeus-docker.sh setup${NC}"
    echo -e "  ${GREEN}nano .env   # add OPENROUTER_API_KEY${NC}"
    echo -e "  ${GREEN}bash scripts/zeus-docker.sh build${NC}"
    echo -e "  ${GREEN}bash scripts/zeus-docker.sh run${NC}"
    echo ""
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "$cmd" in
    setup)   cmd_setup ;;
    build)   cmd_build ;;
    up)      cmd_up ;;
    run)     cmd_run ;;
    shell)   cmd_shell ;;
    logs)    cmd_logs "$@" ;;
    status)  cmd_status ;;
    down)    cmd_down ;;
    reset)   cmd_reset ;;
    upgrade) cmd_upgrade ;;
    help|--help|-h) cmd_help ;;
    *)
        fail "Unknown command: $cmd\nRun 'bash scripts/zeus-docker.sh help' for usage"
        ;;
esac
