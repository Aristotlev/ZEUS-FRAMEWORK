---
name: pgvector-no-sudo-setup
description: Set up PostgreSQL + pgvector cluster in home directory without sudo access
version: 1.0
tags: [postgresql, pgvector, no-sudo, vector-database]
---

# PostgreSQL + pgvector Setup Without Sudo

Set up a fully functional PostgreSQL cluster with pgvector extension in a user's home directory — no root/sudo required.

## Trigger
- Need PostgreSQL and/or pgvector but don't have sudo access
- System PG is running but credentials/roles aren't available
- Setting up a dev vector store on a restricted machine

## Steps

### 1. Create a user-owned PG cluster
```bash
/usr/lib/postgresql/<VERSION>/bin/initdb -D ~/pgdata
echo "port = 5433" >> ~/pgdata/postgresql.conf
echo "unix_socket_directories = '/home/$USER/pgdata'" >> ~/pgdata/postgresql.conf
/usr/lib/postgresql/<VERSION>/bin/pg_ctl -D ~/pgdata -l ~/pgdata/logfile start
```

Key: Must set `unix_socket_directories` to a writable path (default `/var/run/postgresql/` is owned by postgres OS user). Use a non-conflicting port (5433 if system PG is on 5432).

### 2. Create user and database
```bash
psql -h ~/pgdata -p 5433 -U $USER -d postgres -c "CREATE USER hermes WITH PASSWORD 'your_db_password';"
psql -h ~/pgdata -p 5433 -U $USER -d postgres -c "CREATE DATABASE hermes_vectors OWNER hermes;"
```

### 3. Install pgvector extension
- Check if already installed system-wide: `ls /usr/lib/postgresql/<VERSION>/lib/vector.so`
- If present, just enable it: `CREATE EXTENSION vector;`
- If not, build from source (requires `postgresql-server-dev-<VERSION>` and `gcc`):
  ```bash
  git clone --branch v0.7.4 https://github.com/pgvector/pgvector.git /tmp/pgvector
  cd /tmp/pgvector && make && sudo make install
  ```
  Without sudo, install to DESTDIR then manually copy .so and extension files to the system PG dirs (only works if those dirs are world-writable, which they usually aren't). The system package is the easier path.

### 4. Create tables and grant permissions
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE conversation_memory (
    id SERIAL PRIMARY KEY,
    source TEXT,
    content TEXT,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE knowledge_base (
    id SERIAL PRIMARY KEY,
    source TEXT,
    content TEXT,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

GRANT ALL ON ALL TABLES IN SCHEMA public TO hermes;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO hermes;
```

### 5. Auto-start on login
Add to `~/.bashrc`:
```bash
if ! pg_isready -h ~/pgdata -p 5433 -q 2>/dev/null; then
    ~/start_hermes_pg.sh >/dev/null 2>&1
fi
```

## Pitfalls

### psycopg2 + pgvector serialization
psycopg2 **cannot** adapt Python `list` or `dict` to pgvector/jsonb. You MUST:
- `json.dumps(embedding)` and cast with `%s::vector` in SQL
- `json.dumps(metadata)` and cast with `%s::jsonb` in SQL

Wrong:
```python
cur.execute("INSERT INTO t (embedding, metadata) VALUES (%s, %s)", (embedding_list, metadata_dict))
# psycopg2.errors: can't adapt type 'list'/'dict'
```

Correct:
```python
cur.execute("INSERT INTO t (embedding, metadata) VALUES (%s::vector, %s::jsonb)", 
            (json.dumps(embedding_list), json.dumps(metadata_dict)))
```

### Table schema consistency
When using a generic `insert_embedding()` method, ensure the table column names match. Using `source` as a universal column (instead of `session_id` or `topic`) simplifies the generic insert and lets `store_memory`/`store_knowledge` pass their identifier as the source argument.

### initdb socket permission error
If you see `FATAL: could not create lock file "/var/run/postgresql/.s.PGSQL.5433.lock": Permission denied`, add `unix_socket_directories` to `postgresql.conf` pointing to a directory you own.

## Verification
```bash
/usr/bin/python3 hermes_stack.py  # Should print: Redis: OK, PostgreSQL: OK, pgvector: OK, UNSTOPPABLE.
```
