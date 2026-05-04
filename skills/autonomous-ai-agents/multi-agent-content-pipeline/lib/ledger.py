"""
Persistent cost ledger for Zeus content pipeline.

Append-only JSONL at ~/.hermes/zeus_cost_ledger.jsonl. Every pipeline run writes one
row. The "always-on cost analysis" the user wants in every notification email pulls
from here — current run, today, last 7 days, last 30 days.

Schema (one JSON object per line):
  {
    "ts": "2026-05-04T17:55:11.123456",
    "content_type": "article",
    "topic": "Bitcoin breaks 100K",
    "title": "...",
    "status": "posted",
    "total_cost_usd": 0.052,
    "cost_breakdown": {"text:google/gemini-2.5-flash": 0.001, "image:gpt-image-2": 0.04, ...},
    "models": ["google/gemini-2.5-flash", "gpt-image-2"],
    "platforms": ["twitter", "instagram", ...],
    "publer_job_ids": {"twitter": "abc", ...},
    "notion_page_id": "..."
  }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .content_types import ContentPiece

LEDGER_PATH = Path(os.path.expanduser("~/.hermes/zeus_cost_ledger.jsonl"))


def append_entry(piece: ContentPiece) -> dict:
    """Append a row for `piece` to the ledger. Returns the row that was written."""
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.utcnow().isoformat(),
        "content_type": piece.content_type.value,
        "topic": piece.topic,
        "title": piece.title,
        "status": piece.status,
        "total_cost_usd": piece.total_cost,
        "cost_breakdown": dict(piece.cost_breakdown),
        "models": piece.models_used,
        "platforms": piece.target_platforms,
        "publer_job_ids": dict(piece.publer_job_ids),
        "notion_page_id": piece.notion_page_id,
    }
    with LEDGER_PATH.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _read_all() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    out: list[dict] = []
    with LEDGER_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def summary(window_days: Optional[int] = None) -> dict:
    """
    Summarize ledger over the last `window_days` (None = all-time).
    Returns: {total_usd, runs, by_type: {...}, by_model: {...}, top_topics: [...]}
    """
    rows = _read_all()
    if window_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=window_days)
        rows = [r for r in rows if _parse_ts(r.get("ts")) >= cutoff]

    total = sum(float(r.get("total_cost_usd") or 0) for r in rows)
    by_type: dict[str, dict] = {}
    by_model: dict[str, float] = {}
    for r in rows:
        ct = r.get("content_type", "unknown")
        by_type.setdefault(ct, {"runs": 0, "cost_usd": 0.0})
        by_type[ct]["runs"] += 1
        by_type[ct]["cost_usd"] += float(r.get("total_cost_usd") or 0)
        for k, v in (r.get("cost_breakdown") or {}).items():
            model = k.split(":", 1)[1] if ":" in k else k
            by_model[model] = by_model.get(model, 0.0) + float(v or 0)

    return {
        "window_days": window_days,
        "runs": len(rows),
        "total_cost_usd": round(total, 4),
        "by_type": {k: {"runs": v["runs"], "cost_usd": round(v["cost_usd"], 4)} for k, v in by_type.items()},
        "by_model": {k: round(v, 4) for k, v in sorted(by_model.items(), key=lambda x: -x[1])},
    }


def _parse_ts(ts: str | None) -> datetime:
    if not ts:
        return datetime.min
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.min
