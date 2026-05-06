"""Zeus status dashboard, health endpoint, remote trigger, and webhook receiver.

Reads pipeline state from the read-only mount of /opt/data and triggers runs
by `docker exec` into the zeus-agent container. Authentication is a single
Bearer token (ZEUS_TRIGGER_TOKEN) shared by /trigger and /webhook.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

HERMES_HOME = Path(os.getenv("HERMES_HOME", "/opt/data"))
LEDGER = HERMES_HOME / "zeus_cost_ledger.jsonl"
QUEUE = HERMES_HOME / "zeus_publish_queue.jsonl"
DONE = HERMES_HOME / "zeus_publish_done.jsonl"
HEARTBEAT = HERMES_HOME / ".heartbeat"
TRIGGER_TOKEN = os.getenv("ZEUS_TRIGGER_TOKEN", "")
ZEUS_CONTAINER = os.getenv("ZEUS_CONTAINER", "zeus-agent")
# Pipeline modules live in the hermes venv (requests, pydantic, openai, ...).
# A bare `python3` only sees the system site-packages and dies on `import requests`.
ZEUS_AGENT_PYTHON = os.getenv("ZEUS_AGENT_PYTHON", "/opt/hermes/.venv/bin/python")
# Run the trigger as the hermes user so $HOME resolves to HERMES_HOME and any
# files written (logs, ledger, jobs.json) get the right ownership. Plain
# `docker exec` defaults to root, which has historically poisoned cron/jobs.json.
ZEUS_AGENT_USER = os.getenv("ZEUS_AGENT_USER", "hermes")

app = FastAPI(title="Zeus Status")


def _require_token(authorization: str | None) -> None:
    if not TRIGGER_TOKEN:
        raise HTTPException(503, "ZEUS_TRIGGER_TOKEN not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    given = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(given, TRIGGER_TOKEN):
        raise HTTPException(401, "bad token")


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit:
        rows = rows[-limit:]
    return rows


def _ledger_window(rows: list[dict], since: datetime) -> tuple[float, int]:
    cost = 0.0
    n = 0
    for r in rows:
        ts = r.get("ts")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t < since:
            continue
        if r.get("status", "").startswith("checkpoint:"):
            continue
        cost += float(r.get("total_cost_usd", 0) or 0)
        n += 1
    return cost, n


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    """Liveness probe — fresh heartbeat = cron is running."""
    if not HEARTBEAT.exists():
        raise HTTPException(503, "no heartbeat")
    age = datetime.now().timestamp() - HEARTBEAT.stat().st_mtime
    if age > 1800:  # 30 min
        raise HTTPException(503, f"heartbeat stale ({int(age)}s)")
    return f"ok ({int(age)}s)"


@app.get("/status")
def status() -> dict:
    rows = _read_jsonl(LEDGER)
    now = datetime.now(timezone.utc)
    h24 = now - timedelta(hours=24)
    d7 = now - timedelta(days=7)
    d30 = now - timedelta(days=30)
    queue_rows = _read_jsonl(QUEUE)
    done_rows = _read_jsonl(DONE, limit=20)

    cost_24h, n_24h = _ledger_window(rows, h24)
    cost_7d, n_7d = _ledger_window(rows, d7)
    cost_30d, n_30d = _ledger_window(rows, d30)

    heartbeat_age = None
    if HEARTBEAT.exists():
        heartbeat_age = int(now.timestamp() - HEARTBEAT.stat().st_mtime)

    final_rows = [r for r in rows if not r.get("status", "").startswith("checkpoint:")]
    recent = final_rows[-10:][::-1]

    return {
        "heartbeat_age_seconds": heartbeat_age,
        "cost": {
            "last_24h_usd": round(cost_24h, 4),
            "last_7d_usd": round(cost_7d, 4),
            "last_30d_usd": round(cost_30d, 4),
            "runs_24h": n_24h,
            "runs_7d": n_7d,
            "runs_30d": n_30d,
        },
        "queue_pending": len(queue_rows),
        "recent_runs": [
            {
                "ts": r.get("ts"),
                "type": r.get("content_type"),
                "title": r.get("title"),
                "status": r.get("status"),
                "cost": r.get("total_cost_usd"),
                "platforms": r.get("platforms"),
            }
            for r in recent
        ],
        "recent_published": done_rows[-5:][::-1],
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    s = status()
    cost = s["cost"]
    rows_html = "".join(
        f"<tr><td>{(r.get('ts') or '')[:19]}</td><td>{r.get('type') or '-'}</td>"
        f"<td>{(r.get('title') or '')[:60]}</td><td>{r.get('status') or '-'}</td>"
        f"<td>${(r.get('cost') or 0):.4f}</td></tr>"
        for r in s["recent_runs"]
    )
    hb = s["heartbeat_age_seconds"]
    hb_class = "ok" if (hb is not None and hb < 1800) else "bad"
    hb_text = f"{hb}s ago" if hb is not None else "never"
    return f"""<!doctype html>
