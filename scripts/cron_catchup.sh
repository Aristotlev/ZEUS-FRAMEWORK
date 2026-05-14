#!/usr/bin/env bash
# ============================================================================
# Zeus Framework — cron catch-up
# ============================================================================
# Fires a one-shot run for any content slot whose most recent ledger entry
# is older than its expected fire window. Invoked from the prod entrypoint
# after gateway start so a container outage / deploy gap never silently
# swallows a scheduled post.
#
# Slot grace window (mirrors setup_content_cron.py):
#   article-slot     schedule "0 */2 * * *"   → max gap 2h, grace 3h
#   breaking-news    schedule "*/15 * * * *"  → max gap 15m, grace 12m
#
# Idempotent: every successful pipeline run writes a ledger row, so a
# subsequent boot won't re-fire the same slot. Ledger entries from a
# half-completed run also count as "recent" — we'd rather skip than
# double-post on top of an in-flight pipeline. Breaking-news uses a
# marker file (last_breaking_catchup) instead of the ledger because a
# breaking-news pass that finds no qualifying news writes no ledger row,
# so the ledger alone can't distinguish "watcher didn't run" from
# "watcher ran and nothing qualified". The marker is touched whether or
# not anything ships.
#
# Carousel-slot was disabled 2026-05-08 (user pruned to article-only).
# When re-enabled in setup_content_cron.py, restore the carousel
# check_slot call below.
# ============================================================================
set -u

HERMES_HOME="${HERMES_HOME:-/opt/data}"
ZEUS_DIR="${ZEUS_DIR:-/opt/zeus}"
PYTHON="${PYTHON:-/opt/hermes/.venv/bin/python}"
PIPELINE_REL="skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts"
LEDGER="$HERMES_HOME/.hermes/zeus_cost_ledger.jsonl"
LOG="$HERMES_HOME/cron_catchup.log"
BREAKING_MARKER="$HERMES_HOME/.hermes/last_breaking_catchup"

ARTICLE_GRACE_MIN="${ZEUS_ARTICLE_GRACE_MIN:-180}"   # 3h (cron fires every 2h)
BREAKING_GRACE_MIN="${ZEUS_BREAKING_GRACE_MIN:-12}"  # 12m (cron fires every 15m)

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

log() { printf '[%s] cron_catchup: %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

last_epoch_for() {
    local ctype="$1"
    [ -f "$LEDGER" ] || { echo ""; return; }
    local ts
    ts=$(grep -F "\"content_type\": \"$ctype\"" "$LEDGER" 2>/dev/null \
         | tail -1 \
         | "$PYTHON" -c '
import sys, json
line = sys.stdin.read().strip()
if not line:
    print("")
else:
    try:
        print(json.loads(line)["ts"])
    except Exception:
        print("")
' 2>/dev/null)
    [ -z "$ts" ] && { echo ""; return; }
    date -u -d "$ts" +%s 2>/dev/null
}

fire() {
    local label="$1"; shift
    cd "$ZEUS_DIR" || { log "ERROR: cd $ZEUS_DIR failed"; return 1; }
    local out="$HERMES_HOME/cron_catchup_${label}.log"
    log "firing $label: $PYTHON $PIPELINE_REL/pipeline_test.py $*"
    nohup "$PYTHON" "$PIPELINE_REL/pipeline_test.py" "$@" >> "$out" 2>&1 &
    log "  pid=$! log=$out"
}

fire_script() {
    local label="$1" script="$2"; shift 2
    cd "$ZEUS_DIR" || { log "ERROR: cd $ZEUS_DIR failed"; return 1; }
    local out="$HERMES_HOME/cron_catchup_${label}.log"
    log "firing $label: $PYTHON $PIPELINE_REL/$script $*"
    nohup "$PYTHON" "$PIPELINE_REL/$script" "$@" >> "$out" 2>&1 &
    log "  pid=$! log=$out"
}

check_breaking_news() {
    # Breaking-news doesn't always write a ledger row (a pass with no
    # qualifying news ships nothing → no entry), so we can't use the
    # same ledger-grep technique as long_article. Marker file mtime
    # reflects "watcher last ran" regardless of outcome.
    local now=$(date -u +%s)
    local last=0
    if [ -f "$BREAKING_MARKER" ]; then
        last=$(stat -c %Y "$BREAKING_MARKER" 2>/dev/null || echo 0)
    fi
    local age=$(( (now - last) / 60 ))
    if [ "$last" = 0 ]; then
        log "breaking-news: no marker — firing"
    elif [ "$age" -ge "$BREAKING_GRACE_MIN" ]; then
        log "breaking-news: last pass ${age}min ago (>= ${BREAKING_GRACE_MIN}min grace) — firing"
    else
        log "breaking-news: last pass ${age}min ago (< ${BREAKING_GRACE_MIN}min grace) — skip"
        return
    fi
    # Touch marker BEFORE firing so two near-simultaneous catchup invocations
    # (rare, but possible if entrypoint races a docker restart) don't double-fire.
    # The script's own per-hour rate cap is a backstop, not the first line.
    mkdir -p "$(dirname "$BREAKING_MARKER")" 2>/dev/null || true
    touch "$BREAKING_MARKER" 2>/dev/null || true
    fire_script breaking_news breaking_news_watch.py
}

check_slot() {
    local label="$1" ctype="$2" grace_min="$3"; shift 3
    local last age
    last=$(last_epoch_for "$ctype")
    if [ -z "$last" ]; then
        log "$label: no $ctype ledger entry — firing"
        fire "$label" "$@"
        return
    fi
    local now=$(date -u +%s)
    age=$(( (now - last) / 60 ))
    if [ "$age" -ge "$grace_min" ]; then
        log "$label: last $ctype was ${age}min ago (>= ${grace_min}min grace) — firing"
        fire "$label" "$@"
    else
        log "$label: last $ctype was ${age}min ago (< ${grace_min}min grace) — skip"
    fi
}

log "=== cron_catchup start ==="
# Match the cron's content_type: article-slot uses --type long_article,
# which the ledger records as content_type="long_article".
check_slot article long_article "$ARTICLE_GRACE_MIN" --type long_article --auto --publish
# Breaking-news watcher: cron */15 — replays a pass if no run in the
# last 12min (covers gateway-restart windows that drop one or two ticks).
# Must run AFTER article catchup because both writes serialize through
# pipeline_test.py's grounding phase and we want long-form to win the
# race if both are stale (long-form takes ~3min, short-form ~30s).
check_breaking_news
log "=== cron_catchup done ==="
