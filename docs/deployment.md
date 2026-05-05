# Deployment

Two supported deploy modes:

| Mode | Where | Use when |
|---|---|---|
| **Local Docker** | `docker-compose.yml` at repo root | Dev or local 24/7 on your own box |
| **Hetzner production** | `deploy/` directory | Single-VM production with auto-TLS, daily backups, status dashboard |

## Local Docker

```bash
cp .env.docker.example .env
# fill in OPENROUTER_API_KEY at minimum
docker compose up -d
```

This brings up Zeus + Redis + Postgres-with-pgvector locally. The agent listens on stdin via `docker compose exec zeus zeus`.

## Hetzner production

Spin up a fresh **CX22** VM (Ubuntu 24.04, ~€4.51/mo), point DNS at it, then SSH in and:

```bash
curl -fsSL https://raw.githubusercontent.com/Aristotlev/ZEUS-FRAMEWORK/main/deploy/bootstrap.sh -o /tmp/bootstrap.sh
bash /tmp/bootstrap.sh
```

The full runbook with all required env vars is in [deploy/README.md](../deploy/README.md). Highlights:

- **Caddy** auto-provisions a Let's Encrypt cert once DNS resolves
- **Status dashboard** at `https://your-domain/` (FastAPI app in `deploy/status/`)
- **Remote trigger** at `POST /trigger` with bearer auth — kick off content pipeline runs from anywhere
- **Daily backups** of Postgres + the cost ledger via systemd timer (see `deploy/systemd/`)
- **Optional offsite backups** to Backblaze B2 if `B2_*` env vars are set

## What gets persisted

| Path | What |
|---|---|
| `~/.hermes/` (or `/var/lib/zeus/.hermes/` in prod) | agent home — config, persona, memory, skills, ledger |
| Postgres `hermes_vectors` DB | L3 semantic memory |
| Redis | L3 hot path + task queue |
| `~/.hermes/zeus_cost_ledger.jsonl` | per-run cost ledger (also emailed in run summaries) |
| `~/.hermes/notion_ids.json` | cached resolved Notion archive DB id (one-shot lookup) |
