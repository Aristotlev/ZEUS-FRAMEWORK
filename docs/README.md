# Zeus Framework — Documentation

| Guide | What it covers |
|---|---|
| [installation.md](installation.md) | One-script install on Ubuntu / WSL2 / macOS, environment vars, upgrade path |
| [architecture.md](architecture.md) | Top-level layout: `core/`, `stack/`, `plugins/`, `skills/`, `openclaw/` |
| [memory.md](memory.md) | The 4-layer memory model (in-context → episodic → semantic → procedural) |
| [content-pipeline.md](content-pipeline.md) | The 5-type content automation system + cost analysis |
| [cron.md](cron.md) | Three idempotent content cron jobs and how to configure your niche |
| [skills.md](skills.md) | The 98+ procedural skills shipped with Zeus, by domain |
| [pgvector.md](pgvector.md) | User-owned PostgreSQL 16 + pgvector setup (no sudo) |
| [openclaw.md](openclaw.md) | Distributed compute on Oracle ARM nodes |
| [deployment.md](deployment.md) | Single-VM Hetzner deploy with Caddy, Postgres, daily backups |

## Where things live in the repo

```
zeus-framework/
├── core/                    # Hermes Agent (vendored upstream)
├── stack/                   # Redis + pgvector glue
├── plugins/mnemosyne/       # L3 vector memory plugin
├── skills/                  # L4 procedural memory (98+ skills)
├── openclaw/                # Distributed compute engine
├── deploy/                  # Hetzner production deploy
├── docker/                  # Local Docker Compose
├── docs/                    # ← you are here
├── memory/                  # Memory templates + schemas
├── config/                  # Config templates
└── scripts/                 # Setup, cron, upgrade helpers
```
