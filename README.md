# ⚡ Zeus Framework

A fully local, memory-persistent AI agent framework built on [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Zeus adds soul — a 4-layer memory architecture, vector memory plugin, distributed execution, and **automated content creation pipelines** that run entirely on your hardware.

## What's Inside

```
zeus-framework/
├── core/                  # Hermes Agent source (the engine)
├── stack/                 # HermesStack — Redis + pgvector interface
├── plugins/
│   └── mnemosyne/        # L3 vector memory plugin (Redis + pgvector)
├── skills/               # 73+ procedural skills (L4 memory)
├── soul/                 # SOUL.md — identity, memory architecture, principles
├── config/               # Config templates (sanitized, no secrets)
├── memory/               # Memory templates and schemas
├── scripts/              # Start scripts, setup helpers
├── setup/                # pgvector setup, deployment guides
└── content_automation/   # 🆕 Multi-Platform Content Generation System
    ├── pipelines/        # Article, carousel, video, avatar pipelines
    ├── orchestrator.py   # AI content planner & scheduler
    ├── enhanced_content_ideas.py  # Google Workspace + market crawling
    ├── docker-compose.yml  # Full Docker deployment
    └── monitor.py        # Real-time dashboard & cost tracking
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

# Content automation system
cd content_automation/
./docker-zeus.sh up  # Start full content pipeline
```

## 🎬 Content Automation System

**Generate professional financial content at scale - $227/month vs $5000+ competitors spend**

### **5 Content Types, Full Automation:**
- **📄 Articles** ($0.22 each) → SEO-optimized + hero images + publishing
- **🎠 Carousels** ($0.71 each) → Data visualization + multi-slide content  
- **🎬 Videos** ($0.49 each) → Short-form + voiceover + thumbnails
- **🧑‍💼 Avatar Videos** ($0.137 each) → Professional presenter + backgrounds *(60 min/month FREE)*
- **🚨 Alerts** ($0.06 each) → Breaking news + congressional trades + market moves

### **3 Content Discovery Sources:**
1. **📂 File System Drops** → Screenshots, links, notes analyzed daily
2. **📊 Google Sheets Integration** → Live collaboration + structured input  
3. **🌐 Automated Market Crawling** → 8 markets, congressional trades, RSS feeds

### **Cost-Optimized Media Stack:**
- **🤖 LLM:** DeepSeek V4 via OpenRouter ($3.50/month)
- **🎨 Images:** fal.ai Flux Pro/Schnell + Ideogram 2.0 ($65/month)
- **🎙️ Voice:** Fish Audio API ($0.75/month) - *98% cheaper than ElevenLabs*
- **🧑‍💼 Avatars:** Vidnoz FREE tier (60 min/month) - *$0 vs $24/month HeyGen*
- **📱 Publishing:** Publer API to all platforms ($0/month)

**Result:** Professional content creation at **$2.73/piece** vs competitors' **$25+/piece** - 86% cost advantage

### **Quick Start - Content System:**
```bash
cd content_automation/

# 1. Setup environment
cp .env.example .env
# Edit .env with your API keys

# 2. Setup Google Workspace integration (optional)
./setup_google_workspace.sh

# 3. Setup content ideas folders
./setup_content_ideas.sh

# 4. Start the full pipeline
./docker-zeus.sh up

# 5. Monitor at http://localhost:8080/monitor
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
