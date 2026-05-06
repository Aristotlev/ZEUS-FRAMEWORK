#!/usr/bin/env bash
# ============================================================================
# Zeus Framework — production entrypoint
# ============================================================================
# Reuses the standard zeus-entrypoint.sh bootstrap (config, mnemosyne, soul,
# skills sync, dependency wait), then ENDS in a heartbeat loop instead of an
# interactive `hermes` REPL.
#
# Why: production needs the agent container to stay alive without a TTY so
# `docker exec` can fire pipeline runs (cron jobs registered via
# setup_content_cron.py inside the container, or host-level systemd timers).
# ============================================================================
set -e

HERMES_HOME="${HERMES_HOME:-/opt/data}"
ZEUS_DIR="${ZEUS_DIR:-/opt/zeus}"
HERMES_INSTALL="/opt/hermes"

# ── Privilege drop (mirrors zeus-entrypoint.sh) ──────────────────────────────
if [ "$(id -u)" = "0" ]; then
    if [ -n "${HERMES_UID:-}" ] && [ "$HERMES_UID" != "$(id -u hermes)" ]; then
        usermod -u "$HERMES_UID" hermes
    fi
    if [ -n "${HERMES_GID:-}" ] && [ "$HERMES_GID" != "$(id -g hermes)" ]; then
        groupmod -o -g "$HERMES_GID" hermes 2>/dev/null || true
    fi
    actual_uid=$(id -u hermes)
    if [ "$(stat -c %u "$HERMES_HOME" 2>/dev/null)" != "$actual_uid" ]; then
        chown -R hermes:hermes "$HERMES_HOME" 2>/dev/null || true
    fi
    # Force-fix cron/ ownership every boot: setup_content_cron.py was historically
    # invoked as root (via plain `docker exec` without `-u hermes`), leaving
    # jobs.json root:600 and unreadable to the hermes-uid gateway → cron silently
    # dead. Cheap to make idempotent.
    if [ -d "$HERMES_HOME/cron" ]; then
        chown -R hermes:hermes "$HERMES_HOME/cron" 2>/dev/null || true
    fi
    exec gosu hermes "$0" "$@"
fi

# ── Run the standard bootstrap (config, plugins, skills, dep wait) ───────────
# We source the upstream entrypoint up to (but not including) `exec hermes`.
# Trick: replace the final exec line with a no-op via a wrapper that sets
# HERMES_BOOTSTRAP_ONLY and short-circuits in our copy below.
#
# Cleanest path: just inline the same bootstrap steps. They're idempotent.

source "${HERMES_INSTALL}/.venv/bin/activate"

mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills,skins,plans,workspace,home,plugins,memory,webhooks}

# .env (idempotent)
if [ ! -f "$HERMES_HOME/.env" ]; then
    cat > "$HERMES_HOME/.env" <<EOF
OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
POSTGRES_HOST=${POSTGRES_HOST:-postgres}
POSTGRES_PORT=${POSTGRES_PORT:-5432}
POSTGRES_DB=${POSTGRES_DB:-hermes_vectors}
POSTGRES_USER=${POSTGRES_USER:-hermes}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-hermes_local}
REDIS_HOST=${REDIS_HOST:-redis}
REDIS_PORT=${REDIS_PORT:-6379}
EOF
elif [ -n "${OPENROUTER_API_KEY:-}" ]; then
    if grep -q "OPENROUTER_API_KEY" "$HERMES_HOME/.env"; then
        sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=$OPENROUTER_API_KEY|" "$HERMES_HOME/.env"
    else
        echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" >> "$HERMES_HOME/.env"
    fi
fi

# Pass through the full content-pipeline env (fal, fish, notion, publer, etc)
# so cron-fired pipeline_test.py can see them. Only adds keys not already set.
for var in FAL_KEY FISH_AUDIO_API_KEY ZEUS_FISH_VOICE_DEFAULT \
           NOTION_API_KEY NOTION_ARCHIVE_DB_ID NOTION_PIPELINE_DB_ID ZEUS_NOTION_HUB_PAGE_ID \
           PUBLER_API_KEY PUBLER_WORKSPACE_ID \
           PUBLER_TWITTER_ID PUBLER_INSTAGRAM_ID PUBLER_LINKEDIN_ID \
           PUBLER_TIKTOK_ID PUBLER_YOUTUBE_ID PUBLER_FACEBOOK_ID PUBLER_REDDIT_ID \
           RESEND_API_KEY AGENTMAIL_API_KEY AGENTMAIL_INBOX \
           HERMES_GMAIL_USER HERMES_GMAIL_APP_PASSWORD \
           HCLOUD_TOKEN \
           PICKER_MODEL \
           ZEUS_NOTIFY_EMAIL ZEUS_NOTIFY_FROM_NAME ZEUS_NOTIFY_FROM_EMAIL; do
    val="${!var:-}"
    if [ -n "$val" ] && ! grep -q "^${var}=" "$HERMES_HOME/.env"; then
        echo "${var}=${val}" >> "$HERMES_HOME/.env"
    fi
