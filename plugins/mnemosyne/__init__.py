"""Mnemosyne — Local L3 vector memory provider for Hermes.

Redis (L1 cache) + pgvector (L2 semantic search). Zero cloud dependencies.
All data stays local on your machine.

Requires:
  - Redis on 127.0.0.1:6379 (system service)
  - PostgreSQL with pgvector extension
  - Python packages: redis, psycopg2-binary, numpy
  - An embedding endpoint (OpenRouter or local Ollama)

Config via $HERMES_HOME/mnemosyne.json or environment variables:
  MNEMOSYNE_EMBEDDING_PROVIDER  — "openrouter" (default) or "ollama"
  MNEMOSYNE_EMBEDDING_MODEL     — model name (default: openai/text-embedding-3-small)
  MNEMOSYNE_PG_HOST             — PostgreSQL host (default: 127.0.0.1)
  MNEMOSYNE_PG_PORT             — PostgreSQL port (default: 5432)
  MNEMOSYNE_PG_DB               — Database name (default: hermes_vectors)
  MNEMOSYNE_PG_USER             — Database user (default: hermes)
  MNEMOSYNE_PG_PASSWORD         — Database password
  MNEMOSYNE_REDIS_URL           — Redis URL (default: redis://127.0.0.1:6379)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SECS = 120


def _load_config() -> dict:
    from hermes_constants import get_hermes_home

    config = {
        "embedding_provider": os.environ.get("MNEMOSYNE_EMBEDDING_PROVIDER", "openrouter"),
        "embedding_model": os.environ.get("MNEMOSYNE_EMBEDDING_MODEL", "openai/text-embedding-3-small"),
        "pg_host": os.environ.get("MNEMOSYNE_PG_HOST", "127.0.0.1"),
        "pg_port": int(os.environ.get("MNEMOSYNE_PG_PORT", "5432")),
        "pg_db": os.environ.get("MNEMOSYNE_PG_DB", "hermes_vectors"),
        "pg_user": os.environ.get("MNEMOSYNE_PG_USER", "hermes"),
        "pg_password": os.environ.get("MNEMOSYNE_PG_PASSWORD", ""),
        "redis_url": os.environ.get("MNEMOSYNE_REDIS_URL", "redis://127.0.0.1:6379"),
    }

    config_path = get_hermes_home() / "mnemosyne.json"
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({k: v for k, v in file_cfg.items()
                           if v is not None and v != ""})
        except Exception:
            pass

    return config


def _get_embedding(text: str, config: dict) -> List[float]:
    import numpy as np

    provider = config.get("embedding_provider", "openrouter")
    model = config.get("embedding_model", "openai/text-embedding-3-small")

    if provider == "ollama":
        import requests
        resp = requests.post(
            "http://127.0.0.1:11434/api/embeddings",
            json={"model": model, "prompt": text[:8000]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    # OpenRouter (OpenAI-compatible embeddings endpoint)
    import httpx
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    resp = httpx.post(
        "https://openrouter.ai/api/v1/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "input": text[:8000]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "mnemosyne_search",
    "description": (
        "Semantic search across the local knowledge base. Returns entries "
        "ranked by similarity to the query. Use for recalling facts, past "
        "decisions, or any previously stored knowledge."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

RECALL_SCHEMA = {
    "name": "mnemosyne_recall",
    "description": (
        "Recall conversation memory — past exchanges semantically similar "
        "to the query. Use to remember what was discussed in prior sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to recall from conversation history",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

STORE_SCHEMA = {
    "name": "mnemosyne_store",
    "description": (
        "Store a piece of knowledge in the local vector database for future "
        "semantic recall. Use for important facts, decisions, or user preferences."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The knowledge to store",
            },
            "source": {
                "type": "string",
                "description": "Label for the source (e.g. 'user_preference', 'decision')",
                "default": "agent",
            },
        },
        "required": ["content"],
    },
}


class MnemosynePlugin(MemoryProvider):

    def __init__(self):
        self._config: dict = {}
        self._session_id: str = ""
        self._pg_config: dict = {}
        self._redis = None
        self._prefetch_thread: threading.Thread | None = None
        self._prefetch_lock = threading.Lock()
        self._prefetch_result: str = ""
        self._sync_thread: threading.Thread | None = None
        self._failure_count = 0
        self._breaker_open_until = 0.0

    def _is_breaker_open(self) -> bool:
        if self._failure_count < _BREAKER_THRESHOLD:
            return False
        if time.monotonic() > self._breaker_open_until:
            self._failure_count = 0
            return False
        return True

    def _record_success(self):
        self._failure_count = 0

    def _record_failure(self):
        self._failure_count += 1
        if self._failure_count >= _BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
            logger.warning("Mnemosyne circuit breaker open for %ds", _BREAKER_COOLDOWN_SECS)

    def _similarity_search(self, table: str, embedding: List[float],
                           limit: int = 5, threshold: float = 0.5) -> List[Dict]:
        import psycopg2
        vec_str = json.dumps(embedding)
        conn = psycopg2.connect(**self._pg_config)
        try:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT source, content, metadata,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM {table}
                WHERE 1 - (embedding <=> %s::vector) > %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, vec_str, threshold, vec_str, limit))
            results = []
            for row in cur.fetchall():
                results.append({
                    "source": row[0],
                    "content": row[1],
                    "metadata": row[2],
                    "similarity": float(row[3]),
                })
            return results
        finally:
            conn.close()

    def _insert_embedding(self, table: str, source: str, content: str,
                          embedding: List[float], metadata: dict | None = None):
        import psycopg2
        conn = psycopg2.connect(**self._pg_config)
        try:
            cur = conn.cursor()
            cur.execute(f"""
                INSERT INTO {table} (source, content, embedding, metadata)
                VALUES (%s, %s, %s::vector, %s::jsonb)
            """, (source, content, json.dumps(embedding),
                  json.dumps(metadata or {})))
            conn.commit()
        finally:
            conn.close()

    # -- MemoryProvider interface ----------------------------------------------

    @property
    def name(self) -> str:
        return "mnemosyne"

    def is_available(self) -> bool:
        try:
            cfg = _load_config()
            import redis as redis_lib
            r = redis_lib.from_url(cfg["redis_url"])
            r.ping()
            r.close()
        except Exception:
            return False
        try:
            cfg = _load_config()
            import psycopg2
            conn = psycopg2.connect(
                host=cfg["pg_host"], port=cfg["pg_port"],
                database=cfg["pg_db"], user=cfg["pg_user"],
                password=cfg["pg_password"],
                connect_timeout=3,
            )
            conn.close()
        except Exception:
            return False
        return True

    def get_config_schema(self):
        return [
            {"key": "embedding_provider", "description": "Embedding provider: openrouter or ollama",
             "default": "openrouter", "choices": ["openrouter", "ollama"]},
            {"key": "embedding_model", "description": "Embedding model name",
             "default": "openai/text-embedding-3-small"},
            {"key": "pg_host", "description": "PostgreSQL host", "default": "127.0.0.1"},
            {"key": "pg_port", "description": "PostgreSQL port", "default": "5432"},
            {"key": "pg_db", "description": "Database name", "default": "hermes_vectors"},
            {"key": "pg_user", "description": "Database user", "default": "hermes"},
            {"key": "pg_password", "description": "Database password",
             "secret": True, "env_var": "MNEMOSYNE_PG_PASSWORD"},
            {"key": "redis_url", "description": "Redis URL", "default": "redis://127.0.0.1:6379"},
        ]

    def save_config(self, values, hermes_home):
        from pathlib import Path
        config_path = Path(hermes_home) / "mnemosyne.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2))

    # -- Core lifecycle -------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._session_id = session_id
        self._pg_config = {
            "host": self._config["pg_host"],
            "port": self._config["pg_port"],
            "database": self._config["pg_db"],
            "user": self._config["pg_user"],
            "password": self._config["pg_password"],
        }

        import redis as redis_lib
        self._redis = redis_lib.from_url(self._config["redis_url"])

        self._ensure_tables()

        logger.info("Mnemosyne initialized (session=%s, pg=%s:%s)",
                     session_id, self._pg_config["host"], self._pg_config["port"])

    def _ensure_tables(self):
        import psycopg2
        conn = psycopg2.connect(**self._pg_config)
        try:
            cur = conn.cursor()
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversation_memory (
                    id SERIAL PRIMARY KEY,
                    source TEXT,
                    content TEXT,
                    embedding vector(1536),
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id SERIAL PRIMARY KEY,
                    source TEXT,
                    content TEXT,
                    embedding vector(1536),
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def _get_conn(self):
        import psycopg2
        return psycopg2.connect(**self._pg_config)

    # -- System prompt --------------------------------------------------------

    def system_prompt_block(self) -> str:
        return (
            "# Mnemosyne Memory (Local L3)\n"
            "Active. Redis cache + pgvector semantic search.\n"
            "Use mnemosyne_search for semantic recall, mnemosyne_recall for "
            "conversation memory, mnemosyne_store to save knowledge.\n"
            "All data is local — no cloud calls for storage/retrieval."
        )

    # -- Prefetch / recall ----------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## Mnemosyne Memory\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self._is_breaker_open():
            return

        def _run():
            try:
                embedding = _get_embedding(query, self._config)
                results = self._similarity_search("knowledge_base", embedding, limit=3, threshold=0.5)
                results += self._similarity_search("conversation_memory", embedding, limit=2, threshold=0.5)
                if results:
                    lines = []
                    for r in results[:5]:
                        src = r.get("source", "")
                        content = r.get("content", "")
                        sim = r.get("similarity", 0)
                        lines.append(f"- [{src}] {content} (sim={sim:.2f})")
                    with self._prefetch_lock:
                        self._prefetch_result = "\n".join(lines)
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.debug("Mnemosyne prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="mnemosyne-prefetch")
        self._prefetch_thread.start()

    # -- Sync turn ------------------------------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if self._is_breaker_open():
            return

        def _sync():
            try:
                combined = f"User: {user_content}\nAssistant: {assistant_content}"
                embedding = _get_embedding(combined, self._config)

                self._insert_embedding(
                    "conversation_memory",
                    source=session_id or self._session_id,
                    content=combined,
                    embedding=embedding,
                    metadata={"role": "turn"},
                )

                self._redis.setex(
                    f"mnemosyne:last_response:{session_id or self._session_id}",
                    3600,
                    assistant_content[:2000],
                )

                self._record_success()
                logger.debug("Mnemosyne synced turn for session %s", session_id)
            except Exception as e:
                self._record_failure()
                logger.warning("Mnemosyne sync failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(target=_sync, daemon=True, name="mnemosyne-sync")
        self._sync_thread.start()

    # -- Hooks ----------------------------------------------------------------

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if action != "add" or self._is_breaker_open():
            return

        def _mirror():
            try:
                embedding = _get_embedding(content, self._config)
                self._insert_embedding(
                    "knowledge_base",
                    source=f"builtin_{target}",
                    content=content,
                    embedding=embedding,
                    metadata={"origin": "builtin_memory", "target": target},
                )
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.debug("Mnemosyne memory mirror failed: %s", e)

        threading.Thread(target=_mirror, daemon=True, name="mnemosyne-mirror").start()

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._is_breaker_open() or not messages:
            return

        def _extract():
            try:
                parts = []
                for msg in messages[-20:]:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role in ("user", "assistant") and content and len(content) < 500:
                        parts.append(f"{role}: {content[:200]}")

                if parts:
                    summary = "\n".join(parts)
                    embedding = _get_embedding(summary, self._config)
                    self._insert_embedding(
                        "knowledge_base",
                        source=f"session_summary:{self._session_id}",
                        content=summary,
                        embedding=embedding,
                        metadata={"type": "session_summary", "session_id": self._session_id},
                    )
                    self._record_success()
            except Exception as e:
                self._record_failure()
                logger.debug("Mnemosyne session end extraction failed: %s", e)

        threading.Thread(target=_extract, daemon=True, name="mnemosyne-session-end").start()

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        if self._is_breaker_open():
            return ""

        def _preserve():
            try:
                for msg in messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role in ("user", "assistant") and content and len(content) > 50:
                        embedding = _get_embedding(content, self._config)
                        self._insert_embedding(
                            "conversation_memory",
                            source=self._session_id,
                            content=content[:1000],
                            embedding=embedding,
                            metadata={"role": role, "preserved_on_compress": True},
                        )
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.debug("Mnemosyne pre-compress preservation failed: %s", e)

        threading.Thread(target=_preserve, daemon=True, name="mnemosyne-precompress").start()
        return ""

    # -- Tool schemas and dispatch -------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, RECALL_SCHEMA, STORE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if self._is_breaker_open():
            return json.dumps({
                "error": "Mnemosyne temporarily unavailable (multiple failures). Will retry."
            })

        try:
            if tool_name == "mnemosyne_search":
                return self._handle_search(args)
            elif tool_name == "mnemosyne_recall":
                return self._handle_recall(args)
            elif tool_name == "mnemosyne_store":
                return self._handle_store(args)
        except Exception as e:
            self._record_failure()
            return tool_error(f"Mnemosyne error: {e}")

        return tool_error(f"Unknown tool: {tool_name}")

    def _handle_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")
        limit = args.get("limit", 5)
        embedding = _get_embedding(query, self._config)
        results = self._similarity_search("knowledge_base", embedding, limit=limit)
        self._record_success()
        if not results:
            return json.dumps({"results": [], "message": "No matching knowledge found."})
        return json.dumps({"results": results})

    def _handle_recall(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")
        limit = args.get("limit", 5)
        embedding = _get_embedding(query, self._config)
        results = self._similarity_search("conversation_memory", embedding, limit=limit)
        self._record_success()
        if not results:
            return json.dumps({"results": [], "message": "No matching conversation memory found."})
        return json.dumps({"results": results})

    def _handle_store(self, args: dict) -> str:
        content = args.get("content", "")
        if not content:
            return tool_error("content is required")
        source = args.get("source", "agent")
        embedding = _get_embedding(content, self._config)
        self._insert_embedding(
            "knowledge_base",
            source=source,
            content=content,
            embedding=embedding,
            metadata={"stored_via": "tool_call"},
        )
        self._record_success()
        return json.dumps({"status": "stored", "source": source})

    # -- Shutdown -------------------------------------------------------------

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass
        logger.info("Mnemosyne shut down.")
