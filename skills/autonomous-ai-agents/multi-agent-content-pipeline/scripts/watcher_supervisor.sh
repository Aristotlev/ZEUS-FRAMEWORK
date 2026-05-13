#!/usr/bin/env bash
# Idempotent supervisor for the publish_watcher daemon.
#
# Run via the once-a-day publish-ready cron BEFORE invoking publish_from_notion.
# If the daemon is alive, this is a no-op (~50ms). If dead, it spawns a
# self-respawning loop so individual python crashes don't permanently kill
# permalink resolution.
#
# Lifecycle:
#   first run after container boot          -> starts the supervisor loop
#   subsequent runs while daemon alive      -> no-op
#   daemon python crashes                   -> supervisor respawns it (10s)
#   container restarts                      -> next publish-ready cron re-spawns
#                                              (worst-case 24h offline window
#                                              between container restart + cron)
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER="$SCRIPT_DIR/publish_watcher.py"
PYTHON="${ZEUS_PYTHON:-/opt/hermes/.venv/bin/python}"
PID_FILE="$HERMES_HOME/.hermes/zeus_watcher_daemon.pid"
LOG_FILE="$HERMES_HOME/.hermes/zeus_watcher_daemon.log"
INTERVAL="${ZEUS_WATCHER_INTERVAL:-120}"

mkdir -p "$(dirname "$PID_FILE")"

if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || echo "")"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "watcher daemon already alive (pid=$PID)"
        exit 0
    fi
    echo "stale pid file at $PID_FILE — daemon not running, restarting"
fi

# Self-respawning bash loop. The watcher's --daemon mode polls the queue
# every $INTERVAL seconds in-memory; if the python process crashes (network
# blip, memory error, anything), the bash loop respawns it after 10s. This
# replaces the every-10-min agent-driven cron — no per-tick LLM overhead,
# faster permalink resolution.
nohup bash -c "
    while true; do
        printf '%s starting watcher daemon...\n' \"\$(date -Is)\"
        '$PYTHON' '$WATCHER' --daemon --interval $INTERVAL
        rc=\$?
        printf '%s watcher daemon exited rc=%s — respawning in 10s\n' \"\$(date -Is)\" \"\$rc\"
        sleep 10
    done
" > "$LOG_FILE" 2>&1 &

SUPERVISOR_PID=$!
# rm before write: a stale pid file owned by root (from a prior root-mode run)
# can block the hermes-uid `>` redirect even though the parent dir is hermes-owned.
rm -f "$PID_FILE" 2>/dev/null || true
echo "$SUPERVISOR_PID" > "$PID_FILE"
echo "watcher supervisor started (pid=$SUPERVISOR_PID, log=$LOG_FILE)"