done

# config.yaml (idempotent)
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
    ZEUS_MODEL="${ZEUS_MODEL:-deepseek/deepseek-v4-pro}"
    cat > "$HERMES_HOME/config.yaml" <<EOF
model:
  default: $ZEUS_MODEL
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions

fallback_model:
  provider: openrouter
  model: deepseek/deepseek-r1

toolsets:
  - hermes-cli

agent:
  max_turns: 90
  gateway_timeout: 1800
  tool_use_enforcement: auto
  verbose: false
  reasoning_effort: medium

terminal:
  backend: local
  timeout: 180
  persistent_shell: true

memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  provider: mnemosyne
  nudge_interval: 10
  flush_min_turns: 6

approvals:
  mode: manual

security:
  redact_secrets: true

content_pipeline:
  # Set your niche before running the content cron jobs. Accepts a string or list.
  # Examples: "ai research" or [ai, machine learning, tech startups]
  niche: []
  cron_model: deepseek/deepseek-v4-flash
  cron_provider: openrouter
  cron_base_url: https://openrouter.ai/api/v1
EOF
fi

# Soul persona
if [ ! -f "$HERMES_HOME/persona.md" ] && [ -f "$ZEUS_DIR/SOUL.md" ]; then
    cp "$ZEUS_DIR/SOUL.md" "$HERMES_HOME/persona.md"
fi

# Mnemosyne plugin
if [ -d "$ZEUS_DIR/plugins/mnemosyne" ] && [ ! -d "$HERMES_HOME/plugins/mnemosyne" ]; then
    cp -r "$ZEUS_DIR/plugins/mnemosyne" "$HERMES_HOME/plugins/"
fi

# Memory templates
if [ -d "$ZEUS_DIR/memory" ]; then
    for f in "$ZEUS_DIR/memory"/*.example.md; do
        [ -f "$f" ] || continue
        dest="$HERMES_HOME/memory/$(basename "${f%.example.md}").md"
        [ ! -f "$dest" ] && cp "$f" "$dest"
    done
fi

# Skills sync
if [ -d "$ZEUS_DIR/skills" ]; then
    cp -rn "$ZEUS_DIR/skills/"* "$HERMES_INSTALL/skills/" 2>/dev/null || true
fi
if [ -f "$HERMES_INSTALL/tools/skills_sync.py" ]; then
    python3 "$HERMES_INSTALL/tools/skills_sync.py" 2>/dev/null || \
        cp -rn "$HERMES_INSTALL/skills/"* "$HERMES_HOME/skills/" 2>/dev/null || true
fi

# Wait for Redis + Postgres
wait_for() {
    local host="$1" port="$2" name="$3" retries=30
    for i in $(seq 1 $retries); do
        if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('$host',$port)); s.close()" 2>/dev/null; then
            echo "[zeus-prod] $name ready"
            return 0
        fi
        sleep 1
    done
    echo "[zeus-prod] WARN: $name not reachable after ${retries}s"
}
wait_for "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}" "Redis"
wait_for "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "PostgreSQL"

echo "[zeus-prod] bootstrap complete — starting gateway (drives cron jobs)"

# publish_watcher daemon — resolves Publer permalinks and fires the post-run
# email once every platform is live. Without this, content runs land in Publer
# fine, but Notion never gets patched with post URLs and no email arrives.
# Used to start only via the publish-ready cron (06:30 UTC daily) — meaning a
# container restart at 07:00 UTC would leave the daemon dead for ~23.5h.
# Supervisor is idempotent (no-op if alive), so safe to invoke on every boot.
WATCHER_SUPERVISOR="$ZEUS_DIR/skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/watcher_supervisor.sh"
if [ -x "$WATCHER_SUPERVISOR" ]; then
    echo "[zeus-prod] starting publish_watcher daemon"
    bash "$WATCHER_SUPERVISOR" || echo "[zeus-prod] WARN: watcher_supervisor exit non-zero"
else
    echo "[zeus-prod] WARN: watcher_supervisor.sh not found at $WATCHER_SUPERVISOR"
fi

# Background heartbeat — proves the entrypoint is alive to /health and the
# docker healthcheck. Touched every 60s; healthcheck tolerates 30min.
( while true; do touch "$HERMES_HOME/.heartbeat"; sleep 60; done ) &

# Foreground: run the gateway. Its built-in cron-ticker thread fires the jobs
# registered by setup_content_cron.py every 60s. When the gateway exits the
# container exits, and `restart: unless-stopped` brings it back up.
exec hermes gateway run
