<p align="center">
  <img src="assets/banner.svg" alt="Zeus Framework" width="100%"/>
</p>

<h1 align="center">Zeus Framework <sup>⚡</sup></h1>

<p align="center">
  <a href="docs/README.md"><img alt="Docs" src="https://img.shields.io/badge/docs-zeus--framework-1f6feb?logo=readthedocs&logoColor=white"/></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-fbbf24"/></a>
  <a href="https://github.com/NousResearch/hermes-agent"><img alt="Built on Hermes Agent" src="https://img.shields.io/badge/built%20on-Hermes%20Agent-7c3aed"/></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white"/>
  <a href="https://github.com/Aristotlev/ZEUS-FRAMEWORK/issues"><img alt="Issues" src="https://img.shields.io/github/issues/Aristotlev/ZEUS-FRAMEWORK?color=ef4444"/></a>
</p>

<p align="center">
  <b>Local-first, memory-persistent AI agent framework with built-in content automation.</b><br/>
  A Soul + 4-layer memory + content pipeline assembled on top of <a href="https://github.com/NousResearch/hermes-agent">Hermes Agent</a> by <a href="https://nousresearch.com">Nous Research</a>.
</p>

---

Zeus is what you get when you take Hermes Agent and bolt on the things you'd otherwise have to wire up yourself: a vector-memory plugin, distributed compute on cheap ARM nodes, an opinionated content-creation pipeline, and a Soul file that gives the agent a stable identity across sessions. Everything runs on your hardware. Memory persists across restarts. Skills compound with use.

Multi-provider model support is inherited from Hermes (OpenRouter, Anthropic, OpenAI, DeepSeek, Mistral, Vercel, vLLM, llama.cpp, Ollama, HuggingFace, …) — switch models mid-session, route by task, mix paid + local.

## Features

| | |
|---|---|
| **🖥️  Terminal-native** | The agent runs in your shell. No browser, no cloud panel. CLI, TUI, or messaging-gateway. |
| **🧠  4-layer memory** | In-context → episodic → **semantic (pgvector)** → procedural (skills). Every layer swappable. |
| **🎬  Content automation** | One command turns a topic into Article / LongArticle / Carousel / ShortVideo / LongVideo across 7 platforms — fal.ai images, Kling video, fish.audio TTS, Notion archive, Publer distribution. |
| **⏰  Idempotent crons** | Three shipped jobs research + draft + publish on schedule. Niche-agnostic. Re-run setup to update; never duplicates. |
| **☁️  Distributed compute** | OpenClaw runs heavy workloads on Oracle ARM (Ampere A1) free-tier instances. Zeus delegates and returns. |
| **🏗️  Production-ready** | Single-script Hetzner deploy with auto-TLS (Caddy), daily backups, status dashboard, remote trigger endpoint. |
| **🛡️  Honest cost tracking** | Every run, every model, every dollar appended to a JSONL ledger. Email summaries with 24h / 7d / 30d / all-time rollups. No surprises. |

## Quick Install

> **One script.** Ubuntu / WSL2 / macOS. Installs Python, Redis, PostgreSQL 16, pgvector, Hermes Agent, and the Zeus stack.

```bash
git clone https://github.com/Aristotlev/ZEUS-FRAMEWORK.git zeus
cd zeus
chmod +x install.sh
./install.sh
```

