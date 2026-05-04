# ⚡ Zeus Framework — Installation Guide

> **One script. Ubuntu + Hermes Agent + OpenRouter + pgvector memory. Done.**

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Ubuntu 22.04 / 24.04 | Tested. Other Debian-based distros should work. |
| `sudo` access | Needed for apt packages (Python, Redis, PostgreSQL) |
| [OpenRouter API key](https://openrouter.ai/keys) | Free tier available — no credit card needed to start |
| Git | `sudo apt install git` |

---

## Option A — Fresh Install (recommended)

Everything from scratch in one command:

```bash
# 1. Clone Zeus
git clone https://github.com/Aristotlev/ZEUS-FRAMEWORK.git zeus
cd zeus

# 2. Run the installer
chmod +x install.sh
./install.sh
```

The installer will:
1. Install system packages (`python3.11`, `redis`, `postgresql-16`, `pgvector`, `ripgrep`)
2. Set up the Hermes Agent core via `uv`
3. Prompt you for your **OpenRouter API key** and write `~/.hermes/.env`
4. Create a user-owned PostgreSQL cluster on port `5433` with pgvector tables
5. Install the **Mnemosyne** L3 vector memory plugin
6. Activate the **Zeus soul persona** (`~/.hermes/persona.md`)
7. Sync all 98+ skills to `~/.hermes/skills/`

When done, reload your shell and start:

```bash
source ~/.bashrc      # or source ~/.zshrc
hermes doctor         # verify everything is healthy
hermes                # start Zeus
```

---

## Option B — Upgrade Existing Hermes Agent to Zeus

Already running Hermes? One command upgrades your agent to the full Zeus stack:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Aristotlev/ZEUS-FRAMEWORK/main/scripts/zeus-upgrade.sh)
```

Or if you already cloned the repo:

```bash
cd zeus
bash scripts/zeus-upgrade.sh
```

This will **not** overwrite your existing config or API keys — it only adds:
- Zeus skills (merged into `~/.hermes/skills/`)
- Mnemosyne plugin (`~/.hermes/plugins/mnemosyne/`)
- Zeus soul persona (`~/.hermes/persona.md`)
- pgvector database tables (if PostgreSQL is available)

---

## Environment Variables

The installer creates `~/.hermes/.env`. Key variables:

```bash
# Required
OPENROUTER_API_KEY=sk-or-...

# PostgreSQL / Mnemosyne (auto-configured by installer)
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=hermes_vectors
POSTGRES_USER=hermes
POSTGRES_PASSWORD=hermes_local

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
```

You can override any variable before running the installer:

```bash
OPENROUTER_API_KEY=sk-or-abc123 ZEUS_MODEL=openai/gpt-4o ./install.sh
```

---

## Choosing a Model

Zeus works with any model available on [OpenRouter](https://openrouter.ai/models).

Popular choices:

| Model | Speed | Cost | Recommended for |
|---|---|---|---|
| `anthropic/claude-sonnet-4` | Fast | ~$3/M | General use (default) |
| `deepseek/deepseek-r1` | Medium | Free tier | Reasoning tasks |
| `google/gemini-2.5-pro` | Fast | Low | Long context |
| `openai/gpt-4o` | Fast | ~$2.5/M | Tool-heavy tasks |

Set your preferred model:

```bash
ZEUS_MODEL=openai/gpt-4o ./install.sh
```

Or edit `~/.hermes/config.yaml` after install:

```yaml
model:
  default: openai/gpt-4o
```

---

## Verify the Installation

```bash
# Full health check
hermes doctor

# Check memory stack (Redis + pgvector)
python3 -c "
import sys; sys.path.insert(0, 'stack')
from stack.hermes_stack import get_stack
s = get_stack()
print(s.health_check())
"
# Expected: {'redis': True, 'postgres': True, 'pgvector': True}
```

---

## What Gets Installed Where

```
~/.hermes/
├── .env                  ← API keys & DB credentials
├── config.yaml           ← Model + agent configuration
├── persona.md            ← Zeus soul (identity + memory principles)
├── memory/
│   ├── MEMORY.md         ← Episodic memory (L2)
│   └── USER.md           ← User profile
├── plugins/
│   └── mnemosyne/        ← L3 vector memory plugin
└── skills/               ← 98+ procedural skills (L4)
    ├── software-development/
    ├── devops/
    ├── research/
    └── ...
```

---

## Troubleshooting

**`hermes: command not found`**
```bash
source ~/.bashrc   # reload PATH
# or
export PATH="$HOME/.local/bin:$PATH"
```

**Redis not running**
```bash
sudo systemctl start redis-server
redis-cli ping   # should return PONG
```

**PostgreSQL cluster not starting**
```bash
pg_ctl -D ~/pgdata -l ~/pgdata/logfile start
# Check logs:
tail -50 ~/pgdata/logfile
```

**pgvector extension missing**
```bash
sudo apt install postgresql-16-pgvector
psql -p 5433 -d hermes_vectors -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**OpenRouter auth error**
```bash
# Edit your .env
nano ~/.hermes/.env
# Set: OPENROUTER_API_KEY=sk-or-your-actual-key
```

---

## Next Steps After Install

```bash
# Run the interactive setup wizard
hermes setup

# Start a gateway (Discord / Telegram / Slack)
hermes gateway install

# View scheduled jobs
hermes cron list

# Check status
hermes status
```

---

## Uninstall

Zeus lives entirely in `~/.hermes/` and `~/pgdata`. To remove:

```bash
# Stop services
pg_ctl -D ~/pgdata stop 2>/dev/null || true
sudo systemctl stop redis-server 2>/dev/null || true

# Remove Zeus data
rm -rf ~/.hermes ~/pgdata

# Remove the hermes CLI symlink
rm -f ~/.local/bin/hermes
```
