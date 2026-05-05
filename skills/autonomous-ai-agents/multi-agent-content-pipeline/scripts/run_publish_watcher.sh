#!/bin/bash
# Wrapper for publish_watcher.py — designed for cron / launchd / systemd.
# Loads ~/.hermes/.env, finds the repo's .venv automatically, runs one pass,
# appends to a log so silent scheduler failures are debuggable.
#
# Override ZEUS_ENV_FILE / ZEUS_WATCHER_LOG / ZEUS_VENV via environment.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIPELINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Walk up from the pipeline dir until we find a .venv (covers any clone path).
ZEUS_ROOT="$PIPELINE_DIR"
while [ "$ZEUS_ROOT" != "/" ] && [ ! -d "$ZEUS_ROOT/.venv" ]; do
  ZEUS_ROOT="$(dirname "$ZEUS_ROOT")"
done

VENV_PYTHON="${ZEUS_VENV:-$ZEUS_ROOT/.venv/bin/python}"
ENV_FILE="${ZEUS_ENV_FILE:-$HOME/.hermes/.env}"
LOG="${ZEUS_WATCHER_LOG:-$HOME/.hermes/zeus_publish_watcher.log}"

mkdir -p "$(dirname "$LOG")"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "[$(date -u +%FT%TZ)] FATAL: venv python not found at $VENV_PYTHON" >> "$LOG"
  exit 2
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

cd "$PIPELINE_DIR"
echo "[$(date -u +%FT%TZ)] watcher pass starting" >> "$LOG"
exec "$VENV_PYTHON" "$SCRIPT_DIR/publish_watcher.py" --once >> "$LOG" 2>&1
