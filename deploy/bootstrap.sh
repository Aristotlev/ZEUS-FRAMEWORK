#!/usr/bin/env bash
# ============================================================================
# Zeus Framework — Hetzner one-shot bootstrap
# ============================================================================
# Run on a fresh Ubuntu 24.04 box as root:
#
#   curl -fsSL https://raw.githubusercontent.com/Aristotlev/ZEUS-FRAMEWORK/main/deploy/bootstrap.sh | bash
#
# Or, if cloning manually:
#   git clone https://github.com/Aristotlev/ZEUS-FRAMEWORK.git /opt/zeus
#   bash /opt/zeus/deploy/bootstrap.sh
#
# What it does:
#   1. apt update + install docker, docker-compose-plugin, ufw, git
#   2. Clone Zeus into /opt/zeus (if not already there)
#   3. Open ports 22/80/443, set UTC timezone
#   4. Copy .env.prod.example → .env.prod (if missing) and prompt the user
#   5. Install + enable systemd units (zeus.service, zeus-backup.timer)
#   6. docker compose pull + build, start the stack
# ============================================================================
set -euo pipefail

REPO="${ZEUS_REPO:-https://github.com/Aristotlev/ZEUS-FRAMEWORK.git}"
DEST="${ZEUS_DEST:-/opt/zeus}"
BRANCH="${ZEUS_BRANCH:-main}"

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root" >&2
    exit 1
fi

echo "==> [1/6] apt prerequisites"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git ufw

# ── Docker (official repo) ──────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    UBUNTU_CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $UBUNTU_CODENAME stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
fi

echo "==> [2/6] cloning Zeus → $DEST"
if [ ! -d "$DEST/.git" ]; then
    git clone --branch "$BRANCH" "$REPO" "$DEST"
else
    git -C "$DEST" pull --ff-only
fi

echo "==> [3/6] firewall + timezone"
ufw --force enable
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
timedatectl set-timezone UTC

echo "==> [4/6] env file"
ENV_PROD="$DEST/deploy/.env.prod"
if [ ! -f "$ENV_PROD" ]; then
    cp "$DEST/deploy/.env.prod.example" "$ENV_PROD"
    chmod 600 "$ENV_PROD"
    cat <<MSG

  ⚠  $ENV_PROD has been created from the template.
     Edit it now and fill in your keys before continuing:

       nano $ENV_PROD

     Required at minimum: ZEUS_DOMAIN, ACME_EMAIL, ZEUS_TRIGGER_TOKEN,
     POSTGRES_PASSWORD, OPENROUTER_API_KEY, FAL_KEY, NOTION_API_KEY,
     PUBLER_API_KEY (+ platform IDs).

     Generate ZEUS_TRIGGER_TOKEN with:
       openssl rand -hex 32

     Re-run this script when you're done.

MSG
    exit 0
fi

# Sanity check: required keys present
missing=()
for k in ZEUS_DOMAIN ACME_EMAIL ZEUS_TRIGGER_TOKEN POSTGRES_PASSWORD OPENROUTER_API_KEY; do
    val="$(grep -E "^${k}=" "$ENV_PROD" | head -1 | cut -d= -f2- || true)"
    if [ -z "$val" ] || [[ "$val" == *replace_me* ]] || [[ "$val" == *example.com* ]]; then
        missing+=("$k")
    fi
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "  ⚠  $ENV_PROD has placeholder/empty values for: ${missing[*]}"
    echo "     Fix and re-run."
    exit 1
fi

echo "==> [5/6] systemd units"
install -m 644 "$DEST/deploy/systemd/zeus.service" /etc/systemd/system/zeus.service
install -m 644 "$DEST/deploy/systemd/zeus-backup.service" /etc/systemd/system/zeus-backup.service
install -m 644 "$DEST/deploy/systemd/zeus-backup.timer" /etc/systemd/system/zeus-backup.timer
chmod +x "$DEST/deploy/backup/backup.sh"
mkdir -p /var/backups/zeus
systemctl daemon-reload
systemctl enable zeus.service zeus-backup.timer

echo "==> [6/6] build + start stack"
cd "$DEST"
docker compose --env-file "$ENV_PROD" \
    -f docker-compose.yml -f deploy/docker-compose.prod.yml pull --ignore-pull-failures || true
docker compose --env-file "$ENV_PROD" \
    -f docker-compose.yml -f deploy/docker-compose.prod.yml build
systemctl start zeus.service
systemctl start zeus-backup.timer

cat <<DONE

==============================================================================
  ⚡ Zeus is live.

  Status:        systemctl status zeus
  Logs:          docker compose -f $DEST/docker-compose.yml -f $DEST/deploy/docker-compose.prod.yml logs -f
  Stack down:    systemctl stop zeus
  Backup now:    systemctl start zeus-backup
  Backup logs:   journalctl -u zeus-backup -n 100

  Once DNS for \$ZEUS_DOMAIN points at this box, Caddy will issue a TLS cert
  automatically (~30s). Then visit:

      https://$(grep -E '^ZEUS_DOMAIN=' "$ENV_PROD" | cut -d= -f2)

==============================================================================
DONE
