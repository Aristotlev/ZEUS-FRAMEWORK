# Memory Architecture

Zeus has a 4-layer memory model. Every layer is independently swappable.

| Layer | Name | Storage | Purpose |
|---|---|---|---|
| **L1** | In-context | The model's context window | Working memory, current task |
| **L2** | Episodic | Hermes session search + memory files | What happened, past decisions |
| **L3** | Semantic | PostgreSQL + pgvector (via Mnemosyne plugin) | What it knows, semantic recall |
| **L4** | Procedural | Skill `SKILL.md` files in `skills/` | How to do things |

## L1 — In-context

The model's running window. Nothing to configure — it's the LLM's prompt cache.

## L2 — Episodic

Per-session memory: previous turns of the current conversation, plus what Hermes decides is worth keeping in `~/.hermes/memory/`. See [Hermes' memory docs](https://github.com/NousResearch/hermes-agent) for the upstream behavior.

## L3 — Semantic (Mnemosyne)

The Zeus-specific layer. `plugins/mnemosyne/` writes 1536-dim embeddings into a `conversation_memory` and `knowledge_base` table backed by pgvector, with a Redis hot-path for the most recently touched rows.

| Component | Where |
|---|---|
| Plugin source | [`plugins/mnemosyne/`](../plugins/mnemosyne/) |
| Stack glue | [`stack/hermes_stack.py`](../stack/hermes_stack.py) |
| pgvector setup | [pgvector.md](pgvector.md) |
| Configuration env vars | `MNEMOSYNE_PG_HOST`, `MNEMOSYNE_PG_PORT`, `MNEMOSYNE_PG_DB`, `MNEMOSYNE_PG_USER`, `MNEMOSYNE_PG_PASSWORD`, `MNEMOSYNE_REDIS_URL` |

Features baked in:
- **Circuit breaker** — drops to in-memory fallback if Postgres is unreachable
- **Auto-mirror** — every L1 turn that contains a fact gets mirrored into L3
- **Session summarization** — long sessions get summarized into a single L3 row at end-of-turn

## L4 — Procedural (Skills)

Each skill is a directory under `skills/<domain>/<skill>/` with a `SKILL.md` file describing when and how to do something. The `SKILL.md` frontmatter declares triggers, and the body is procedural prose the agent reads when it decides the skill applies.

See [skills.md](skills.md) for the full list.
