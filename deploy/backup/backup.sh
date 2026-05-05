#!/usr/bin/env bash
# ============================================================================
# Zeus Framework — Daily backup
# ============================================================================
#   1. pg_dump into /var/backups/zeus/pg_<date>.sql.gz
#   2. tar of the hermes-data volume into /var/backups/zeus/data_<date>.tar.gz
#   3. If B2_* env vars are set, rclone push to Backblaze B2
#   4. Prune local backups older than 14 days
# ============================================================================
set -euo pipefail

DATE="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/zeus}"
ENV_FILE="${ZEUS_ENV_FILE:-/opt/zeus/deploy/.env.prod}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

# ── Load env (B2 creds, postgres password) ───────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

PG_FILE="$BACKUP_DIR/pg_${DATE}.sql.gz"
DATA_FILE="$BACKUP_DIR/data_${DATE}.tar.gz"

echo "[backup] $(date -u) — starting"

# ── 1. Postgres dump (via docker exec into the running container) ────────────
echo "[backup] dumping postgres → $PG_FILE"
docker exec -e PGPASSWORD="${POSTGRES_PASSWORD:-}" zeus-postgres \
    pg_dump -U "${POSTGRES_USER:-hermes}" -d "${POSTGRES_DB:-hermes_vectors}" \
    | gzip -9 > "$PG_FILE"

# ── 2. Hermes data volume snapshot (config, ledger, sessions, memory) ────────
echo "[backup] tarring hermes-data → $DATA_FILE"
docker run --rm \
    -v zeus-hermes-data:/data:ro \
    -v "$BACKUP_DIR:/backup" \
    alpine:3 \
    tar -czf "/backup/$(basename "$DATA_FILE")" -C /data .

# ── 3. Optional offsite copy to Backblaze B2 ─────────────────────────────────
if [ -n "${B2_ACCOUNT_ID:-}" ] && [ -n "${B2_APPLICATION_KEY:-}" ] && [ -n "${B2_BUCKET:-}" ]; then
    echo "[backup] pushing to B2 bucket: $B2_BUCKET"
    docker run --rm \
        -v "$BACKUP_DIR:/backup:ro" \
        -e RCLONE_CONFIG_B2_TYPE=b2 \
        -e RCLONE_CONFIG_B2_ACCOUNT="$B2_ACCOUNT_ID" \
        -e RCLONE_CONFIG_B2_KEY="$B2_APPLICATION_KEY" \
        rclone/rclone:latest \
        copy "/backup/pg_${DATE}.sql.gz" "b2:${B2_BUCKET}/postgres/" --no-traverse
    docker run --rm \
        -v "$BACKUP_DIR:/backup:ro" \
        -e RCLONE_CONFIG_B2_TYPE=b2 \
        -e RCLONE_CONFIG_B2_ACCOUNT="$B2_ACCOUNT_ID" \
        -e RCLONE_CONFIG_B2_KEY="$B2_APPLICATION_KEY" \
        rclone/rclone:latest \
        copy "/backup/data_${DATE}.tar.gz" "b2:${B2_BUCKET}/data/" --no-traverse
else
    echo "[backup] B2 vars not set — skipping offsite push"
fi

# ── 4. Prune local backups older than RETENTION_DAYS ─────────────────────────
echo "[backup] pruning local files older than ${RETENTION_DAYS}d"
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "[backup] done — sizes:"
ls -lh "$PG_FILE" "$DATA_FILE"
