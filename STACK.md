# Zeus Framework — Full Stack

This directory contains the complete Zeus Framework stack.
Every component needed to run a fully local, memory-persistent AI agent.

```
zeus-framework/
├── core/               # Hermes Agent engine (full source + SOUL.md)
│   ├── SOUL.md         # ← Shared soul (identity, memory arch, principles)
│   ├── run_agent.py    # AIAgent class — core conversation loop
│   ├── model_tools.py  # Tool orchestration
│   ├── tools/          # 50+ built-in tool implementations
│   ├── gateway/        # Discord, Telegram, Slack, WhatsApp adapters
│   ├── agent/          # Prompt builder, compressor, caching
│   ├── hermes_cli/     # CLI subcommands, config, setup wizard
│   ├── acp_adapter/    # VS Code / Zed / JetBrains integration
│   ├── cron/           # Scheduler
│   └── ui-tui/         # React terminal UI
├── openclaw/           # OpenClaw distributed execution engine
│   ├── openclaw.mjs    # Main entry point
│   ├── skills/         # 20+ OpenClaw skills (coding, Discord, Slack, etc.)
│   ├── docs/           # Documentation
│   └── scripts/        # Setup and utility scripts
├── stack/              # Redis + pgvector interface (HermesStack)
│   └── hermes_stack.py # Unified cache + vector store client
├── plugins/
│   └── mnemosyne/      # L3 vector memory plugin (Redis + pgvector)
├── skills/             # 93+ procedural skills (L4 memory)
├── soul/               # Standalone soul reference
│   └── SOUL.md         # ← Same as core/SOUL.md (shared)
├── config/             # Config templates (sanitized, no secrets)
├── memory/             # Memory templates and schemas
├── scripts/            # Start scripts, setup helpers
└── setup/              # pgvector setup, OpenClaw deployment guides
```

## Shared SOUL.md

The `core/SOUL.md` and `soul/SOUL.md` are identical — the shared soul definition.
When deploying, copy to `~/.hermes/persona.md` to activate Zeus identity.

## OpenClaw Stack

The `openclaw/` directory contains the full OpenClaw execution engine:
- Entry point: `openclaw.mjs`
- Skills: coding-agent, Discord, Slack, Trello, 1Password, Obsidian, tmux, and more
- Runs on Oracle ARM (Ampere A1) instances for distributed compute
