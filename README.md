# Zeus Framework

A fully local, memory-persistent AI agent framework built on [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Zeus adds soul — a 4-layer memory architecture, vector memory plugin, distributed execution, and **automated content creation pipelines** that run entirely on your hardware.

## What's Inside

```
zeus-framework/
├── core/                   # Hermes Agent source (the engine)
├── stack/                  # HermesStack — Redis + pgvector interface
├── plugins/
│   └── mnemosyne/          # L3 vector memory plugin (Redis + pgvector)
├── skills/                 # 98+ procedural skills (L4 memory)
│   └── autonomous-ai-agents/
│       └── multi-agent-content-pipeline/
│           ├── lib/        # fal.py, fish.py, notion.py, platforms.py, ledger.py, email_notify.py, content_types.py
│           ├── scripts/    # pipeline_test.py — canonical orchestrator
│           └── references/ # Publer API reference, cost analysis
├── soul/                   # SOUL.md — identity, memory architecture, principles
├── config/                 # Config templates (sanitized, no secrets)
├── memory/                 # Memory templates and schemas
├── scripts/                # Start scripts, setup helpers
└── setup/                  # pgvector setup, deployment guides
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

## Content Automation System

Generate professional content across Twitter, Instagram, LinkedIn, TikTok, YouTube, Reddit, and Facebook from a single command. Every run archives to Notion BEFORE any external API spend, downloads media locally, appends to a persistent cost ledger, and emails a summary with post links + always-on cost analysis.

### 4 Content Types (canonical taxonomy, May 2026)

| Type | Media | Platforms | Description |
|------|-------|-----------|-------------|
| **Article** | 1 image (1024x1024 default) + long description | Twitter, IG, LinkedIn, TikTok | 550-900 chars (clears "read more" everywhere) |
| **Carousel** | 3-5 slide images + long description | Twitter, IG, LinkedIn, TikTok | 550-900 chars |
| **Short-form Video** | 1080x1920, <90s | Twitter, IG (reel), LinkedIn, TikTok, YouTube Shorts | Mobile-native portrait |
| **Long-form Video** | 1920x1080 | YouTube, Twitter, LinkedIn, Reddit | Landscape, 16:9 |

A single `ContentPiece` dataclass (`skills/autonomous-ai-agents/multi-agent-content-pipeline/lib/content_types.py`) flows through every stage: text gen → variants → media → archive → publish → ledger → email.

### Cost-Optimized Media Stack (May 2026)

- **LLM:** OpenRouter `google/gemini-2.5-flash` (~$0.001/post)
- **Images:** fal.ai `openai/gpt-image-2` (~$0.04 medium / $0.16 high quality at 1920x1080)
- **Video:** fal.ai `kling-video/v2.5-turbo/pro` ($0.35 first 5s + $0.07/s — 1080p in either orientation)
- **TTS:** **fish.audio** S1 — `https://api.fish.audio/v1/tts` (~$15/1M chars). User mandate: TTS only via fish.audio.
- **Music:** fal.ai `cassetteai/music-generator` (~$0.05/clip, swappable)
- **Publishing:** Publer API (single key, all platforms)
- **Archive:** Notion (Omnifolio Content Hub auto-discovered)
- **Notifications:** Resend / AgentMail / Gmail SMTP (auto-pick by configured env)
- **Cost ledger:** `~/.hermes/zeus_cost_ledger.jsonl` — every run, every model, every dollar

**Stack notes:**
- **Replicate is dead.** Removed entirely after May 2026 — burned $15 on generations that were never archived. All media now flows through `lib/fal.py` with mandatory local download + Notion archive before any publish step.
- **Notion archive** auto-discovers the archive database under the Omnifolio Content Hub page on first run; cached at `~/.hermes/notion_ids.json`.
- **Email summaries** sent after every run with social media post links + 24h/7d/30d/all-time cost rollups.

### Quick Start — Content System

```bash
# 1. Set required keys in ~/.hermes/.env:
#    OPENROUTER_API_KEY, FAL_KEY, NOTION_API_KEY, PUBLER_API_KEY, FISH_AUDIO_API_KEY
#    (optional) RESEND_API_KEY or AGENTMAIL_API_KEY for the post-run email
#    (optional) ZEUS_NOTIFY_EMAIL — defaults to ariscsc@gmail.com

# 2. Install Python deps
pip install fal-client requests

# 3. Generate + archive (no posting yet — safe mode)
cd skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts
export $(grep -v '^#' ~/.hermes/.env | xargs)
python3 pipeline_test.py --type article --topic "Bitcoin breaks 100K"

# 4. When ready, post to Publer
python3 pipeline_test.py --type article --topic "..." --publish
```

Every run: archives to Notion BEFORE any external API spend, downloads media locally, appends to `~/.hermes/zeus_cost_ledger.jsonl`, and sends an email summary.

## Skills (L4 Memory)

98+ skills across domains:

- **autonomous-ai-agents** — Claude Code, Codex, OpenCode, subagent delegation, multi-agent content pipeline
- **creative** — ASCII art, diagrams, infographics, Excalidraw, pixel art
- **data-science** — Jupyter live kernel
- **devops** — Remote access, pgvector setup, webhooks, Wake-on-LAN system
- **email** — Himalaya IMAP/SMTP, multi-backend email (AgentMail, Gmail, Proton, Resend/SendGrid)
- **gaming** — Minecraft modpack servers, Pokemon
- **github** — Auth, code review, issues, PRs, repo management
- **mcp** — Model Context Protocol client
- **media** — YouTube, GIFs, music generation, spectrograms
- **mlops** — HuggingFace, evaluation, inference, training, research
- **productivity** — Google Workspace, Notion, PDFs, PowerPoint
- **red-teaming** — LLM jailbreak techniques
- **research** — arXiv, blog monitoring, prediction markets, competitive analysis
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

All data stays on your machine. No cloud dependencies for storage. Skills and knowledge grow with use. Circuit breakers and fallback models ensure graceful degradation. Every layer can be swapped or extended.

## License

Hermes Agent core: See core/LICENSE
Zeus additions (stack, plugins, soul, skills, config): MIT

## Credits

Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research.
