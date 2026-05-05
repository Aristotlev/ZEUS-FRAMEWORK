#!/usr/bin/env bash
# ============================================================================
# ⚡ Zeus Framework — Ubuntu One-Shot Installer
# ============================================================================
# Sets up the full stack from scratch:
#   1. System dependencies (Python 3.11, Redis, PostgreSQL 16 + pgvector)
#   2. Hermes Agent core (via uv)
#   3. OpenRouter API key configuration
#   4. pgvector database + tables
#   5. Mnemosyne L3 memory plugin
#   6. Zeus soul persona
#   7. Skills sync
#
# Usage (fresh Ubuntu machine):
#   git clone https://github.com/Aristotlev/ZEUS-FRAMEWORK.git zeus
#   cd zeus
#   chmod +x install.sh
#   ./install.sh
#
# Env overrides (all optional):
#   OPENROUTER_API_KEY   — skip interactive prompt
#   ZEUS_MODEL           — default model (e.g. anthropic/claude-sonnet-4)
#   ZEUS_SKIP_POSTGRES   — set to 1 to skip pgvector setup
#   ZEUS_SKIP_REDIS      — set to 1 to skip Redis install check
#   ZEUS_HERMES_HOME     — override ~/.hermes location
# ============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC}  $*"; }
info() { echo -e "${CYAN}→${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }
hdr()  { echo ""; echo -e "${BOLD}${CYAN}$*${NC}"; echo ""; }

# ── Paths ─────────────────────────────────────────────────────────────────────
ZEUS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${ZEUS_HERMES_HOME:-$HOME/.hermes}"
HERMES_CORE="$ZEUS_DIR/core"
PGDATA="$HOME/pgdata"
PG_PORT=5433

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo -e "${BOLD}${CYAN}"
cat <<'EOF'
  ______
 |___  /
    / / ___ _   _ ___
   / / / _ \ | | / __|
  / /_|  __/ |_| \__ \
 /_____\___|\__,_|___/

  ⚡ Zeus Framework Installer
EOF
echo -e "${NC}"
echo -e "${DIM}  Full stack: Hermes Agent + OpenRouter + pgvector + Mnemosyne${NC}"
echo ""

# ── OS check ─────────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
    warn "This script targets Ubuntu/Debian Linux."
    warn "You are on $(uname -s). Continuing, but some apt steps will be skipped."
    IS_UBUNTU=false
else
    # Check for apt
    if command -v apt-get &>/dev/null; then
        IS_UBUNTU=true
    else
        IS_UBUNTU=false
        warn "apt not found — skipping system package installs. Install deps manually."
    fi
fi

# ── Helper: require sudo or skip ──────────────────────────────────────────────
HAS_SUDO=false
if command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
    HAS_SUDO=true
elif command -v sudo &>/dev/null; then
    info "This script needs sudo for system package installation."
    sudo -v && HAS_SUDO=true || warn "Continuing without sudo — system packages may be missing."
fi

apt_install() {
    # Only run on Ubuntu/Debian with apt available
    [[ "$IS_UBUNTU" == false ]] && return 0
    if [[ "$HAS_SUDO" == true ]]; then
        sudo apt-get install -y "$@" 2>/dev/null || warn "apt install $* failed (may already be installed)"
    else
        warn "Cannot install $* without sudo — please install manually."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
hdr "Step 1/7 — System dependencies"
# ─────────────────────────────────────────────────────────────────────────────

info "Updating package lists..."
if [[ "$IS_UBUNTU" == true && "$HAS_SUDO" == true ]]; then
    sudo apt-get update -qq
fi

# Essential build tools
apt_install git curl wget build-essential ca-certificates gnupg lsb-release

# Python 3.11
info "Checking Python 3.11..."
if command -v python3.11 &>/dev/null; then
    ok "Python 3.11 found ($(python3.11 --version))"
else
    info "Installing Python 3.11..."
    if [[ "$IS_UBUNTU" == true && "$HAS_SUDO" == true ]]; then
        sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
        sudo apt-get update -qq
    fi
    apt_install python3.11 python3.11-venv python3.11-dev python3-pip
    ok "Python 3.11 installed"
fi

# ripgrep (optional but recommended)
if ! command -v rg &>/dev/null; then
    info "Installing ripgrep (faster file search)..."
    apt_install ripgrep || true
fi

# Redis
if [[ "${ZEUS_SKIP_REDIS:-0}" != "1" ]]; then
    info "Checking Redis..."
    if command -v redis-cli &>/dev/null && redis-cli ping &>/dev/null 2>&1; then
        ok "Redis is running"
    else
        info "Installing Redis..."
        apt_install redis-server
        if [[ "$HAS_SUDO" == true ]]; then
            sudo systemctl enable redis-server 2>/dev/null || true
            sudo systemctl start redis-server 2>/dev/null || true
        fi
        ok "Redis installed and started"
    fi
fi

# PostgreSQL 16 + pgvector
if [[ "${ZEUS_SKIP_POSTGRES:-0}" != "1" ]]; then
    info "Checking PostgreSQL 16..."
    if ! command -v psql &>/dev/null; then
        info "Installing PostgreSQL 16..."
        if [[ "$IS_UBUNTU" == true && "$HAS_SUDO" == true ]]; then
            # Add official PostgreSQL apt repo for guaranteed pg16
            curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
                | sudo gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg 2>/dev/null
            echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] \
https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
                | sudo tee /etc/apt/sources.list.d/pgdg.list > /dev/null
            sudo apt-get update -qq
            apt_install postgresql-16 postgresql-16-pgvector libpq-dev
        else
            warn "Cannot install PostgreSQL without sudo. Install manually: sudo apt install postgresql-16 postgresql-16-pgvector"
        fi
    else
        ok "PostgreSQL found ($(psql --version 2>/dev/null | head -1))"
        # Ensure pgvector is available
        apt_install postgresql-16-pgvector 2>/dev/null || \
        apt_install postgresql-$(psql --version | grep -oP '\d+' | head -1)-pgvector 2>/dev/null || true
    fi
fi

ok "System dependencies ready"

# ─────────────────────────────────────────────────────────────────────────────
hdr "Step 2/7 — Hermes Agent core"
# ─────────────────────────────────────────────────────────────────────────────

if [[ ! -d "$HERMES_CORE" ]]; then
    fail "core/ directory not found. Make sure you cloned the full Zeus repo."
fi

info "Running Hermes core setup (uv-based)..."
cd "$HERMES_CORE"

# Run the existing setup-hermes.sh non-interactively (skip wizard prompt at end)
if [[ -f "setup-hermes.sh" ]]; then
    # Auto-answer "n" to the wizard prompt — we'll handle config ourselves
    echo "n" | bash setup-hermes.sh
else
    fail "setup-hermes.sh not found in core/"
fi

cd "$ZEUS_DIR"
ok "Hermes core installed"

# ─────────────────────────────────────────────────────────────────────────────
hdr "Step 3/7 — OpenRouter API key & configuration"
# ─────────────────────────────────────────────────────────────────────────────

mkdir -p "$HERMES_HOME"

# Determine API key
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    OR_KEY="$OPENROUTER_API_KEY"
    ok "Using OPENROUTER_API_KEY from environment"
else
    echo ""
    echo -e "  ${BOLD}Get your free OpenRouter API key at:${NC}"
    echo -e "  ${CYAN}https://openrouter.ai/keys${NC}"
    echo ""
    read -rp "  Paste your OpenRouter API key: " OR_KEY
    echo ""
    if [[ -z "$OR_KEY" ]]; then
        warn "No API key provided — you can add it later to $HERMES_HOME/.env"
        OR_KEY="YOUR_OPENROUTER_API_KEY_HERE"
    fi
fi

# Default model
ZEUS_MODEL="${ZEUS_MODEL:-anthropic/claude-sonnet-4}"

# Write .env
ENV_FILE="$HERMES_HOME/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<EOF
# Zeus Framework — Environment Configuration
# Generated by install.sh on $(date -u +"%Y-%m-%d %H:%M UTC")

# ── OpenRouter (required) ────────────────────────────────────────────────────
OPENROUTER_API_KEY=$OR_KEY

# ── PostgreSQL / pgvector (Mnemosyne L3 memory) ──────────────────────────────
POSTGRES_HOST=localhost
POSTGRES_PORT=$PG_PORT
POSTGRES_DB=hermes_vectors
POSTGRES_USER=hermes
POSTGRES_PASSWORD=hermes_local

# ── Redis (L2 cache) ─────────────────────────────────────────────────────────
REDIS_HOST=localhost
REDIS_PORT=6379

# ── Optional integrations ────────────────────────────────────────────────────
# DISCORD_TOKEN=
# TELEGRAM_BOT_TOKEN=
# SLACK_BOT_TOKEN=
EOF
    ok ".env created at $ENV_FILE"
else
    # Patch existing .env — add key if missing
    if ! grep -q "OPENROUTER_API_KEY" "$ENV_FILE"; then
        echo "" >> "$ENV_FILE"
        echo "OPENROUTER_API_KEY=$OR_KEY" >> "$ENV_FILE"
    elif [[ "$OR_KEY" != "YOUR_OPENROUTER_API_KEY_HERE" ]]; then
        sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=$OR_KEY|" "$ENV_FILE"
    fi
    ok ".env updated at $ENV_FILE"
fi

# Write config.yaml
CONFIG_FILE="$HERMES_HOME/config.yaml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    cat > "$CONFIG_FILE" <<EOF
# Zeus Framework — Hermes Agent Configuration
# Generated by install.sh on $(date -u +"%Y-%m-%d %H:%M UTC")

model:
  default: $ZEUS_MODEL
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions

fallback_model:
  provider: openrouter
  model: deepseek/deepseek-r1

fallback_model_3:
  provider: openrouter
  model: anthropic/claude-sonnet-4

toolsets:
  - hermes-cli

agent:
  max_turns: 90
  gateway_timeout: 1800
  restart_drain_timeout: 60
  tool_use_enforcement: auto
  verbose: false
  reasoning_effort: medium

terminal:
  backend: local
  timeout: 180
  persistent_shell: true

browser:
  inactivity_timeout: 120
  command_timeout: 30

checkpoints:
  enabled: true
  max_snapshots: 50

compression:
  enabled: true
  threshold: 0.5
  target_ratio: 0.2

display:
  compact: false
  personality: default
  streaming: true

memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  provider: mnemosyne
  nudge_interval: 10
  flush_min_turns: 6

delegation:
  default_toolsets:
    - terminal
    - file
    - web

skills:
  external_dirs: []

approvals:
  mode: manual

security:
  redact_secrets: true
EOF
    ok "config.yaml created at $CONFIG_FILE"
else
    ok "config.yaml already exists at $CONFIG_FILE"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "Step 4/7 — PostgreSQL user-owned cluster + pgvector"
# ─────────────────────────────────────────────────────────────────────────────

if [[ "${ZEUS_SKIP_POSTGRES:-0}" == "1" ]]; then
    warn "ZEUS_SKIP_POSTGRES=1 — skipping pgvector setup"
else
    # Find pg_ctl
    PG_CTL=""
    for candidate in \
        /usr/lib/postgresql/16/bin/pg_ctl \
        /usr/pgsql-16/bin/pg_ctl \
        $(command -v pg_ctl 2>/dev/null || true); do
        if [[ -x "$candidate" ]]; then
            PG_CTL="$candidate"
            break
        fi
    done

    INITDB=""
    for candidate in \
        /usr/lib/postgresql/16/bin/initdb \
        /usr/pgsql-16/bin/initdb \
        $(command -v initdb 2>/dev/null || true); do
        if [[ -x "$candidate" ]]; then
            INITDB="$candidate"
            break
        fi
    done

    PSQL=""
    for candidate in \
        /usr/lib/postgresql/16/bin/psql \
        $(command -v psql 2>/dev/null || true); do
        if [[ -x "$candidate" ]]; then
            PSQL="$candidate"
            break
        fi
    done

    if [[ -z "$PG_CTL" || -z "$INITDB" || -z "$PSQL" ]]; then
        warn "PostgreSQL binaries not found — skipping pgvector cluster setup."
        warn "Install manually: sudo apt install postgresql-16 postgresql-16-pgvector"
        warn "Then re-run: bash $ZEUS_DIR/scripts/init_pgvector.sh"
    else
        info "PostgreSQL binaries found at: $(dirname $PG_CTL)"

        # Init cluster if not exists
        if [[ ! -d "$PGDATA/base" ]]; then
            info "Initialising user-owned PostgreSQL cluster at $PGDATA..."
            "$INITDB" -D "$PGDATA" -U "$(whoami)" --locale=en_US.UTF-8 --encoding=UTF8
            echo "port = $PG_PORT" >> "$PGDATA/postgresql.conf"
            # Tune for a lightweight agent instance
            cat >> "$PGDATA/postgresql.conf" <<PGCONF

# Zeus tuning
shared_buffers = 128MB
work_mem = 8MB
maintenance_work_mem = 64MB
max_connections = 20
PGCONF
            ok "Cluster initialised at $PGDATA"
        else
            ok "Cluster already initialised at $PGDATA"
        fi

        # Start cluster
        if ! "$PG_CTL" -D "$PGDATA" status &>/dev/null; then
            info "Starting PostgreSQL cluster (port $PG_PORT)..."
            "$PG_CTL" -D "$PGDATA" -l "$PGDATA/logfile" start
            sleep 2
            ok "Cluster started"
        else
            ok "Cluster already running"
        fi

        # Create user + database + tables (idempotent)
        info "Provisioning hermes database and pgvector..."
        "$PSQL" -p $PG_PORT -d postgres <<EOSQL 2>/dev/null || true
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hermes') THEN
    CREATE USER hermes WITH PASSWORD 'hermes_local';
  END IF;
END
\$\$;
EOSQL

        "$PSQL" -p $PG_PORT -d postgres -tc \
            "SELECT 1 FROM pg_database WHERE datname = 'hermes_vectors'" \
            | grep -q 1 || \
            "$PSQL" -p $PG_PORT -d postgres \
                -c "CREATE DATABASE hermes_vectors OWNER hermes;" 2>/dev/null || true

        "$PSQL" -p $PG_PORT -d hermes_vectors <<EOSQL 2>/dev/null || true
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS conversation_memory (
    id          SERIAL PRIMARY KEY,
    source      TEXT,
    content     TEXT,
    embedding   vector(1536),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_base (
    id          SERIAL PRIMARY KEY,
    source      TEXT,
    content     TEXT,
    embedding   vector(1536),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
EOSQL

        ok "hermes_vectors database ready (port $PG_PORT)"

        # Write auto-start snippet to shell config
        SHELL_CONFIG=""
        [[ -f "$HOME/.bashrc" ]] && SHELL_CONFIG="$HOME/.bashrc"
        [[ "$SHELL" == *zsh* && -f "$HOME/.zshrc" ]] && SHELL_CONFIG="$HOME/.zshrc"

        if [[ -n "$SHELL_CONFIG" ]]; then
            PG_AUTOSTART="# Zeus — auto-start PostgreSQL cluster
if command -v $PG_CTL &>/dev/null && ! $PG_CTL -D $PGDATA status &>/dev/null 2>&1; then
    $PG_CTL -D $PGDATA -l $PGDATA/logfile start &>/dev/null
fi"
            if ! grep -q "Zeus — auto-start PostgreSQL" "$SHELL_CONFIG" 2>/dev/null; then
                echo "" >> "$SHELL_CONFIG"
                echo "$PG_AUTOSTART" >> "$SHELL_CONFIG"
                ok "PostgreSQL auto-start added to $SHELL_CONFIG"
            fi
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "Step 5/7 — Mnemosyne L3 memory plugin"
# ─────────────────────────────────────────────────────────────────────────────

MNEMOSYNE_SRC="$ZEUS_DIR/plugins/mnemosyne"
MNEMOSYNE_DST="$HERMES_HOME/plugins/mnemosyne"

if [[ -d "$MNEMOSYNE_SRC" ]]; then
    mkdir -p "$HERMES_HOME/plugins"
    if [[ ! -d "$MNEMOSYNE_DST" ]]; then
        cp -r "$MNEMOSYNE_SRC" "$MNEMOSYNE_DST"
        ok "Mnemosyne plugin installed → $MNEMOSYNE_DST"
    else
        # Sync updates
        rsync -a --update "$MNEMOSYNE_SRC/" "$MNEMOSYNE_DST/" 2>/dev/null || \
            cp -ru "$MNEMOSYNE_SRC/." "$MNEMOSYNE_DST/"
        ok "Mnemosyne plugin synced → $MNEMOSYNE_DST"
    fi
else
    warn "plugins/mnemosyne not found in repo — skipping"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "Step 6/7 — Zeus soul persona"
# ─────────────────────────────────────────────────────────────────────────────

SOUL_SRC="$ZEUS_DIR/soul/SOUL.md"
[[ ! -f "$SOUL_SRC" ]] && SOUL_SRC="$ZEUS_DIR/core/SOUL.md"

if [[ -f "$SOUL_SRC" ]]; then
    cp "$SOUL_SRC" "$HERMES_HOME/persona.md"
    ok "Zeus soul persona activated → $HERMES_HOME/persona.md"
else
    warn "SOUL.md not found — persona not set"
fi

# Copy memory templates
MEMORY_SRC="$ZEUS_DIR/memory"
if [[ -d "$MEMORY_SRC" ]]; then
    mkdir -p "$HERMES_HOME/memory"
    for f in "$MEMORY_SRC"/*.example.md; do
        [[ -f "$f" ]] || continue
        dest="$HERMES_HOME/memory/$(basename "${f%.example.md}").md"
        [[ ! -f "$dest" ]] && cp "$f" "$dest"
    done
    ok "Memory templates copied → $HERMES_HOME/memory/"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "Step 7/7 — Skills sync"
# ─────────────────────────────────────────────────────────────────────────────

SKILLS_SRC="$ZEUS_DIR/skills"
SKILLS_DST="$HERMES_HOME/skills"
mkdir -p "$SKILLS_DST"

if [[ -d "$SKILLS_SRC" ]]; then
    # Try the skills_sync tool first
    VENV_PYTHON="$HERMES_CORE/venv/bin/python"
    SYNC_SCRIPT="$HERMES_CORE/tools/skills_sync.py"
    if [[ -x "$VENV_PYTHON" && -f "$SYNC_SCRIPT" ]]; then
        "$VENV_PYTHON" "$SYNC_SCRIPT" 2>/dev/null && ok "Skills synced via skills_sync.py" || {
            cp -rn "$SKILLS_SRC/"* "$SKILLS_DST/" 2>/dev/null || true
            ok "Skills copied ($(ls "$SKILLS_SRC" | wc -l | tr -d ' ') categories)"
        }
    else
        cp -rn "$SKILLS_SRC/"* "$SKILLS_DST/" 2>/dev/null || true
        ok "Skills copied ($(ls "$SKILLS_SRC" | wc -l | tr -d ' ') categories)"
    fi
else
    warn "skills/ directory not found"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  ⚡ Zeus is ready!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Config:${NC}   $HERMES_HOME/config.yaml"
echo -e "  ${BOLD}Env:${NC}      $HERMES_HOME/.env"
echo -e "  ${BOLD}Persona:${NC}  $HERMES_HOME/persona.md"
echo -e "  ${BOLD}Skills:${NC}   $HERMES_HOME/skills/"
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo ""
echo -e "  ${CYAN}1.${NC} Reload your shell:"
echo -e "     ${DIM}source ~/.bashrc   # or ~/.zshrc${NC}"
echo ""
echo -e "  ${CYAN}2.${NC} Verify the setup:"
echo -e "     ${DIM}zeus doctor${NC}"
echo ""
echo -e "  ${CYAN}3.${NC} Start Zeus:"
echo -e "     ${DIM}zeus${NC}"
echo ""
echo -e "  ${CYAN}4.${NC} (Optional) Run Zeus via gateway (Discord/Telegram/Slack):"
echo -e "     ${DIM}zeus gateway install${NC}"
echo ""
echo -e "  ${DIM}Tip: ask Zeus to upgrade itself anytime:${NC}"
echo -e "  ${DIM}  zeus > \"upgrade yourself to the latest Zeus skills\"${NC}"
echo ""
echo -e "  ${DIM}(The legacy 'hermes' command still works — same binary.)${NC}"
echo ""
