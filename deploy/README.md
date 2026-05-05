# Zeus on Hetzner — deployment runbook

Bring up the full Zeus stack (agent + Redis + Postgres/pgvector + Caddy + status dashboard) on a fresh Hetzner VM with one bootstrap script. Auto-TLS, daily backups, health endpoint, remote trigger.

## Target box

- **Hetzner CX22** (€4.51/mo, 2 vCPU, 4 GB RAM, 40 GB NVMe, Frankfurt or Ashburn)
- Image: **Ubuntu 24.04**
- Add your SSH key during creation

## DNS

Point `zeus.yourdomain.com` (or whatever you want) at the Hetzner IPv4. Caddy provisions the Let's Encrypt cert automatically once DNS resolves.

## Bootstrap

SSH in as root, then:

```bash
curl -fsSL https://raw.githubusercontent.com/Aristotlev/ZEUS-FRAMEWORK/main/deploy/bootstrap.sh -o /tmp/bootstrap.sh
bash /tmp/bootstrap.sh
```

First run clones the repo to `/opt/zeus` and creates `deploy/.env.prod` from the template, then exits. Edit it:

```bash
nano /opt/zeus/deploy/.env.prod
```

Fill in at minimum:

| Key | Where to get it |
|---|---|
| `ZEUS_DOMAIN` | the hostname you set up |
| `ACME_EMAIL` | your email (Let's Encrypt registration) |
| `ZEUS_TRIGGER_TOKEN` | run `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | random string, your choice |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| `FAL_KEY` | https://fal.ai/dashboard/keys |
| `FISH_AUDIO_API_KEY` | https://fish.audio |
| `NOTION_API_KEY` | https://www.notion.so/my-integrations |
| `PUBLER_API_KEY` + per-platform IDs | Publer dashboard |
| One email backend (`RESEND_API_KEY` recommended) | https://resend.com |
| `B2_*` (optional) | https://www.backblaze.com/b2 — leave empty for local-only backups |

Then re-run bootstrap:

```bash
bash /tmp/bootstrap.sh
```

It installs the systemd units, builds images, and starts the stack. ~5–10 min on first run (Playwright install is the long part).

## Verify

```bash
systemctl status zeus
docker compose -f /opt/zeus/docker-compose.yml -f /opt/zeus/deploy/docker-compose.prod.yml ps
curl -s https://zeus.yourdomain.com/health
```

The dashboard lives at `https://zeus.yourdomain.com`. JSON status at `/status`.

## Register the cron jobs (one-time, after first boot)

Cron is registered inside the agent container via Hermes' built-in scheduler. Once the stack is up:

```bash
docker exec -it zeus-agent python3 /opt/zeus/scripts/setup_content_cron.py
```

This registers the four jobs (article slot, carousel 00:30/12:30, daily crawl, Notion ideas).

The prod entrypoint runs `hermes gateway run` in the foreground — the gateway includes a built-in cron-ticker thread that polls every 60s and fires due jobs, so registered jobs will execute as long as the container is up.

Verify the gateway and jobs:

```bash
docker exec -it zeus-agent hermes cron status   # ✓ Gateway is running + N active job(s)
docker exec -it zeus-agent hermes cron list     # see schedules + next_run_at
docker exec -it zeus-agent hermes cron logs zeus-content-carousel-slot  # tail a run
```

## Remote trigger (from your phone, browser, anywhere)

Explicit topic:

```bash
curl -X POST -H "Authorization: Bearer $ZEUS_TRIGGER_TOKEN" \
  "https://zeus.yourdomain.com/trigger/article?topic=Bitcoin%20breaks%20100K"
```

Niche-pick (uses `content_pipeline.niche` from `config.yaml`):

```bash
curl -X POST -H "Authorization: Bearer $ZEUS_TRIGGER_TOKEN" \
  "https://zeus.yourdomain.com/trigger/carousel?auto=1"
```

Add `&publish=0` to archive-only (no Publer post). Valid types: `article`, `long_article`, `carousel`, `short_video`, `long_video`.

## Webhooks

Generic landing pad at `/webhook/<source>` — auth-gated, payloads logged to `/opt/data/webhooks/<source>.jsonl` inside the container. Use for Publer callbacks, Notion automation hooks, etc.

```bash
curl -X POST -H "Authorization: Bearer $ZEUS_TRIGGER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event":"post_live","url":"..."}' \
  "https://zeus.yourdomain.com/webhook/publer"
```

## Backups

`zeus-backup.timer` fires daily at 03:30 UTC (with up to 30min jitter). It:
1. `pg_dump` of the Postgres DB → `/var/backups/zeus/pg_<date>.sql.gz`
2. tar of the hermes-data volume (config, ledger, sessions, memory) → `/var/backups/zeus/data_<date>.tar.gz`
3. If `B2_*` env vars are set, rclone-pushes both files to your B2 bucket
4. Prunes local files older than 14 days

Manual:

```bash
systemctl start zeus-backup        # run now
journalctl -u zeus-backup -n 100   # see what happened
ls -lh /var/backups/zeus/          # verify
```

Restore (Postgres):

```bash
gunzip -c /var/backups/zeus/pg_<date>.sql.gz \
  | docker exec -i zeus-postgres psql -U hermes -d hermes_vectors
```

Restore (data volume):

```bash
docker run --rm -v zeus-hermes-data:/data -v /var/backups/zeus:/b alpine \
  tar -xzf /b/data_<date>.tar.gz -C /data
```

## Day-2 ops

```bash
systemctl restart zeus           # bounce the whole stack
systemctl stop zeus              # stop everything
docker compose -f /opt/zeus/docker-compose.yml -f /opt/zeus/deploy/docker-compose.prod.yml logs -f zeus
docker exec -it zeus-agent bash  # shell into the agent
```

### Updating

```bash
cd /opt/zeus
git pull
docker compose --env-file deploy/.env.prod \
  -f docker-compose.yml -f deploy/docker-compose.prod.yml build
systemctl restart zeus
```

## Architecture (what the bootstrap actually deploys)

```
                  ┌─────────────────────────────────────────┐
   internet ──▶   │  Caddy :443  (auto-TLS via LE)          │
                  └────────────────┬────────────────────────┘
                                   │
                                   ▼
                  ┌─────────────────────────────────────────┐
                  │  status :8000  (FastAPI dashboard)      │
                  │  • / dashboard                          │
                  │  • /health  (heartbeat probe)           │
                  │  • /status  (JSON cost + queue)         │
                  │  • /trigger/{type}  (Bearer auth)       │
                  │  • /webhook/{source}  (Bearer auth)     │
                  └────────────┬────────┬───────────────────┘
                               │        │ docker exec
                               ▼        ▼
            ┌──────────────────────────────────────┐
            │  zeus-agent  (Hermes + cron)         │
            │   /opt/data ↔ shared volume          │
            └──────┬─────────────────────┬─────────┘
                   │                     │
                   ▼                     ▼
            ┌──────────────┐     ┌─────────────────┐
            │  redis :6379 │     │  postgres :5432 │
            │  (L1 cache)  │     │  (pgvector L3)  │
            └──────────────┘     └─────────────────┘

  systemd:  zeus.service (stack)   zeus-backup.timer (daily 03:30 UTC)
  fw:       ufw allow 22, 80, 443
```

## Cost breakdown (monthly)

| Item | Cost |
|---|---|
| Hetzner CX22 | €4.51 |
| Domain (.com via Cloudflare/Namecheap) | ~$0.83 |
| Backblaze B2 (≈10 GB) | ~$0.06 |
| **Infra total** | **≈ €5.50 / $6** |
| OpenRouter / fal / fish.audio | usage-based, see `/status` cost rollups |

## Fallback: host cron

If you'd rather drive scheduling from systemd than from the in-container gateway (e.g. the gateway crashed and you want belt-and-braces), use host-side timers. `pipeline_test.py` accepts `--auto`, which picks a topic from `content_pipeline.niche` in `~/.hermes/config.yaml` via a cheap LLM call (~$0.0001) — no `--topic` needed.

```ini
# /etc/systemd/system/zeus-carousel.service
[Unit]
Description=Zeus carousel slot

[Service]
Type=oneshot
ExecStart=/usr/bin/docker exec zeus-agent python3 \
  /opt/zeus/skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/pipeline_test.py \
  --type carousel --auto --publish

# /etc/systemd/system/zeus-carousel.timer
[Unit]
Description=Zeus carousel — 00:30 and 12:30 daily

[Timer]
OnCalendar=*-*-* 00:30:00
OnCalendar=*-*-* 12:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

If you go this route, also remove the equivalent in-app job to avoid double-posting:

```bash
docker exec -it zeus-agent hermes cron remove zeus-content-carousel-slot
```

## Files in this directory

```
deploy/
├── README.md                       this file
├── bootstrap.sh                    one-shot Hetzner setup
├── docker-compose.prod.yml         production overrides
├── .env.prod.example               env template (copy → .env.prod, fill in)
├── Caddyfile                       reverse proxy + auto-TLS
├── zeus-prod-entrypoint.sh         bootstraps then heartbeat-loops
├── status/
│   ├── Dockerfile
│   ├── app.py                      FastAPI: /, /health, /status, /trigger, /webhook
│   └── requirements.txt
├── systemd/
│   ├── zeus.service                stack up/down via systemctl
│   ├── zeus-backup.service         backup oneshot
│   └── zeus-backup.timer           daily 03:30 UTC
└── backup/
    └── backup.sh                   pg_dump + tar + optional rclone→B2
```
