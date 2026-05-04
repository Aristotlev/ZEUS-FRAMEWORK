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
    41|### Prerequisites
    42|- Python 3.11+
    43|- Redis (`sudo apt install redis-server`)
    44|- PostgreSQL 16 with pgvector extension
    45|- An OpenRouter API key (or compatible LLM endpoint)
    46|
    47|### 1. Set up PostgreSQL + pgvector (no sudo needed)
    48|```bash
    49|cd setup/
    50|# Follow pgvector-no-sudo-setup skill instructions
    51|```
    52|
    53|### 2. Configure
    54|```bash
    55|cp config/.env.example ~/.hermes/.env
    56|# Edit ~/.hermes/.env with your API keys
    57|
    58|cp config/config.example.yaml ~/.hermes/config.yaml
    59|# Edit ~/.hermes/config.yaml with your model preferences
    60|```
    61|
    62|### 3. Install Mnemosyne plugin
    63|```bash
    64|cp -r plugins/mnemosyne ~/.hermes/plugins/
    65|```
    66|
    67|### 4. Install the core
    68|```bash
    69|cd core/
    70|pip install -e .
    71|```
    72|
    73|### 5. Run
    74|```bash
    75|hermes  # CLI mode
    76|hermes gateway  # Discord/Telegram/Slack gateway
    77|
    78|# Content automation system
    79|cd content_automation/
    80|./docker-zeus.sh up  # Start full content pipeline
    81|```
    82|
    83|## 🎬 Content Automation System
    84|
    85|**Generate professional financial content at scale - $227/month vs $5000+ competitors spend**
    86|
    87|### **5 Content Types, Full Automation:**
    88|- **📄 Articles** ($0.22 each) → SEO-optimized + hero images + publishing
    89|- **🎠 Carousels** ($0.71 each) → Data visualization + multi-slide content  
    90|- **🎬 Videos** ($0.49 each) → Short-form + voiceover + thumbnails
    91|- **🧑‍💼 Avatar Videos** ($0.137 each) → Professional presenter + backgrounds *(60 min/month FREE)*
    92|- **🚨 Alerts** ($0.06 each) → Breaking news + congressional trades + market moves
    93|
    94|### **3 Content Discovery Sources:**
    95|1. **📂 File System Drops** → Screenshots, links, notes analyzed daily
    96|2. **📊 Google Sheets Integration** → Live collaboration + structured input  
    97|3. **🌐 Automated Market Crawling** → 8 markets, congressional trades, RSS feeds
    98|
    99|### **Cost-Optimized Media Stack:**
   100|- **🤖 LLM:** DeepSeek V4 via OpenRouter ($3.50/month)
   101|- **🎨 Images:** fal.ai Flux Pro/Schnell + Ideogram 2.0 ($65/month)
   102|- **🎙️ Voice:** Fish Audio API ($0.75/month) - *98% cheaper than ElevenLabs*
   103|- **🧑‍💼 Avatars:** Vidnoz FREE tier (60 min/month) - *$0 vs $24/month HeyGen*
   104|- **📱 Publishing:** Publer API to all platforms ($0/month)
   105|
   106|**Result:** Professional content creation at **$2.73/piece** vs competitors' **$25+/piece** - 86% cost advantage
   107|
   108|### **Quick Start - Content System:**
   109|```bash
   110|cd content_automation/
   111|
   112|# 1. Setup environment
   113|cp .env.example .env
   114|# Edit .env with your API keys
   115|
   116|# 2. Setup Google Workspace integration (optional)
   117|./setup_google_workspace.sh
   118|
   119|# 3. Setup content ideas folders
   120|./setup_content_ideas.sh
   121|
   122|# 4. Start the full pipeline
   123|./docker-zeus.sh up
   124|
   125|# 5. Monitor at http://localhost:8080/monitor
   126|```
   127|
   128|## Skills (L4 Memory)
   129|
   130|95+ skills across domains:
   131|
   132|- **autonomous-ai-agents** — Claude Code, Codex, OpenCode, subagent delegation
   133|- **creative** — ASCII art, diagrams, infographics, Excalidraw, pixel art
   134|- **data-science** — Jupyter live kernel
   135|- **devops** — Remote access, pgvector setup, webhooks, Wake-on-LAN system
   136|- **email** — Himalaya IMAP/SMTP, multi-backend email system (AgentMail, Gmail, Proton, Resend/SendGrid)
   137|- **gaming** — Minecraft modpack servers, Pokemon
   138|- **github** — Auth, code review, issues, PRs, repo management
   139|- **mcp** — Model Context Protocol client
   140|- **media** — YouTube, GIFs, music generation, spectrograms
   141|- **mlops** — HuggingFace, evaluation, inference, training, research
   142|- **productivity** — Google Workspace, Notion, PDFs, PowerPoint
   143|- **red-teaming** — LLM jailbreak techniques
   144|- **research** — arXiv, blog monitoring, prediction markets, competitive analysis
   145|- **smart-home** — Philips Hue
   146|- **social-media** — X/Twitter
   147|- **software-development** — Planning, TDD, debugging, code review
   148|
   149|## The Stack
   150|
   151|- **Redis** — Task queue + L1 cache (hot paths)
   152|- **PostgreSQL + pgvector** — L3 semantic memory (1536-dim embeddings)
   153|- **Mnemosyne** — Memory plugin with circuit breaker, auto-mirror, session summarization
   154|- **OpenClaw** — Distributed execution on Oracle ARM nodes
   155|- **Hermes Agent** — Core engine with 50+ built-in tools
   156|
   157|## Philosophy
   158|
   159|> Local-first. Evolving. Resilient. Modular. Honest.
   160|
   161|All data stays on your machine. No cloud dependencies for storage.
   162|Skills and knowledge grow with use. Circuit breakers and fallback models
   163|ensure graceful degradation. Every layer can be swapped or extended.
   164|
   165|## License
   166|
   167|Hermes Agent core: See core/LICENSE
   168|Zeus additions (stack, plugins, soul, skills, config): MIT
   169|
   170|## Credits
   171|
   172|Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research.
   173|Zeus Framework assembled and configured by [example-user](https://github.com/example-user).
   174|