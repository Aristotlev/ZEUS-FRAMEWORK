# Skills

Skills are L4 procedural memory: directories under `skills/<domain>/<skill>/` with a `SKILL.md` file the agent reads when the skill applies.

## Domains

| Domain | What's in it |
|---|---|
| `apple/` | Apple Notes, Reminders, FindMy, iMessage |
| `autonomous-ai-agents/` | Claude Code, Codex, OpenCode, Hermes, multi-agent content pipeline |
| `creative/` | ASCII art, architecture diagrams, ComfyUI, Excalidraw, infographics, pixel art, baoyu comic/infographic |
| `data-science/` | Jupyter live kernel |
| `devops/` | Remote access, pgvector setup, webhooks, Wake-on-LAN |
| `email/` | Himalaya IMAP/SMTP, multi-backend email (AgentMail, Gmail, Proton, Resend/SendGrid) |
| `gaming/` | Minecraft modpack servers, Pokemon |
| `github/` | Auth, code review, issues, PRs, repo management, agent repo publishing |
| `mcp/` | Model Context Protocol client |
| `media/` | YouTube, GIFs, music generation, spectrograms |
| `mlops/` | HuggingFace, evaluation, inference, training, research |
| `productivity/` | Google Workspace, Notion, PDFs, PowerPoint |
| `red-teaming/` | LLM jailbreak techniques (defensive research) |
| `research/` | arXiv, blog monitoring, prediction markets, competitive analysis |
| `smart-home/` | Philips Hue |
| `social-media/` | X/Twitter |
| `software-development/` | Planning, TDD, debugging, code review |

## Anatomy of a skill

```
skills/<domain>/<skill>/
├── SKILL.md           # the procedure (frontmatter + prose)
├── references/        # supporting docs the agent can read on demand
└── scripts/           # executables the skill calls
```

`SKILL.md` frontmatter:

```yaml
---
name: my-skill
description: When and why this skill applies.
triggers:
  - "natural language phrase that should activate this skill"
  - "another phrase"
---
```

The body is procedural prose. Lead with the workflow, follow with pitfalls. The agent reads the body when it decides the skill applies.

## Adding a skill

1. Pick a domain. If none fits, propose a new one in your PR description.
2. Copy an existing skill in that domain as a template (e.g. for productivity, copy `skills/productivity/notion/SKILL.md`).
3. Update frontmatter — name, description, triggers.
4. Write the workflow first, pitfalls second.
5. PR the change. See [CONTRIBUTING.md](../CONTRIBUTING.md).

## Skill bundling

The shipped skills are listed in `skills/.bundled_manifest`. The installer syncs them to `~/.hermes/skills/` on first run. Adding a skill in this repo and re-running the installer (or `bash scripts/zeus-upgrade.sh`) syncs it to your local Hermes home.
