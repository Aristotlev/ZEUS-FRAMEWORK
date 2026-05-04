     1|# ⚡ Zeus Framework
     2|
     3|A fully local, memory-persistent AI agent framework built on [Hermes Agent](https://github.com/NousResearch/hermes-agent).
     4|
     5|Zeus adds soul — a 4-layer memory architecture, vector memory plugin, distributed execution, and **automated content creation pipelines** that run entirely on your hardware.
     6|
     7|## What's Inside
     8|
     9|```
    10|zeus-framework/
    11|├── core/                  # Hermes Agent source (the engine)
    12|├── stack/                 # HermesStack — Redis + pgvector interface
    13|├── plugins/
    14|│   └── mnemosyne/        # L3 vector memory plugin (Redis + pgvector)
    15|├── skills/               # 98+ procedural skills (L4 memory)
    16|├── soul/                 # SOUL.md — identity, memory architecture, principles
    17|├── config/               # Config templates (sanitized, no secrets)
    18|├── memory/               # Memory templates and schemas
    19|├── scripts/              # Start scripts, setup helpers
    20|├── setup/                # pgvector setup, deployment guides
    21|├── content_automation/   # 🆕 Multi-Platform Content Generation System
    22|    │    ├── architecture/     # Content pipeline architecture & cost analysis
    23|    │    ├── pipelines/        # Article, carousel, video, avatar pipelines
    24|    │    ├── orchestrator.py   # AI content planner & scheduler
    25|    │    ├── enhanced_content_ideas.py  # Google Workspace + market crawling
    26|    │    ├── docker-compose.yml  # Full Docker deployment
    27|    │    └── monitor.py        # Real-time dashboard & cost tracking
    28|```
    29|
    30|## Memory Architecture
    31|
    32|| Layer | Name | Storage | Purpose |
    33||-------|------|---------|---------|
    34|| L1 | In-context | Context window | Working memory, current task |
    35|| L2 | Episodic | Session search + memory files | What happened, past decisions |
    36|| L3 | Semantic | pgvector + Redis | What it knows, semantic recall |
    37|| L4 | Procedural | Skills system (SKILL.md files) | How to do things |
    38|
    39|## Quick Start
    40|
    41|> **Ubuntu one-liner — everything automated.** See [INSTALL.md](INSTALL.md) for full docs.
    42|
    43|```bash
    44|# 1. Clone Zeus
    45|git clone https://github.com/Aristotlev/ZEUS-FRAMEWORK.git zeus
    46|cd zeus
    47|
    48|# 2. Run the installer (Python, Redis, PostgreSQL, pgvector, Hermes, OpenRouter — all automated)
    49|chmod +x install.sh
    50|./install.sh
    51|```
    52|
    53|The installer will ask for your **[OpenRouter API key](https://openrouter.ai/keys)** (free tier available) and sets up everything.
    54|
    55|```bash
    56|# After install:
    57|source ~/.bashrc     # reload PATH
    58|hermes doctor        # verify everything is healthy
    59|hermes               # start Zeus ⚡
    60|```
    61|
    62|### Already running Hermes? Upgrade to Zeus in one command:
    63|
    64|```bash
    65|bash <(curl -fsSL https://raw.githubusercontent.com/Aristotlev/ZEUS-FRAMEWORK/main/scripts/zeus-upgrade.sh)
    66|```
    67|
    68|→ **[Full installation guide & troubleshooting](INSTALL.md)**
    69|
    70|## 🎬 Content Automation System
    71|
    72|**Generate professional financial content at scale - $227/month vs $5000+ competitors spend**
    73|
    74|### **5 Content Types, Full Automation:**
    75|- **📄 Articles** ($0.22 each) → SEO-optimized + hero images + publishing
    76|- **🎠 Carousels** ($0.71 each) → Data visualization + multi-slide content  
    77|- **🎬 Videos** ($0.49 each) → Short-form + voiceover + thumbnails
    78|- **🧑‍💼 Avatar Videos** ($0.137 each) → Professional presenter + backgrounds *(60 min/month FREE)*
    79|- **🚨 Alerts** ($0.06 each) → Breaking news + congressional trades + market moves
    80|
    81|### **3 Content Discovery Sources:**
    82|1. **📂 File System Drops** → Screenshots, links, notes analyzed daily
    83|2. **📊 Google Sheets Integration** → Live collaboration + structured input  
    84|3. **🌐 Automated Market Crawling** → 8 markets, congressional trades, RSS feeds
    85|
    86|### **Cost-Optimized Media Stack:**
   87|- **🤖 LLM:** DeepSeek V4 via OpenRouter ($3.50/month)
   88|- **🎨 Images:** fal.ai Flux Pro/Schnell + Ideogram 2.0 ($65/month)
   89|- **🎙️ Voice:** Fish Audio API ($0.75/month) - *98% cheaper than ElevenLabs*
   90|- **🧑‍💼 Avatars:** Vidnoz FREE tier (60 min/month) - *$0 vs $24/month HeyGen*
   91|- **📱 Publishing:** Publer API to all platforms ($0/month)
   92|
   93|**Result:** Professional content creation at **$2.73/piece** vs competitors' **$25+/piece** - 86% cost advantage
   94|
   95|### **Quick Start - Content System:**
   96|```bash
   97|cd content_automation/
   98|
   99|# 1. Setup environment
   100|cp .env.example .env
   101|# Edit .env with your API keys
   102|
   103|# 2. Setup Google Workspace integration (optional)
   104|./setup_google_workspace.sh
   105|
   106|# 3. Setup content ideas folders
   107|./setup_content_ideas.sh
   108|
   109|# 4. Start the full pipeline
   110|./docker-zeus.sh up
   111|
   112|# 5. Monitor at http://localhost:8080/monitor
   113|```
   114|
   115|## Skills (L4 Memory)
   116|
   117|95+ skills across domains:
   118|
   119|- **autonomous-ai-agents** — Claude Code, Codex, OpenCode, subagent delegation
   120|- **creative** — ASCII art, diagrams, infographics, Excalidraw, pixel art
   121|- **data-science** — Jupyter live kernel
   122|- **devops** — Remote access, pgvector setup, webhooks, Wake-on-LAN system
   123|- **email** — Himalaya IMAP/SMTP, multi-backend email system (AgentMail, Gmail, Proton, Resend/SendGrid)
   124|- **gaming** — Minecraft modpack servers, Pokemon
   125|- **github** — Auth, code review, issues, PRs, repo management
   126|- **mcp** — Model Context Protocol client
   127|- **media** — YouTube, GIFs, music generation, spectrograms
   128|- **mlops** — HuggingFace, evaluation, inference, training, research
   129|- **productivity** — Google Workspace, Notion, PDFs, PowerPoint
   130|- **red-teaming** — LLM jailbreak techniques
   131|- **research** — arXiv, blog monitoring, prediction markets, competitive analysis
   132|- **smart-home** — Philips Hue
   133|- **social-media** — X/Twitter
   134|- **software-development** — Planning, TDD, debugging, code review
   135|
   136|## The Stack
   137|
   138|- **Redis** — Task queue + L1 cache (hot paths)
   139|- **PostgreSQL + pgvector** — L3 semantic memory (1536-dim embeddings)
   140|- **Mnemosyne** — Memory plugin with circuit breaker, auto-mirror, session summarization
   141|- **OpenClaw** — Distributed execution on Oracle ARM nodes
   142|- **Hermes Agent** — Core engine with 50+ built-in tools
   143|
   144|## Philosophy
   145|
   146|> Local-first. Evolving. Resilient. Modular. Honest.
   147|
   148|All data stays on your machine. No cloud dependencies for storage.
   149|Skills and knowledge grow with use. Circuit breakers and fallback models
   150|ensure graceful degradation. Every layer can be swapped or extended.
   151|
   152|## License
   153|
   154|Hermes Agent core: See core/LICENSE
   155|Zeus additions (stack, plugins, soul, skills, config): MIT
   156|
   157|## Credits
   158|
   159|Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research.
   160|Zeus Framework assembled and configured by [example-user](https://github.com/example-user).
   161|