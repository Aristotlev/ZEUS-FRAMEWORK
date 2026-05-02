---
name: hermes-post-update-recovery
description: "Fix stale code, ImportError, and Discord silence after `hermes update`."
version: 1.0.0
metadata:
  hermes:
    tags: [hermes, update, troubleshooting, gateway, pycache, import-error]
---

# Hermes Post-Update Recovery

After `hermes update`, the on-disk source code changes but **running processes (gateway + CLI) still execute old code from memory and stale `__pycache__` bytecode**. This causes a cascade of failures that look like provider auth issues but are really import corruption.

## Symptoms

- `ImportError: cannot import name 'X' from 'utils'` in agent.log or errors.log
- "Discord bot token already in use" — race between old and new gateway instances during update restart
- Gateway says "connected" but messages go unprocessed — agent dispatch crashes on stale imports mid-request
- CLI tools (`send_message`, etc.) fail with ImportError — CLI process is also stale
- HTTP 401 errors that look like bad API keys — actually the import error prevents proper key loading

## Root Cause

1. `hermes update` pulls new code and restarts the gateway, but the old gateway process may briefly coexist (token conflict)
2. Stale `.pyc` files in `__pycache__` dirs (including mismatched Python version bytecode like `.cpython-312.pyc` alongside `.cpython-311.pyc`) don't match the new source
3. The CLI process started before the update keeps running with old in-memory code — no hot-reload

## Fix Procedure

```bash
# 1. Nuke stale bytecode (excludes venv/)
find ~/.hermes/hermes-agent/ -path "*/venv" -prune -o -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
find ~/.hermes/hermes-agent/ -maxdepth 1 -name "*.pyc" -delete 2>/dev/null

# 2. Kill ALL hermes processes (gateway + CLI)
pkill -f "hermes_cli.main" 2>/dev/null
pkill -f "hermes" 2>/dev/null
sleep 2

# 3. Restart gateway service
systemctl --user restart hermes-gateway

# 4. Start a fresh CLI session (old one is dead)
hermes
```

## Verification

```bash
# Confirm no import errors in fresh gateway
sleep 5 && tail -20 ~/.hermes/logs/agent.log | grep -i "import\|error"

# Confirm gateway connected to Discord
tail -10 ~/.hermes/logs/agent.log | grep "discord connected"

# Send a test message and watch for dispatch activity
tail -f ~/.hermes/logs/agent.log
```

## Discord Config: Silent Message Drops

Even with imports fixed, the bot can appear "connected" in logs but never respond. The most common cause is Discord config silently dropping messages:

### Triple filter causing silence

Three settings stack up to block ALL messages:

1. **`discord.require_mention: true`** in config.yaml — bot only responds when @mentioned
2. **`discord.free_response_channels: ''`** (empty) — no channels exempt from the mention requirement
3. **`DISCORD_IGNORE_NO_MENTION`** env var defaults to `"true"` — additional code-level filter that drops non-mention messages even in non-DM channels

The gateway logs **nothing** when messages are dropped — no error, no warning. It just silently returns from `on_message`. This makes it look like Discord connectivity is broken when really the bot is just ignoring everything.

### Fix

Set `require_mention: false` and/or add channel IDs to `free_response_channels` in the discord section of config.yaml. Or set `DISCORD_IGNORE_NO_MENTION=false` in .env.

### Diagnostic

If gateway log shows "discord connected" but no message processing entries appear:

1. Check `require_mention` and `free_response_channels` in config.yaml
2. Check `DISCORD_IGNORE_NO_MENTION` in .env
3. Try @mentioning the bot directly — if that works, the issue is the mention filter
4. DMs bypass the mention filter entirely — if DMs work but channel messages don't, it's definitely the config

### Thread participation exemption

Messages inside threads where the bot has previously participated (auto-created or replied in) bypass `require_mention`. So old threads may work while new messages in channels don't. This can be confusing — "it worked yesterday in that thread but not today in the channel."
