# PostgreSQL + pgvector Setup (No sudo)

This guide sets up a user-owned PostgreSQL cluster with pgvector extension,
without requiring root/sudo access.

## Quick Setup

```bash
# Install PostgreSQL 16 (if not available, use user-owned build)
# Create a user-owned cluster
initdb -D ~/pgdata

# Configure port (avoid system PG on 5432)
echo "port = 5433" >> ~/pgdata/postgresql.conf

# Start the cluster
pg_ctl -D ~/pgdata -l ~/pgdata/logfile start

# Create database and user
psql -p 5433 -c "CREATE USER hermes WITH PASSWORD 'your_password_here';"
psql -p 5433 -c "CREATE DATABASE hermes_vectors OWNER hermes;"
psql -p 5433 -d hermes_vectors -c "CREATE EXTENSION vector;"

# Create tables
psql -p 5433 -d hermes_vectors -c "
CREATE TABLE conversation_memory (
    id SERIAL PRIMARY KEY,
    source TEXT,
    content TEXT,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);"

psql -p 5433 -d hermes_vectors -c "
CREATE TABLE knowledge_base (
    id SERIAL PRIMARY KEY,
    source TEXT,
    content TEXT,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);"
```

## Auto-start on Login

Add to `~/.bashrc`:
```bash
# Start PostgreSQL cluster if not running
if ! pg_isready -p 5433 -q 2>/dev/null; then
    pg_ctl -D ~/pgdata -l ~/pgdata/logfile start 2>/dev/null
fi
```

Or use the provided start script:
```bash
cp scripts/start_hermes_pg.sh ~/
# Add to .bashrc: ~/start_hermes_pg.sh
```

## Verification

```bash
python3 -c "
from stack.hermes_stack import get_stack
s = get_stack()
print(s.health_check())
"
# Should show: {'redis': True, 'postgres': True, 'pgvector': True}
```
