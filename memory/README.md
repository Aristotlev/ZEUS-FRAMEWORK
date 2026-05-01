# Zeus — Persistent Memory

This directory holds the memory files that survive across sessions.
Zeus uses a 4-layer memory hierarchy:

- L1: In-context (ephemeral, current conversation)
- L2: Episodic (session_search, past session summaries)
- L3: Semantic (Mnemosyne pgvector, semantic recall)
- L4: Procedural (skills/ directory, playbooks)

## Memory Files

- MEMORY.md — Agent's personal notes (environment, conventions, lessons)
- USER.md — User profile (preferences, communication style, setup)

These are automatically read at the start of every session and
updated when durable facts are discovered.
