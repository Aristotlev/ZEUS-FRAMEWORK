-- ============================================================================
-- Zeus Framework — pgvector initialisation
-- Runs automatically via docker-compose healthcheck on postgres startup.
-- Idempotent: safe to run multiple times.
-- ============================================================================

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ── L3 Semantic memory tables ────────────────────────────────────────────────

-- Conversation memory — episodic recall across sessions
CREATE TABLE IF NOT EXISTS conversation_memory (
    id          SERIAL PRIMARY KEY,
    source      TEXT,
    content     TEXT,
    embedding   vector(1536),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Knowledge base — long-term semantic knowledge
CREATE TABLE IF NOT EXISTS knowledge_base (
    id          SERIAL PRIMARY KEY,
    source      TEXT,
    content     TEXT,
    embedding   vector(1536),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes for fast ANN search ───────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS conversation_memory_embedding_idx
    ON conversation_memory USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS knowledge_base_embedding_idx
    ON knowledge_base USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
