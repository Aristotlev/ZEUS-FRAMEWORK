#!/usr/bin/env python3
"""
Hermes Unstoppable Stack — Vector Store + Cache Interface
Provides unified access to Redis (caching) and pgvector (semantic search)
"""

import redis
import psycopg2
import psycopg2.extras
import numpy as np
import json
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

# Connection settings
REDIS_URL = "redis://127.0.0.1:6379"
PG_URL = {
    "host": "127.0.0.1",
    "port": 5433,
    "database": "hermes_vectors",
    "user": "hermes",
    "password": "your_db_password"
}

class HermesStack:
    """Unified interface for Redis + pgvector"""
    
    def __init__(self):
        self.redis = redis.from_url(REDIS_URL)
        self._pg_config = PG_URL
        
    @contextmanager
    def pg(self):
        """Context manager for PostgreSQL connections"""
        conn = psycopg2.connect(**self._pg_config)
        try:
            yield conn
        finally:
            conn.close()
    
    # === Redis Operations ===
    
    def cache_get(self, key: str) -> Optional[str]:
        """Get cached value"""
        return self.redis.get(key)
    
    def cache_set(self, key: str, value: str, ttl: int = 3600) -> bool:
        """Set cached value with TTL"""
        return self.redis.setex(key, ttl, value)
    
    def cache_delete(self, key: str) -> bool:
        """Delete cached value"""
        return self.redis.delete(key) > 0
    
    def session_store(self, session_id: str, data: Dict) -> bool:
        """Store session data in Redis hash"""
        return self.redis.hset(f"session:{session_id}", mapping=data)
    
    def session_get(self, session_id: str) -> Dict:
        """Get session data from Redis hash"""
        return self.redis.hgetall(f"session:{session_id}")
    
    # === Vector Operations ===
    
    def insert_embedding(self, table: str, source: str, content: str, 
                         embedding: List[float], metadata: Dict = None) -> int:
        """Insert embedding into pgvector table"""
        with self.pg() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                INSERT INTO {table} (source, content, embedding, metadata)
                VALUES (%s, %s, %s::vector, %s::jsonb)
                RETURNING id
            """, (source, content, json.dumps(embedding), 
                  json.dumps(metadata or {})))
            row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
    
    def similarity_search(self, table: str, embedding: List[float], 
                          limit: int = 5, threshold: float = 0.7) -> List[Dict]:
        """Find similar vectors using cosine distance"""
        vec_str = json.dumps(embedding)
        with self.pg() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT id, source, content, metadata,
                       1 - (embedding <=> %s::vector) as similarity
                FROM {table}
                WHERE 1 - (embedding <=> %s::vector) > %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, vec_str, threshold, vec_str, limit))
            
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": row[0],
                    "source": row[1],
                    "content": row[2],
                    "metadata": row[3],
                    "similarity": float(row[4])
                })
            return results
    
    def store_memory(self, session_id: str, role: str, content: str,
                     embedding: List[float], metadata: Dict = None) -> int:
        """Store conversation memory with embedding"""
        return self.insert_embedding(
            "conversation_memory", session_id, content, 
            embedding, {"role": role, **(metadata or {})}
        )
    
    def recall_memory(self, query_embedding: List[float], 
                      session_id: str = None, limit: int = 5) -> List[Dict]:
        """Recall relevant memories by similarity"""
        vec_str = json.dumps(query_embedding)
        with self.pg() as conn:
            cur = conn.cursor()
            if session_id:
                cur.execute("""
                    SELECT id, source, content, metadata,
                           1 - (embedding <=> %s::vector) as similarity
                    FROM conversation_memory
                    WHERE source = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (vec_str, session_id, vec_str, limit))
            else:
                cur.execute("""
                    SELECT id, source, content, metadata,
                           1 - (embedding <=> %s::vector) as similarity
                    FROM conversation_memory
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (vec_str, vec_str, limit))
            
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": row[0],
                    "session_id": row[1],
                    "content": row[2],
                    "metadata": row[3],
                    "similarity": float(row[4])
                })
            return results
    
    # === Knowledge Base ===
    
    def store_knowledge(self, topic: str, content: str, 
                        embedding: List[float], metadata: Dict = None) -> int:
        """Store knowledge entry"""
        return self.insert_embedding(
            "knowledge_base", topic, content, embedding, metadata
        )
    
    def query_knowledge(self, query_embedding: List[float], 
                        topic: str = None, limit: int = 5) -> List[Dict]:
        """Query knowledge base by similarity"""
        vec_str = json.dumps(query_embedding)
        with self.pg() as conn:
            cur = conn.cursor()
            if topic:
                cur.execute("""
                    SELECT id, source, content, metadata,
                           1 - (embedding <=> %s::vector) as similarity
                    FROM knowledge_base
                    WHERE source = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (vec_str, topic, vec_str, limit))
            else:
                cur.execute("""
                    SELECT id, source, content, metadata,
                           1 - (embedding <=> %s::vector) as similarity
                    FROM knowledge_base
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (vec_str, vec_str, limit))
            
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": row[0],
                    "topic": row[1],
                    "content": row[2],
                    "metadata": row[3],
                    "similarity": float(row[4])
                })
            return results
    
    # === Health Check ===
    
    def health_check(self) -> Dict[str, Any]:
        """Check health of all services"""
        status = {"redis": False, "postgres": False, "pgvector": False}
        
        try:
            self.redis.ping()
            status["redis"] = True
        except:
            pass
        
        try:
            with self.pg() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                status["postgres"] = True
                
                cur.execute("SELECT extname FROM pg_extension WHERE extname='vector'")
                if cur.fetchone():
                    status["pgvector"] = True
        except:
            pass
        
        return status


# Singleton instance
_stack = None

def get_stack() -> HermesStack:
    """Get or create the stack singleton"""
    global _stack
    if _stack is None:
        _stack = HermesStack()
    return _stack


if __name__ == "__main__":
    # Test the stack
    stack = get_stack()
    health = stack.health_check()
    print(f"Redis: {'OK' if health['redis'] else 'DOWN'}")
    print(f"PostgreSQL: {'OK' if health['postgres'] else 'DOWN'}")
    print(f"pgvector: {'OK' if health['pgvector'] else 'DOWN'}")
    
    if all(health.values()):
        print("\nUNSTOPPABLE.")
