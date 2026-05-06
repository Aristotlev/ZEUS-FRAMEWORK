"""
Pending-publish queue for the Zeus content pipeline.

The pipeline used to block 2-6 minutes per run waiting for Publer to confirm
that scheduled posts went live. That meant every cron firing held its slot for
~7 minutes when the actual generation work was done in <1 minute.

This queue lets the pipeline schedule posts and exit immediately. A separate
watcher (`scripts/publish_watcher.py`, designed to run from cron every 1-2 min
or as a long-running daemon) polls the queue, captures live post URLs, patches
Notion, writes the final ledger row, and sends the "posts live" email.

State model:
    queue file: ~/.hermes/zeus_publish_queue.jsonl   (one row per pending run)
    archive file: ~/.hermes/zeus_publish_done.jsonl  (rows the watcher has resolved)

A row in the queue is the full ContentPiece payload (via to_dict / from_dict)
plus enqueue timestamp + max_wait_until deadline. The watcher rewrites the
queue file each pass, omitting resolved rows.
"""
from __future__ import annotations

import json
import os
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .content_types import ContentPiece

QUEUE_PATH = Path(os.path.expanduser("~/.hermes/zeus_publish_queue.jsonl"))
ARCHIVE_PATH = Path(os.path.expanduser("~/.hermes/zeus_publish_done.jsonl"))


def _piece_to_dict(piece: ContentPiece) -> dict:
    """Serialize a ContentPiece for the watcher. Drops binary refs but keeps URLs / paths."""
    return {
        "content_type": piece.content_type.value,
        "title": piece.title,
        "body": piece.body,
        "topic": piece.topic,
        "audio_mode": piece.audio_mode.value if piece.audio_mode else None,
        "images": [
            {"url": a.url, "kind": a.kind, "width": a.width, "height": a.height,
             "duration_s": a.duration_s, "model": a.model, "cost_usd": a.cost_usd,
             "local_path": a.local_path}
            for a in piece.images
        ],
        "video": (
            {"url": piece.video.url, "kind": piece.video.kind,
             "width": piece.video.width, "height": piece.video.height,
             "duration_s": piece.video.duration_s, "model": piece.video.model,
             "cost_usd": piece.video.cost_usd, "local_path": piece.video.local_path}
            if piece.video else None
        ),
        "created_at": piece.created_at.isoformat(),
        "posted_at": piece.posted_at.isoformat() if piece.posted_at else None,
        "publer_job_ids": dict(piece.publer_job_ids),
        "notion_page_id": piece.notion_page_id,
        "notion_pipeline_page_id": piece.notion_pipeline_page_id,
        "run_id": piece.run_id,
        "local_artifact_dir": piece.local_artifact_dir,
        "phase_durations_ms": dict(piece.phase_durations_ms),
        "status": piece.status,
        "cost_breakdown": dict(piece.cost_breakdown),
        "cost_sources": dict(piece.cost_sources),
    }


def _piece_from_dict(d: dict) -> ContentPiece:
    """Hydrate a ContentPiece from a queue row."""
    from .content_types import AudioMode, ContentType, GeneratedAsset

    piece = ContentPiece(
        content_type=ContentType(d["content_type"]),
        title=d.get("title") or "",
        body=d.get("body") or "",
        topic=d.get("topic") or "",
        audio_mode=AudioMode(d["audio_mode"]) if d.get("audio_mode") else None,
    )
    piece.images = [
        GeneratedAsset(
            url=a["url"], kind=a.get("kind", "image"),
            width=a.get("width"), height=a.get("height"),
            duration_s=a.get("duration_s"), model=a.get("model") or "",
            cost_usd=float(a.get("cost_usd") or 0), local_path=a.get("local_path"),
        )
        for a in d.get("images") or []
    ]
    if d.get("video"):
        v = d["video"]
        piece.video = GeneratedAsset(
            url=v["url"], kind=v.get("kind", "video"),
            width=v.get("width"), height=v.get("height"),
            duration_s=v.get("duration_s"), model=v.get("model") or "",
            cost_usd=float(v.get("cost_usd") or 0), local_path=v.get("local_path"),
        )
    if d.get("created_at"):
        try:
            piece.created_at = datetime.fromisoformat(d["created_at"])
        except ValueError:
            pass
    if d.get("posted_at"):
        try:
            piece.posted_at = datetime.fromisoformat(d["posted_at"])
        except ValueError:
            pass
    piece.publer_job_ids = dict(d.get("publer_job_ids") or {})
    piece.notion_page_id = d.get("notion_page_id")
    piece.notion_pipeline_page_id = d.get("notion_pipeline_page_id")
    piece.run_id = d.get("run_id") or piece.run_id
    piece.local_artifact_dir = d.get("local_artifact_dir")
    piece.phase_durations_ms = dict(d.get("phase_durations_ms") or {})
    piece.status = d.get("status") or "scheduled"
    piece.cost_breakdown = {k: float(v) for k, v in (d.get("cost_breakdown") or {}).items()}
    piece.cost_sources = dict(d.get("cost_sources") or {})
    return piece


def enqueue(piece: ContentPiece, *, max_wait_s: int = 86400) -> None:
    """Append a pending-publish row for `piece`. Watcher resolves within max_wait_s.

    Default 24h: Publer's actual publish-to-platform latency varies from
    seconds to many minutes depending on feed-spacing config, content type,
    and per-platform throttles. The watcher only finalises a run when nothing
    is left pending, so this is a hard "give up entirely" cutoff, not a
    "first poll where we might lose URLs" knob.
    """
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=max_wait_s)).isoformat()
    row = {
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "deadline": deadline,
        "piece": _piece_to_dict(piece),
    }
    with QUEUE_PATH.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def read_pending() -> list[dict]:
    """Return all queued rows. Caller decides which to resolve this pass."""
    if not QUEUE_PATH.exists():
        return []
    out: list[dict] = []
    with QUEUE_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def rewrite_queue(remaining: list[dict]) -> None:
    """Atomically replace the queue with `remaining` (rows still pending)."""
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(QUEUE_PATH.suffix + ".tmp")
    with tmp.open("w") as fh:
        for r in remaining:
            fh.write(json.dumps(r, default=str) + "\n")
    os.replace(tmp, QUEUE_PATH)


def archive_done(rows: list[dict]) -> None:
    """Append resolved rows to the archive log so we have a forensic trail."""
    if not rows:
        return
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ARCHIVE_PATH.open("a") as fh:
        for r in rows:
            fh.write(json.dumps({**r, "resolved_at": datetime.now(timezone.utc).isoformat()},
                                default=str) + "\n")


def hydrate(row: dict) -> tuple[ContentPiece, dict]:
    """Convert a queue row back to (ContentPiece, queue_metadata)."""
    piece = _piece_from_dict(row.get("piece") or {})
    meta = {k: v for k, v in row.items() if k != "piece"}
    return piece, meta


def is_past_deadline(row: dict, now: Optional[datetime] = None) -> bool:
    deadline_s = row.get("deadline")
    if not deadline_s:
        return False
    try:
        deadline = datetime.fromisoformat(deadline_s)
    except ValueError:
        return False
    return (now or datetime.now(timezone.utc)) > deadline
