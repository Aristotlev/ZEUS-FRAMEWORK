#!/usr/bin/env python3
"""Start the Hermes user-owned PostgreSQL cluster"""

import subprocess
import sys
import os

PGDATA = os.path.expanduser("~/pgdata")
PG_BIN = "/usr/lib/postgresql/16/bin/pg_ctl"

result = subprocess.run(
    [PG_BIN, "-D", PGDATA, "-l", os.path.join(PGDATA, "logfile"), "start"],
    capture_output=True, text=True
)

if result.returncode == 0:
    print("PostgreSQL cluster started on port 5433")
else:
    print(f"Failed to start: {result.stderr}", file=sys.stderr)
    sys.exit(1)