The installer prompts for an [OpenRouter API key](https://openrouter.ai/keys) (free tier available, no card needed) and writes `~/.hermes/.env`.

After install:

```bash
source ~/.bashrc       # or ~/.zshrc
zeus doctor            # verify everything is healthy
zeus                   # start the agent
```

> The legacy `hermes` command still works — same binary.

### Already running Hermes? Upgrade in one command:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Aristotlev/ZEUS-FRAMEWORK/main/scripts/zeus-upgrade.sh)
```

→ Full guide: **[docs/installation.md](docs/installation.md)**

## Commands

| Command | What it does |
|---|---|
| `zeus` | Start an interactive session (default) |
| `zeus doctor` | Health-check every layer (Redis, Postgres, pgvector, Hermes, OpenRouter) |
| `zeus gateway install` | Install a Discord / Telegram / Slack / WhatsApp / Signal gateway |
| `zeus cron start` | Run the cron daemon (foreground) |
| `zeus cron daemon` | Run the cron daemon (background) |
| `zeus cron list` | List installed cron jobs |
| `python scripts/setup_content_cron.py` | Install the three content cron jobs for your niche |
| `python skills/.../scripts/pipeline_test.py --type <T> --topic "<...>"` | Run the content pipeline manually for a single piece |

## Documentation

| Guide | What it covers |
|---|---|
| [Installation](docs/installation.md) | One-script install, env vars, upgrade path |
| [Architecture](docs/architecture.md) | Top-level layout: `core/`, `stack/`, `plugins/`, `skills/`, `openclaw/` |
| [Memory model](docs/memory.md) | The 4 memory layers, swapping any of them |
| [Content pipeline](docs/content-pipeline.md) | The 5-type content automation system + cost analysis |
| [Cron](docs/cron.md) | Three idempotent content cron jobs and how to configure your niche |
| [Skills](docs/skills.md) | The 98+ procedural skills shipped with Zeus, by domain |
| [pgvector setup](docs/pgvector.md) | User-owned PostgreSQL 16 + pgvector setup (no sudo) |
| [OpenClaw](docs/openclaw.md) | Distributed compute on Oracle ARM nodes |
| [Deployment](docs/deployment.md) | Single-VM Hetzner deploy with Caddy, Postgres, daily backups |
| [Contributing](CONTRIBUTING.md) | Where each kind of change lives + PR checklist |
| [Security](SECURITY.md) | Reporting vulnerabilities, scope, what's in/out |

## Memory Architecture

| Layer | Name | Storage | Purpose |
|---|---|---|---|
| **L1** | In-context | Model context window | Working memory, current task |
| **L2** | Episodic | Hermes session search + memory files | What happened, past decisions |
| **L3** | Semantic | PostgreSQL + pgvector (Mnemosyne plugin) | What it knows, semantic recall |
| **L4** | Procedural | Skill `SKILL.md` files | How to do things |

Detailed: [docs/memory.md](docs/memory.md).

## Content Automation at a glance

Generate professional content across **Twitter, Instagram, LinkedIn, TikTok, YouTube, Reddit, Facebook** from a single command. Every run archives to Notion **before** any external API spend, downloads media locally, appends to a persistent cost ledger, and emails a summary with post links + always-on cost rollups.

| Type | Media | Targets |
|---|---|---|
| **Article** | 1 image (1024×1024) | Twitter, IG, LinkedIn, TikTok |
| **LongArticle** | 1 image + thread | Twitter (thread), IG, LinkedIn, TikTok |
| **Carousel** | 3–5 portrait slides | Twitter, IG, LinkedIn, TikTok |
| **ShortVideo** | 1080×1920, <90s | Twitter, IG (reel), LinkedIn, TikTok, YouTube Shorts |
| **LongVideo** | 1920×1080 | YouTube, Twitter, LinkedIn, Reddit |

Stack: OpenRouter (text) · fal.ai GPT-Image-2 (images) · fal.ai Kling Turbo Pro (video) · fish.audio (TTS) · cassetteai/music-generator (music) · Publer (distribution) · Notion (archive).

Detailed: [docs/content-pipeline.md](docs/content-pipeline.md).

## Skills

98+ skills across `apple/`, `autonomous-ai-agents/`, `creative/`, `data-science/`, `devops/`, `email/`, `gaming/`, `github/`, `mcp/`, `media/`, `mlops/`, `productivity/`, `red-teaming/`, `research/`, `smart-home/`, `social-media/`, `software-development/`.

Browse all: [docs/skills.md](docs/skills.md).

## Stack

- **Redis** — Task queue + L1 cache (hot paths)
- **PostgreSQL + pgvector** — L3 semantic memory (1536-dim embeddings)
- **Mnemosyne** — Memory plugin: circuit breaker, auto-mirror, session summarization
- **OpenClaw** — Distributed execution on Oracle ARM free-tier nodes
- **Hermes Agent** — Core engine with 50+ built-in tools

Detailed: [docs/architecture.md](docs/architecture.md).

## Philosophy

> Local-first. Evolving. Resilient. Modular. Honest.

All data stays on your machine. No cloud dependencies for storage. Skills and knowledge grow with use. Circuit breakers and fallback models ensure graceful degradation. Every layer can be swapped or extended.

## Contributing

PRs welcome. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for where each kind of change lives, the PR checklist, and the no-secrets rule.

Security issues: please use [GitHub's private vulnerability reporting](https://github.com/Aristotlev/ZEUS-FRAMEWORK/security/advisories/new). See [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE). The `core/` directory is vendored from [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) under its own license; see `core/LICENSE`.

## Credits

Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) by [Nous Research](https://nousresearch.com).
Zeus Framework assembled and maintained by [Aristotlev](https://github.com/Aristotlev).
