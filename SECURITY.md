# Security Policy

## Scope

Zeus Framework is a self-hosted AI agent stack. The components in scope for security reports are:

- The `plugins/mnemosyne/` memory plugin
- The `stack/` Redis + pgvector glue
- The `deploy/` Docker / Hetzner deployment
- The `skills/autonomous-ai-agents/multi-agent-content-pipeline/` pipeline
- The `install.sh` installer
- The README, docs, and example configs (for accidental secret leakage)

The `core/` directory is vendored from [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — security issues there should be reported upstream per [their security policy](https://github.com/NousResearch/hermes-agent/blob/main/SECURITY.md).

## Reporting a vulnerability

**Do not** open a public GitHub issue for security problems.

Instead, use [GitHub's private vulnerability reporting](https://github.com/Aristotlev/ZEUS-FRAMEWORK/security/advisories/new) on this repo. We aim to acknowledge within 72 hours.

Please include:
- A description of the issue and its impact
- Steps to reproduce
- The commit or release version you observed it on
- Any proof-of-concept (scrub real keys before sending)

## What counts

- Credential leakage in committed files (keys, tokens, page-ids tied to a real account)
- Path-traversal / RCE in any pipeline component
- Privilege escalation in the Docker compose / systemd unit setup
- Insecure defaults that expose Redis / Postgres to the public internet
- Skill `SKILL.md` files that exfiltrate user data

## What doesn't count

- The `change-me-in-prod` placeholder password in `stack/hermes_stack.py` — it is a placeholder; production deploys override `MNEMOSYNE_PG_PASSWORD`
- Issues in third-party APIs Zeus calls (fal.ai, fish.audio, Notion, Publer) — report those upstream
- Bugs in `core/` (vendored Hermes upstream) — report those to Nous Research

## Disclosure

Once a fix is merged, we credit the reporter (GitHub handle, optional name) in the release notes unless the reporter prefers anonymity.