<html><head><title>Zeus</title>
<style>
body{{font-family:ui-monospace,monospace;background:#0b0d10;color:#e6e6e6;margin:0;padding:24px;max-width:1100px;margin:auto}}
h1{{color:#ffd866;margin:0 0 8px}}
.bar{{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0;padding:16px;background:#161a1f;border-radius:6px}}
.k{{font-size:11px;text-transform:uppercase;color:#888}}
.v{{font-size:20px;font-weight:600}}
.ok{{color:#7fcf6e}} .bad{{color:#ff6b6b}}
table{{width:100%;border-collapse:collapse;margin-top:16px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #222;font-size:13px}}
th{{color:#888;font-weight:500;text-transform:uppercase;font-size:11px}}
</style></head>
<body>
<h1>⚡ Zeus</h1>
<div class="bar">
  <div><div class="k">Heartbeat</div><div class="v {hb_class}">{hb_text}</div></div>
  <div><div class="k">Queue</div><div class="v">{s['queue_pending']}</div></div>
  <div><div class="k">Cost 24h</div><div class="v">${cost['last_24h_usd']:.2f}</div></div>
  <div><div class="k">Cost 7d</div><div class="v">${cost['last_7d_usd']:.2f}</div></div>
  <div><div class="k">Cost 30d</div><div class="v">${cost['last_30d_usd']:.2f}</div></div>
  <div><div class="k">Runs 24h</div><div class="v">{cost['runs_24h']}</div></div>
</div>
<h3>Recent runs</h3>
<table><tr><th>When</th><th>Type</th><th>Title</th><th>Status</th><th>Cost</th></tr>{rows_html}</table>
<p style="color:#666;font-size:11px;margin-top:24px">JSON: <a href="/status" style="color:#888">/status</a> · health: <a href="/health" style="color:#888">/health</a></p>
</body></html>"""


@app.post("/trigger/{content_type}")
def trigger(
    content_type: str,
    topic: str | None = None,
    auto: int = 0,
    publish: int = 1,
    authorization: str | None = Header(None),
) -> dict:
    """Kick off a pipeline run. Bearer-auth required.

    Provide either `topic=...` (explicit headline) or `auto=1` (pick from
    content_pipeline.niche). `publish=0` archives only (default publishes).
    """
    _require_token(authorization)
    valid = {"article", "long_article", "carousel", "short_video", "long_video"}
    if content_type not in valid:
        raise HTTPException(400, f"invalid content_type, expected one of {sorted(valid)}")
    if not topic and not auto:
        raise HTTPException(400, "provide ?topic=... or ?auto=1")
    if topic and auto:
        raise HTTPException(400, "topic and auto are mutually exclusive")
    cmd = [
        "docker", "exec", "-d", "-u", ZEUS_AGENT_USER, ZEUS_CONTAINER,
        ZEUS_AGENT_PYTHON,
        "/opt/zeus/skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/pipeline_test.py",
        "--type", content_type,
    ]
    cmd += ["--auto"] if auto else ["--topic", topic]
    if publish:
        cmd.append("--publish")
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, f"docker exec failed: {e.stderr.decode()[:300]}")
    return {"queued": True, "type": content_type, "topic": topic, "auto": bool(auto), "publish": bool(publish)}


@app.post("/webhook/{source}")
async def webhook(source: str, request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    """Generic webhook landing pad. Auth-gated. Logs to /opt/data/webhooks/<source>.jsonl."""
    _require_token(authorization)
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")}
    log_dir = HERMES_HOME / "webhooks"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / f"{source}.jsonl").open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "payload": payload}) + "\n")
    except OSError:
        # Read-only mount in some setups — fail soft
        pass
    return JSONResponse({"received": True, "source": source})
