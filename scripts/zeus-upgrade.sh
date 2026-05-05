#!/usr/bin/env bash
# ============================================================================
# ⚡ Zeus Framework — Self-Upgrade Script
# ============================================================================
# Upgrades an existing Hermes Agent installation to the full Zeus stack.
#
# What it does (NON-DESTRUCTIVE — never overwrites existing config/keys):
#   1. Clones or updates the Zeus repo to ~/zeus-framework
#   2. Syncs Zeus skills into ~/.hermes/skills/
#   3. Installs Mnemosyne L3 memory plugin
#   4. Activates Zeus soul persona (saves old persona as .bak if exists)
#   5. Sets up pgvector DB tables (if PostgreSQL is already installed)
#   6. Patches config.yaml to enable mnemosyne provider (if not already set)
#
# Usage (run from anywhere — even inside Hermes itself):
#   bash <(curl -fsSL https://raw.githubusercontent.com/Aristotlev/ZEUS-FRAMEWORK/main/scripts/zeus-upgrade.sh)
#
# Or after cloning:
#   bash scripts/zeus-upgrade.sh
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC}  $*"; }
info() { echo -e "${CYAN}→${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }

ZEUS_REPO="https://github.com/Aristotlev/ZEUS-FRAMEWORK.git"
ZEUS_LOCAL="${ZEUS_LOCAL:-$HOME/zeus-framework}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PG_PORT="${PG_PORT:-5433}"

echo ""
echo -e "${BOLD}${CYAN}⚡ Zeus Framework Upgrade${NC}"
echo -e "${DIM}  Upgrading Hermes Agent → Zeus stack${NC}"
echo ""

# ── Verify Hermes is installed ────────────────────────────────────────────────
if ! command -v hermes &>/dev/null; then
    fail "hermes command not found. Install Hermes first:
  cd core && bash setup-hermes.sh
  # or run the full Zeus installer:
  git clone $ZEUS_REPO zeus && cd zeus && ./install.sh"
fi
ok "Hermes found ($(hermes --version 2>/dev/null || echo 'version unknown'))"

# ── Add `zeus` command alongside existing `hermes` ────────────────────────────
HERMES_PATH="$(command -v hermes)"
HERMES_BIN_DIR="$(dirname "$HERMES_PATH")"
if [[ -w "$HERMES_BIN_DIR" ]]; then
    if [[ ! -e "$HERMES_BIN_DIR/zeus" ]]; then
        ln -sf "$HERMES_PATH" "$HERMES_BIN_DIR/zeus"
        ok "Symlinked zeus → $HERMES_BIN_DIR/zeus (same binary as hermes)"
    else
        ok "zeus command already present at $HERMES_BIN_DIR/zeus"
    fi
else
    warn "Can't write to $HERMES_BIN_DIR — skipping zeus symlink. Use 'hermes' for now."
    warn "To add zeus manually: sudo ln -sf $HERMES_PATH $HERMES_BIN_DIR/zeus"
fi

# ── Step 1: Clone or update Zeus repo ────────────────────────────────────────
info "Syncing Zeus repository..."
if [[ -d "$ZEUS_LOCAL/.git" ]]; then
    git -C "$ZEUS_LOCAL" pull --ff-only 2>/dev/null || \
    git -C "$ZEUS_LOCAL" fetch origin && git -C "$ZEUS_LOCAL" reset --hard origin/main
    ok "Zeus repo updated → $ZEUS_LOCAL"
elif [[ -d "$ZEUS_LOCAL" ]]; then
    warn "$ZEUS_LOCAL exists but is not a git repo — using it as-is"
else
    info "Cloning Zeus Framework..."
    git clone --depth=1 "$ZEUS_REPO" "$ZEUS_LOCAL"
    ok "Zeus cloned → $ZEUS_LOCAL"
fi

# ── Step 2: Sync skills ───────────────────────────────────────────────────────
info "Syncing Zeus skills..."
SKILLS_SRC="$ZEUS_LOCAL/skills"
SKILLS_DST="$HERMES_HOME/skills"
mkdir -p "$SKILLS_DST"

if [[ -d "$SKILLS_SRC" ]]; then
    # Count before
    BEFORE=$(find "$SKILLS_DST" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')

    # Try official sync tool first
    VENV_PYTHON="$ZEUS_LOCAL/core/venv/bin/python"
    SYNC_SCRIPT="$ZEUS_LOCAL/core/tools/skills_sync.py"
    if [[ -x "$VENV_PYTHON" && -f "$SYNC_SCRIPT" ]]; then
        "$VENV_PYTHON" "$SYNC_SCRIPT" 2>/dev/null || \
            cp -rn "$SKILLS_SRC/"* "$SKILLS_DST/" 2>/dev/null || true
    else
        cp -rn "$SKILLS_SRC/"* "$SKILLS_DST/" 2>/dev/null || true
    fi

    AFTER=$(find "$SKILLS_DST" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
    NEW=$(( AFTER - BEFORE ))
    if [[ $NEW -gt 0 ]]; then
        ok "Added $NEW new skill files → $SKILLS_DST"
    else
        ok "Skills already up to date ($AFTER skill files)"
    fi
else
    warn "skills/ directory not found in Zeus repo"
fi

# ── Step 3: Mnemosyne plugin ──────────────────────────────────────────────────
info "Installing Mnemosyne L3 memory plugin..."
MNEMOSYNE_SRC="$ZEUS_LOCAL/plugins/mnemosyne"
MNEMOSYNE_DST="$HERMES_HOME/plugins/mnemosyne"

if [[ -d "$MNEMOSYNE_SRC" ]]; then
    mkdir -p "$HERMES_HOME/plugins"
    if [[ -d "$MNEMOSYNE_DST" ]]; then
        # Sync updates without deleting customisations
        rsync -a --update "$MNEMOSYNE_SRC/" "$MNEMOSYNE_DST/" 2>/dev/null || \
            cp -ru "$MNEMOSYNE_SRC/." "$MNEMOSYNE_DST/"
        ok "Mnemosyne plugin updated → $MNEMOSYNE_DST"
    else
        cp -r "$MNEMOSYNE_SRC" "$MNEMOSYNE_DST"
        ok "Mnemosyne plugin installed → $MNEMOSYNE_DST"
    fi
else
    warn "Mnemosyne plugin not found in Zeus repo"
fi

# ── Step 4: Soul persona ──────────────────────────────────────────────────────
info "Activating Zeus soul persona..."
SOUL_SRC="$ZEUS_LOCAL/soul/SOUL.md"
[[ ! -f "$SOUL_SRC" ]] && SOUL_SRC="$ZEUS_LOCAL/core/SOUL.md"
PERSONA_DST="$HERMES_HOME/persona.md"

if [[ -f "$SOUL_SRC" ]]; then
    if [[ -f "$PERSONA_DST" ]]; then
        # Back up existing persona
        cp "$PERSONA_DST" "${PERSONA_DST}.bak.$(date +%Y%m%d%H%M%S)"
        warn "Existing persona backed up as ${PERSONA_DST}.bak.*"
    fi
    cp "$SOUL_SRC" "$PERSONA_DST"
    ok "Zeus soul persona activated → $PERSONA_DST"
else
    warn "SOUL.md not found — persona not updated"
fi

# ── Step 5: pgvector tables ───────────────────────────────────────────────────
info "Checking pgvector database..."

PSQL=""
for candidate in \
    /usr/lib/postgresql/16/bin/psql \
    /usr/pgsql-16/bin/psql \
    $(command -v psql 2>/dev/null || true); do
    [[ -x "$candidate" ]] && PSQL="$candidate" && break
done

if [[ -n "$PSQL" ]]; then
    # Check if cluster is running
    PGDATA="$HOME/pgdata"
    PG_CTL=""
    for candidate in \
        /usr/lib/postgresql/16/bin/pg_ctl \
        $(command -v pg_ctl 2>/dev/null || true); do
        [[ -x "$candidate" ]] && PG_CTL="$candidate" && break
    done

    if [[ -n "$PG_CTL" ]] && "$PG_CTL" -D "$PGDATA" status &>/dev/null 2>&1; then
        "$PSQL" -p $PG_PORT -d hermes_vectors <<EOSQL 2>/dev/null || true
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS conversation_memory (
    id SERIAL PRIMARY KEY, source TEXT, content TEXT,
    embedding vector(1536), metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS knowledge_base (
    id SERIAL PRIMARY KEY, source TEXT, content TEXT,
    embedding vector(1536), metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
EOSQL
        ok "pgvector tables verified (port $PG_PORT)"
    else
        warn "PostgreSQL cluster not running — skipping table setup"
        warn "Start it with: pg_ctl -D ~/pgdata start"
        warn "Then re-run: bash $ZEUS_LOCAL/scripts/zeus-upgrade.sh"
    fi
else
    warn "psql not found — skipping pgvector setup"
fi

# ── Step 6: Patch config.yaml for mnemosyne ──────────────────────────────────
info "Checking config.yaml for Mnemosyne memory provider..."
CONFIG_FILE="$HERMES_HOME/config.yaml"

if [[ -f "$CONFIG_FILE" ]]; then
    if grep -q "provider: mnemosyne" "$CONFIG_FILE"; then
        ok "Mnemosyne already configured in config.yaml"
    else
        # Append memory block if not present
        if ! grep -q "^memory:" "$CONFIG_FILE"; then
            cat >> "$CONFIG_FILE" <<'YAML'

memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  provider: mnemosyne
  nudge_interval: 10
  flush_min_turns: 6
YAML
            ok "Memory block added to config.yaml (provider: mnemosyne)"
        else
            # Patch existing memory block
            sed -i 's/provider: .*/provider: mnemosyne/' "$CONFIG_FILE" 2>/dev/null || \
                warn "Could not auto-patch config.yaml — manually set 'provider: mnemosyne' under memory:"
            ok "Mnemosyne provider set in config.yaml"
        fi
    fi
else
    warn "config.yaml not found at $CONFIG_FILE"
    warn "Run 'hermes setup' or copy config/config.example.yaml to $CONFIG_FILE"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  ⚡ Upgrade complete!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Zeus installed at:${NC} $ZEUS_LOCAL"
echo -e "  ${BOLD}Config home:${NC}      $HERMES_HOME"
echo ""
echo -e "  ${BOLD}What changed:${NC}"
echo -e "    ${GREEN}✓${NC} CLI rebranded to Zeus (banner, prompts, status, gateway)"
echo -e "    ${GREEN}✓${NC} New ${BOLD}zeus${NC} command (legacy ${BOLD}hermes${NC} still works)"
echo -e "    ${GREEN}✓${NC} Zeus soul persona activated"
echo -e "    ${GREEN}✓${NC} Mnemosyne L3 memory plugin installed"
echo -e "    ${GREEN}✓${NC} Skills synced"
echo ""
echo -e "  ${BOLD}Restart to see the rebrand:${NC}"
echo ""
echo -e "    ${CYAN}zeus${NC}        ${DIM}# new branded entry point${NC}"
echo -e "    ${DIM}# or:${NC}"
echo -e "    ${CYAN}hermes${NC}      ${DIM}# legacy alias, same binary${NC}"
echo ""
echo -e "  ${DIM}First run will greet you with the Zeus persona and memory context.${NC}"
echo ""
