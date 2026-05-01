# ⚡ Zeus Framework

A fully local, memory-persistent AI agent framework built on [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Zeus adds soul — a 4-layer memory architecture, vector memory plugin, distributed execution, and a battle-tested stack that runs entirely on your hardware.

## What's Inside

```
zeus-framework/
├── core/               # Hermes Agent source (the engine)
├── stack/              # HermesStack — Redis + pgvector interface
├── plugins/
│   └── mnemosyne/      # L3 vector memory plugin (Redis + pgvector)
├── skills/             # 73+ procedural skills (L4 memory)
├── soul/               # SOUL.md — identity, memory architecture, principles
├── config/             # Config templates (sanitized, no secrets)
├── memory/             # Memory templates and schemas
├── scripts/            # Start scripts, setup helpers
└── setup/              # pgvector setup, deployment guides
```

## Memory Architecture

| Layer | Name | Storage | Purpose |
|-------|------|---------|---------|
| L1 | In-context | Context window | Working memory, current task |
| L2 | Episodic | Session search + memory files | What happened, past decisions |
| L3 | Semantic | pgvector + Redis | What it knows, semantic recall |
| L4 | Procedural | Skills system (SKILL.md files) | How to do things |

## Quick Start

### Prerequisites
- Python 3.11+
- Redis (`sudo apt install redis-server`)
- PostgreSQL 16 with pgvector extension
- An OpenRouter API key (or compatible LLM endpoint)

### 1. Set up PostgreSQL + pgvector (no sudo needed)
```bash
cd setup/
# Follow pgvector-no-sudo-setup skill instructions
```

### 2. Configure
```bash
cp config/.env.example ~/.hermes/.env
# Edit ~/.hermes/.env with your API keys

cp config/config.example.yaml ~/.hermes/config.yaml
# Edit ~/.hermes/config.yaml with your model preferences
```

### 3. Install Mnemosyne plugin
```bash
cp -r plugins/mnemosyne ~/.hermes/plugins/
```

### 4. Install the core
```bash
cd core/
pip install -e .
```

### 5. Run
```bash
hermes  # CLI mode
hermes gateway  # Discord/Telegram/Slack gateway
```

## Skills (L4 Memory)

73+ skills across domains:

- **autonomous-ai-agents** — Claude Code, Codex, OpenCode, subagent delegation
- **creative** — ASCII art, diagrams, infographics, Excalidraw, pixel art
- **data-science** — Jupyter live kernel
- **devops** — Remote access, pgvector setup, webhooks
- **email** — Himalaya IMAP/SMTP
- **gaming** — Minecraft modpack servers, Pokemon
- **github** — Auth, code review, issues, PRs, repo management
- **mcp** — Model Context Protocol client
- **media** — YouTube, GIFs, music generation, spectrograms
- **mlops** — HuggingFace, evaluation, inference, training, research
- **productivity** — Google Workspace, Notion, PDFs, PowerPoint
- **red-teaming** — LLM jailbreak techniques
- **research** — arXiv, blog monitoring, prediction markets
- **smart-home** — Philips Hue
- **social-media** — X/Twitter
- **software-development** — Planning, TDD, debugging, code review

## The Stack

- **Redis** — Task queue + L1 cache (hot paths)
- **PostgreSQL + pgvector** — L3 semantic memory (1536-dim embeddings)
- **Mnemosyne** — Memory plugin with circuit breaker, auto-mirror, session summarization
- **OpenClaw** — Distributed execution on Oracle ARM nodes
- **Hermes Agent** — Core engine with 50+ built-in tools

## Philosophy

> Local-first. Evolving. Resilient. Modular. Honest.

All data stays on your machine. No cloud dependencies for storage.
Skills and knowledge grow with use. Circuit breakers and fallback models
ensure graceful degradation. Every layer can be swapped or extended.

## License

Hermes Agent core: See core/LICENSE
Zeus additions (stack, plugins, soul, skills, config): MIT

## Credits

Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research.
Zeus Framework assembled and configured by [ariscsc](https://github.com/ariscsc).
