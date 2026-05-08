#!/usr/bin/env bash
# ============================================================================
# Zeus Framework — cron catch-up
# ============================================================================
# Fires a one-shot run for any content slot whose most recent ledger entry
# is older than its expected fire window. Invoked from the prod entrypoint
# after gateway start so a container outage / deploy gap never silently
# swallows a scheduled post.
#
# Slot grace windows (mirror setup_content_cron.py schedules):
#   article-slot   schedule "0 0,4,8,12,17,21 * * *"  → max gap 5h, grace 6h
#   carousel-slot  schedule "30 0,12 * * *"           → max gap 12h, grace 13h
#
# Idempotent: every successful pipeline run writes a ledger row, so a
# subsequent boot won't re-fire the same slot. Ledger entries from a
# half-completed run also count as "recent" — we'd rather skip than
# double-post on top of an in-flight pipeline.
# ============================================================================
set -u

HERMES_HOME="${HERMES_HOME:-/opt/data}"
ZEUS_DIR="${ZEUS_DIR:-/opt/zeus}"
PYTHON="${PYTHON:-/opt/hermes/.venv/bin/python}"
PIPELINE_REL="skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts"
LEDGER="$HERMES_HOME/.hermes/zeus_cost_ledger.jsonl"
LOG="$HERMES_HOME/cron_catchup.log"

ARTICLE_GRACE_MIN="${ZEUS_ARTICLE_GRACE_MIN:-360}"   # 6h
CAROUSEL_GRACE_MIN="${ZEUS_CAROUSEL_GRACE_MIN:-780}" # 13h

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
check_slot article  article  "$ARTICLE_GRACE_MIN"  --type long_article --auto --publish
check_slot carousel carousel "$CAROUSEL_GRACE_MIN" --type carousel --auto --slides 4 --publish
log "=== cron_catchup done ==="
