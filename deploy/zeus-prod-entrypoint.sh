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
    # Same usermod-after-build issue for /opt/hermes: the venv is built as
    # uid 10000 in the image but HERMES_UID=1000 at runtime, so uv pip
    # installs (e.g. soft-dep pypdf below) fail with EACCES on every boot.
    if [ "$(stat -c %u "$HERMES_INSTALL" 2>/dev/null)" != "$actual_uid" ]; then
        chown -R hermes:hermes "$HERMES_INSTALL" 2>/dev/null || true
    fi

    # Run setup_content_cron.py NOW, as hermes via gosu, before we hand off
    # to the hermes-phase bootstrap. The hermes-phase used to invoke this
    # script itself, but the upstream Hermes bootstrap can re-elevate
    # between gosu and the python call — when that happens jobs.json lands
    # root:600 and the gateway (running as hermes) cannot read it →
    # every cron silently dies. Doing it from the root phase via an
    # explicit gosu hermes pins the writer uid deterministically. A marker
    # file tells the hermes-phase block downstream to skip its duplicate
    # invocation.
    SETUP_CRON_PRE="$ZEUS_DIR/scripts/setup_content_cron.py"
    if [ -f "$SETUP_CRON_PRE" ]; then
        echo "[zeus-prod] resyncing zeus-content-* cron jobs (pre-gosu, as hermes)"
        gosu hermes "$HERMES_INSTALL/.venv/bin/python" "$SETUP_CRON_PRE" \
            2>&1 | sed 's/^/[zeus-prod cron-sync] /' || \
            echo "[zeus-prod] WARN: setup_content_cron.py exited non-zero"
        gosu hermes touch "$HERMES_HOME/.cron-resynced-this-boot" 2>/dev/null || true
    fi
    # Belt-and-braces: even if the gosu-hermes invocation above ended up
    # writing jobs.json as something other than hermes (e.g. an in-process
    # re-elevation we don't know about), re-assert ownership here while we
    # still have root.
    if [ -f "$HERMES_HOME/cron/jobs.json" ]; then
        chown hermes:hermes "$HERMES_HOME/cron/jobs.json" 2>/dev/null || true
        chmod 644 "$HERMES_HOME/cron/jobs.json" 2>/dev/null || true
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
    [ -z "$val" ] && continue
    if grep -q "^${var}=" "$HERMES_HOME/.env"; then
        # Host env is authoritative — refresh existing entries so a
        # `compose up` env change reaches cron's execute_code subprocess.
        # Previous "set if missing" silently dropped updates.
        esc=$(printf '%s' "$val" | sed -e 's/[\/&|]/\\&/g')
        sed -i "s|^${var}=.*|${var}=${esc}|" "$HERMES_HOME/.env"
    else
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
  # Hermes strips category=tool secrets (TAVILY_API_KEY, etc.) from execute_code
  # subprocess env by default. The content pipeline's grounding phase runs
  # pipeline_test.py via execute_code and needs Tavily live — without the
  # allowlist, _tavily_cross_sources() silently returns [] and the writer
  # loses cross-outlet grounding. MARKETAUX_API_KEY is not in the blocklist.
  # SUBSTACK_SID + SUBSTACK_PUBLICATION_URL are stripped the same way; without
  # the allowlist _publish_substack() reads empty strings and silently skips,
  # so substack shows up as 'skipped' in the watcher and the run finalises
  # 'posted' without ever hitting Substack.
  # ZEUS_TWITTER_TIER + the three knob overrides: stripping these forces
  # lib/platforms.py to fall back to free-tier defaults (TWITTER_LIMIT=280).
  # caption_for(piece, "twitter") then truncates every LONG_ARTICLE body to
  # 278c with "…" BEFORE _publer_schedule sees the text, which silently
  # disables the len(text)>280 → details.long_post path. Symptom seen in
  # prod 2026-05-14 on the 10:00 UTC cron: body=1500c+ generated, post on X
  # came back text_len=278 truncated despite container env having
  # ZEUS_TWITTER_TIER=premium.
  env_passthrough:
    - TAVILY_API_KEY
    - SUBSTACK_SID
    - SUBSTACK_PUBLICATION_URL
    - ZEUS_TWITTER_TIER
    - ZEUS_TWITTER_LIMIT
    - ZEUS_TWITTER_THREAD_TRIGGER
    - ZEUS_TWITTER_TWEET_BUDGET

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

# Soft deps for the Notion ideas ingester. pypdf is needed when users drop
# PDFs into the Content Ideas DB (extract_pdf_text falls back to a no-op
# without it, so the rest of the pipeline still runs — but PDF support
# is silently broken). Hermes's uv venv ships without `pip` itself, so we
# use the system uv binary. Idempotent: uv skips the install if installed.
if command -v uv >/dev/null 2>&1; then
    uv pip install --quiet --python "$HERMES_INSTALL/.venv/bin/python" pypdf 2>&1 \
        | sed 's/^/[zeus-prod uv] /' || \
        echo "[zeus-prod] WARN: pypdf install failed (PDF attachments will be ignored)"
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

# Re-sync the cron job lineup from setup_content_cron.py. Idempotent —
# wipes any existing zeus-content-* jobs first, then recreates from the
# current spec. This means edits to setup_content_cron.py (schedule
# changes, prompt tweaks, adding/removing slots) take effect on the next
# `git pull && docker restart zeus-agent` without a separate SSH step.
SETUP_CRON="$ZEUS_DIR/scripts/setup_content_cron.py"
if [ -f "$HERMES_HOME/.cron-resynced-this-boot" ]; then
    rm -f "$HERMES_HOME/.cron-resynced-this-boot" 2>/dev/null || true
    echo "[zeus-prod] cron resync already done in root phase (as hermes via gosu), skipping"
elif [ -f "$SETUP_CRON" ]; then
    echo "[zeus-prod] resyncing zeus-content-* cron jobs from setup_content_cron.py"
    "$HERMES_INSTALL/.venv/bin/python" "$SETUP_CRON" \
        2>&1 | sed 's/^/[zeus-prod cron-sync] /' || \
        echo "[zeus-prod] WARN: setup_content_cron.py exited non-zero"
fi

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

# Cron catch-up — fires a one-shot pipeline run for any content slot whose
# most recent ledger entry exceeds the slot's grace window. Protects against
# deploy windows / gateway crashes silently swallowing scheduled posts (e.g.
# the 21:00→04:00 article + 00:30 carousel slots dropped during the
# 2026-05-07→05-08 Discord-token gateway crash loop).
#
# Idempotent (writes a fresh ledger row on success → next boot won't re-fire)
# and async (sleeps 90s so the gateway is up before we start a long-running
# pipeline). Safe to run on every boot.
CATCHUP_SCRIPT="$ZEUS_DIR/scripts/cron_catchup.sh"
if [ -x "$CATCHUP_SCRIPT" ]; then
    echo "[zeus-prod] scheduling cron catch-up (in 90s, async)"
    ( sleep 90 && bash "$CATCHUP_SCRIPT" ) >/dev/null 2>&1 &
else
    echo "[zeus-prod] WARN: cron_catchup.sh not found at $CATCHUP_SCRIPT"
fi

# Foreground: run the gateway. Its built-in cron-ticker thread fires the jobs
# registered by setup_content_cron.py every 60s. When the gateway exits the
# container exits, and `restart: unless-stopped` brings it back up.
exec hermes gateway run
