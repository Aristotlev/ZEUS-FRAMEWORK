# Contributing to Zeus Framework

Thanks for your interest. Zeus is a thin layer on top of [Hermes Agent](https://github.com/NousResearch/hermes-agent) — most contributions land in one of three places, and where you contribute determines how the change flows.

## Where the change belongs

| You want to change… | Where it lives | How to contribute |
|---|---|---|
| Core agent loop, CLI, gateways, model adapters | `core/` (vendored Hermes upstream) | Open a PR against [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) and bump the vendored ref |
| Memory plugin (Mnemosyne) | `plugins/mnemosyne/` | PR against this repo |
| Stack glue (Redis + pgvector) | `stack/` | PR against this repo |
| Skills (procedural memory) | `skills/<domain>/<skill>/SKILL.md` | PR against this repo; follow the existing SKILL.md frontmatter format |
| Content pipeline | `skills/autonomous-ai-agents/multi-agent-content-pipeline/` | PR against this repo |
| Deployment / install | `deploy/`, `install.sh`, `docker/` | PR against this repo |
| Documentation | `docs/`, `README.md` | PR against this repo |

If you're not sure, open an issue and ask.

## Local setup

```bash
git clone https://github.com/Aristotlev/ZEUS-FRAMEWORK.git zeus
cd zeus
./install.sh   # Python, Redis, PostgreSQL, pgvector, Hermes, OpenRouter
```

See [docs/installation.md](docs/installation.md) for the full guide.

## Before you open a PR

1. **No secrets, no personal info.** No real API keys, no real Notion page-ids, no personal emails or business names. Use placeholders (`<your-key>`, `you@example.com`).
2. **Run the installer at least once** to make sure your change doesn't break a fresh setup.
3. **For skills:** keep `SKILL.md` self-contained — it should be readable without the agent context.
4. **For content-pipeline changes:** every change must preserve the rule that **Notion archive runs BEFORE any external publish call**. Generation spend has been lost before because of this.
5. **For deployment changes:** test against `deploy/docker-compose.prod.yml` if you can.

## Style

- Python: keep formatting close to what's already in the file. No CI formatter is enforced.
- Markdown: short sentences, examples first, theory after.
- Commit messages: present-tense subject, body explains *why*, not *what*.

## Reporting issues

Use [GitHub Issues](https://github.com/Aristotlev/ZEUS-FRAMEWORK/issues). Include:
- What you ran
- What you expected
- What happened (paste logs if you have them — scrub keys first)
- Your platform (Linux distro / macOS version)

## License

By contributing, you agree your contribution is released under the [MIT License](LICENSE).
