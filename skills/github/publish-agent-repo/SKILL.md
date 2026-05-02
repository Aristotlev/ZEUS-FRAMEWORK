---
name: publish-agent-repo
description: Assemble a sanitized public GitHub repo from a live Hermes agent setup — strip all credentials, structure the repo, push, and share the link.
version: 1.0.0
triggers:
  - upload to github
  - publish repo
  - make public repo
  - push to github
---

# Publish Agent Repo to GitHub

Assemble and push a clean public repo from a live Hermes setup, stripping all credentials and personal data.

## Steps

### 1. Discover the source structure

The live setup lives at `~/.hermes/`. Key directories:
- `hermes-agent/` — core engine (already has .gitignore)
- `plugins/` — custom plugins (e.g., mnemosyne)
- `skills/` — procedural skills (L4 memory)
- `memories/` — persistent memory files (contain personal data!)
- Config file — contains credentials, tokens, channel IDs
- Env file — contains all credential values
- `~/hermes_stack.py` — Redis + pgvector interface
- `~/start_hermes_pg.sh` — PostgreSQL start script

### 2. Assemble into a clean directory

```bash
mkdir -p /tmp/REPO-NAME/{core,stack,plugins,soul,config,memory,scripts,setup}

# Core engine (respects existing .gitignore)
rsync -a --exclude='.git' --exclude='venv' --exclude='__pycache__' \
  --exclude='node_modules' --exclude='.env' --exclude='*.pyc' \
  ~/.hermes/hermes-agent/ /tmp/REPO-NAME/core/

# Plugins
cp -r ~/.hermes/plugins/mnemosyne/ /tmp/REPO-NAME/plugins/mnemosyne/

# Skills
rsync -a --exclude='__pycache__' ~/.hermes/skills/ /tmp/REPO-NAME/skills/

# Stack + scripts
cp ~/hermes_stack.py /tmp/REPO-NAME/stack/
cp ~/start_hermes_pg.sh /tmp/REPO-NAME/scripts/

# OpenClaw (if installed — typically in npm cache)
OPENCLAW_DIR=$(find ~/.npm -path "*/node_modules/openclaw" -type d 2>/dev/null | head -1)
if [ -n "$OPENCLAW_DIR" ]; then
  cp -r "$OPENCLAW_DIR" /tmp/REPO-NAME/openclaw/
  # Trim compiled/heavy assets (dist/ alone can be 1.3GB)
  find /tmp/REPO-NAME/openclaw/dist -delete 2>/dev/null
  find /tmp/REPO-NAME/openclaw/assets -delete 2>/dev/null
  find /tmp/REPO-NAME/openclaw -name '*.map' -delete
  find /tmp/REPO-NAME/openclaw -name '*.wasm' -delete
fi
```

### 3. Sanitize — CRITICAL

**Never copy these as-is — always create templates with placeholder values:**
- Env file → create `.env.example` with placeholder values like `sk-or-...`
- Config YAML → create `config.example.yaml` stripped of all real keys, tokens, channel IDs, passwords
- Memory files → create `MEMORY.example.md` and `USER.example.md` as generic templates
- Session files → exclude entirely (contain full conversation history)

**Watch for partial credentials in:**
- Plugin source code docstrings (default passwords, connection strings — replace with `your_value_here`)
- Config comments referencing real model names or provider details
- Stack connection strings with embedded credentials

### 4. Write framework-level docs

Create these files in the repo root:
- `README.md` — overview, quick start, what's inside
- `LICENSE` — MIT or match core engine license
- `.gitignore` — venv, __pycache__, .env, *.pem, logs, data
- `soul/SOUL.md` — identity, memory architecture diagram, principles, stack diagram
- `core/SOUL.md` — copy of soul/SOUL.md so the core engine loads it (shared soul)
- `STACK.md` — full stack directory guide explaining every component including OpenClaw

### 5. Git init and commit

```bash
cd /tmp/REPO-NAME
git init
git config user.email "user@example.com"
git config user.name "Username"
git branch -m main
git add -A
git commit -m "⚡ Initial upload"
```

### 6. GitHub PAT — use CLASSIC token

**PITFALL: Fine-grained PATs often lack repo creation and write permissions.**

Symptoms of insufficient fine-grained PAT:
- `Resource not accessible by personal access token (createRepository)` — can't create repos
- `Permission denied` on push — can't write to repos

**Solution:** Use a **classic PAT** with the `repo` scope (full repository access).

```bash
# Set remote with embedded token
git remote add origin https://USERNAME:TOKEN@github.com/USERNAME/REPO.git
git push -u origin main
```

### 7. Make repo public (if created as private)

```bash
curl -s -X PATCH \
  -H "Authorization: token TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/USERNAME/REPO \
  -d '{"private":false}'
```

### 8. Share the link

Use AgentMail to send the repo URL to the user's email.

## Pitfalls

- **Fine-grained PATs are limited.** Classic PATs with `repo` scope work for everything. Don't waste time debugging fine-grained permission errors.
- **Credentials hide in docstrings.** Plugins may have default DB passwords in module docstrings. Sanitize.
- **Memory files are personal.** Never copy persistent memory files as-is — they contain system details, usernames, passwords.
- **Session files contain full conversations.** Exclude the entire sessions/ directory.
- **Config files have channel IDs.** Even seemingly harmless fields like home channel IDs are sensitive.
- **Git needs user identity.** `git commit` fails without `user.name` and `user.email` configured.
- **OpenClaw dist/ is massive.** The compiled JS in dist/ can be 1.3GB+. Always exclude it — only copy source .mjs, skills/, docs/, scripts/, package.json, LICENSE, README.md.
- **Shared SOUL.md.** Place the same SOUL.md in both `soul/SOUL.md` and `core/SOUL.md` so it's discoverable both as a standalone reference and as the engine's persona file.
