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
    """Append the FINAL row for `piece` to the ledger. Returns the row that was written."""
    return _write_row(piece, status_override=None)


def append_checkpoint(piece: ContentPiece, phase: str) -> dict:
    """
    Append a CHECKPOINT row right after a paid step (fal image/video, etc.) so the
    cost survives even if the pipeline later crashes (Notion failure, JSON decode
    failure, network drop). Final row from append_entry will share the same run_id
    and supersede earlier checkpoints in summary().
    """
    return _write_row(piece, status_override=f"checkpoint:{phase}")


def _write_row(piece: ContentPiece, status_override: Optional[str]) -> dict:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.utcnow().isoformat(),
        "run_id": getattr(piece, "run_id", None),
        "content_type": piece.content_type.value,
        "topic": piece.topic,
        "title": piece.title,
        "status": status_override if status_override is not None else piece.status,
        "total_cost_usd": piece.total_cost,
        "cost_breakdown": dict(piece.cost_breakdown),
        "cost_sources": dict(getattr(piece, "cost_sources", {}) or {}),
        "models": piece.models_used,
        "platforms": piece.target_platforms,
        "publer_job_ids": dict(piece.publer_job_ids),
        "notion_page_id": piece.notion_page_id,
        "artifact_dir": getattr(piece, "local_artifact_dir", None),
        "asset_urls": [a.url for a in piece.images] + ([piece.video.url] if piece.video and piece.video.url else []),
        "asset_local_paths": [a.local_path for a in piece.images if a.local_path]
        + ([piece.video.local_path] if piece.video and piece.video.local_path else []),
        "phase_durations_ms": dict(getattr(piece, "phase_durations_ms", {}) or {}),
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

    Dedupe semantics: rows are grouped by run_id. If a final row exists for a run
    (status not starting with 'checkpoint:'), it supersedes any checkpoint rows for
    the same run. If only checkpoint rows exist, the LATEST checkpoint represents
    the leaked spend for that run and is counted toward total_cost_usd but tagged
    as 'leaked' (not a completed run).

    Rows from before run_id was introduced (no run_id field) are treated as their
    own group keyed by ts so the historical ledger still parses cleanly.

    Returns: {total_usd, runs, leaked_runs, leaked_cost_usd, by_type, by_model}
    """
    rows = _read_all()
    if window_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=window_days)
        rows = [r for r in rows if _parse_ts(r.get("ts")) >= cutoff]

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        key = r.get("run_id") or f"_legacy:{r.get('ts')}"
        grouped.setdefault(key, []).append(r)

    chosen: list[tuple[dict, bool]] = []  # (row, is_leaked)
    for run_rows in grouped.values():
        finals = [r for r in run_rows if not str(r.get("status", "")).startswith("checkpoint:")]
        if finals:
            finals.sort(key=lambda r: _parse_ts(r.get("ts")))
            chosen.append((finals[-1], False))
        else:
            run_rows.sort(key=lambda r: _parse_ts(r.get("ts")))
            chosen.append((run_rows[-1], True))

    total = sum(float(r.get("total_cost_usd") or 0) for r, _ in chosen)
    leaked_cost = sum(float(r.get("total_cost_usd") or 0) for r, leaked in chosen if leaked)
    leaked_runs = sum(1 for _, leaked in chosen if leaked)
    completed_runs = len(chosen) - leaked_runs

    actual_total = 0.0
    estimated_total = 0.0
    by_type: dict[str, dict] = {}
    by_model: dict[str, float] = {}
    durations_by_type: dict[str, list[int]] = {}
    durations_by_phase: dict[str, list[int]] = {}
    for r, _ in chosen:
        ct = r.get("content_type", "unknown")
        by_type.setdefault(ct, {"runs": 0, "cost_usd": 0.0})
        by_type[ct]["runs"] += 1
        by_type[ct]["cost_usd"] += float(r.get("total_cost_usd") or 0)
        sources = r.get("cost_sources") or {}
        for k, v in (r.get("cost_breakdown") or {}).items():
            model = k.split(":", 1)[1] if ":" in k else k
            by_model[model] = by_model.get(model, 0.0) + float(v or 0)
            if sources.get(k) == "actual":
                actual_total += float(v or 0)
            else:
                estimated_total += float(v or 0)
        phases = r.get("phase_durations_ms") or {}
        if phases:
            total_ms = sum(int(v or 0) for v in phases.values())
            durations_by_type.setdefault(ct, []).append(total_ms)
            for phase, ms in phases.items():
                durations_by_phase.setdefault(phase, []).append(int(ms or 0))

    accuracy_pct = round((actual_total / total) * 100, 1) if total > 0 else 100.0

    def _percentile(values: list[int], p: float) -> int:
        if not values:
            return 0
        s = sorted(values)
        idx = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
        return s[idx]

    timing_by_type: dict[str, dict] = {}
    for ct, vals in durations_by_type.items():
        timing_by_type[ct] = {
            "runs_with_timing": len(vals),
            "p50_s": round(_percentile(vals, 50) / 1000.0, 2),
            "p90_s": round(_percentile(vals, 90) / 1000.0, 2),
            "max_s": round(max(vals) / 1000.0, 2),
        }
    timing_by_phase: dict[str, dict] = {}
    for phase, vals in durations_by_phase.items():
        timing_by_phase[phase] = {
            "samples": len(vals),
            "p50_s": round(_percentile(vals, 50) / 1000.0, 2),
            "p90_s": round(_percentile(vals, 90) / 1000.0, 2),
        }

    return {
        "window_days": window_days,
        "runs": completed_runs,
        "leaked_runs": leaked_runs,
        "total_cost_usd": round(total, 4),
        "leaked_cost_usd": round(leaked_cost, 4),
        "actual_cost_usd": round(actual_total, 4),
        "estimated_cost_usd": round(estimated_total, 4),
        "accuracy_pct": accuracy_pct,
        "by_type": {k: {"runs": v["runs"], "cost_usd": round(v["cost_usd"], 4)} for k, v in by_type.items()},
        "by_model": {k: round(v, 4) for k, v in sorted(by_model.items(), key=lambda x: -x[1])},
        "timing_by_type": timing_by_type,
        "timing_by_phase": timing_by_phase,
    }


def incomplete_runs(window_days: Optional[int] = None) -> list[dict]:
    """
    Return ledger rows for runs that look orphaned: either only checkpoint rows
    were written (process crashed before final), or the final row's status is one
    of failed/partial/media_partial. Caller can use these to recover bytes from
    `artifact_dir` and re-archive to Notion.

    Each entry is the chosen representative row for that run, plus a synthesized
    `incomplete_reason` field: 'leaked' (no final row) or 'failed_status'.
    """
    rows = _read_all()
    if window_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=window_days)
        rows = [r for r in rows if _parse_ts(r.get("ts")) >= cutoff]

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        key = r.get("run_id") or f"_legacy:{r.get('ts')}"
        grouped.setdefault(key, []).append(r)

    bad: dict[str, str] = {"failed", "partial", "media_partial"}
    out: list[dict] = []
    for run_rows in grouped.values():
        finals = [r for r in run_rows if not str(r.get("status", "")).startswith("checkpoint:")]
        if not finals:
            run_rows.sort(key=lambda r: _parse_ts(r.get("ts")))
            row = dict(run_rows[-1])
            row["incomplete_reason"] = "leaked"
            out.append(row)
            continue
        finals.sort(key=lambda r: _parse_ts(r.get("ts")))
        latest = finals[-1]
        if str(latest.get("status", "")) in bad:
            row = dict(latest)
            row["incomplete_reason"] = "failed_status"
            out.append(row)
    out.sort(key=lambda r: _parse_ts(r.get("ts")), reverse=True)
    return out


def _parse_ts(ts: str | None) -> datetime:
    if not ts:
        return datetime.min
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.min
