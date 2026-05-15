#!/usr/bin/env python3
"""Event-clip watcher — poll the gov/official YouTube allowlist for fresh
uploads and ship the most newsworthy ≤90s soundbite from each one through
the EVENT_CLIP pipeline.

Run once per cron tick (default schedule: every 30 min). Idempotent thanks to
the SQLite seen-table; safe to run more often when debugging.

Per-fire flow:
    iterate EVENT_CLIP_CHANNELS
        -> yt-dlp list uploads in last EVENT_CLIP_LOOKBACK_HOURS
        -> dedupe against ~/.hermes/event_clip_seen.db
        -> for each unseen upload (cap MAX_SHIPS_PER_FIRE per pass):
              lib.event_clip.fetch_and_cut() (yt-dlp + Gemini + ffmpeg)
              build ContentPiece (caption from Gemini hook)
              pipeline_test.run_event_clip(piece) -> Publer + Substack + ledger + email
        -> mark seen (shipped OR skipped) so the next tick doesn't re-process

Hard caps (per-hour, per-day) match the breaking-news watcher pattern so a
runaway loop can't blast Publer.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))
sys.path.insert(0, str(SCRIPT_DIR))


def _load_env_file() -> None:
    for path in (
        "/opt/data/.env",
        os.path.expanduser("~/.hermes/.env"),
        os.path.expanduser("~/.env"),
    ):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            continue


_load_env_file()

from lib import (  # noqa: E402
    ContentPiece,
    ContentType,
    GeneratedAsset,
    EVENT_CLIP_GEMINI_MODEL,
    EventClipError,
    GeminiPickerError,
    SourceTooLong,
    channels_from_env,
    event_clip_fetch_and_cut,
    event_clip_list_fresh_uploads,
    event_clip_lookback_hours,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("event_clip_watch")

DB_PATH = pathlib.Path.home() / ".hermes" / "event_clip_seen.db"
LOCK_PATH = pathlib.Path.home() / ".hermes" / "event_clip_watcher.lock"

# Same shape as breaking-news caps. EVENT_CLIP is heavier per-fire (download
# + ffmpeg + Gemini), so we keep the per-hour cap low to give us cost-control
# headroom while testing.
MAX_SHIPS_PER_FIRE = 1
HARD_CAP_PER_HOUR = 3
HARD_CAP_PER_DAY = 30
DEDUP_RETENTION_DAYS = 14


@contextmanager
def _exclusive_lock() -> Iterator[bool]:
    """Process-level lock so overlapping cron fires can't double-ship.

    Mirrors the breaking-news watcher pattern. fcntl.flock with LOCK_NB so
    a held lock yields False instead of blocking — the in-flight pass owns
    the tick.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            fh.write(str(os.getpid()))
            fh.flush()
        except Exception:
            pass
        yield True
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
    finally:
        try:
            fh.close()
        except Exception:
            pass


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen (
            item_id TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            video_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            shipped INTEGER NOT NULL,
            outcome TEXT,
            seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_seen_at ON seen(seen_at)")
    conn.commit()
    return conn


def _prune_old(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.now(timezone.utc).timestamp() - DEDUP_RETENTION_DAYS * 86400)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    conn.execute("DELETE FROM seen WHERE seen_at < ?", (cutoff_iso,))
    conn.commit()


def _item_id(channel: str, video_id: str) -> str:
    return hashlib.sha1(f"{channel}|{video_id}".encode()).hexdigest()


def _is_seen(conn: sqlite3.Connection, item_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen WHERE item_id = ?", (item_id,)).fetchone()
    return row is not None


def _mark_seen(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    channel: str,
    video_id: str,
    title: str,
    url: str,
    shipped: bool,
    outcome: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO seen "
        "(item_id, channel, video_id, title, url, shipped, outcome, seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item_id, channel, video_id, title, url,
            1 if shipped else 0, outcome,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def _shipped_count_since(conn: sqlite3.Connection, seconds_ago: int) -> int:
    cutoff = (datetime.now(timezone.utc).timestamp() - seconds_ago)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM seen WHERE shipped = 1 AND seen_at >= ?",
        (cutoff_iso,),
    ).fetchone()
    return int(row[0] or 0)


# ---------------------------------------------------------------------------
# Caption builder — turn Gemini's hook + transcript into a ship-ready body.
# Stays under the platform char caps via the existing platforms.LIMITS dict;
# unified-caption rule means we don't generate per-platform variants.
# ---------------------------------------------------------------------------
def _format_caption(*, hook: str, transcript: str, source_title: str) -> tuple[str, str]:
    """Returns (title, body). Title is the Gemini hook; body is the soundbite
    quote prefixed with "Flash News:" + 2-3 semantic emojis, capped at 270c
    to stay inside the X long-post path even on Premium tier."""
    hook = (hook or "").strip().strip('"').strip("'")
    transcript = (transcript or "").strip()

    # Pick 2-3 semantic emojis (matches the ARTICLE convention).
    emojis = "🚨📢"
    if any(k in (hook + transcript).lower() for k in ("rate cut", "cut rates", "rally", "surge", "jump")):
        emojis = "🚨📈"
    elif any(k in (hook + transcript).lower() for k in ("rate hike", "hike", "slump", "crash", "fall", "drop")):
        emojis = "🚨📉"
    elif any(k in (hook + transcript).lower() for k in ("sanction", "tariff", "ban", "investigation")):
        emojis = "🚨⚠️"

    # The hook is the lede. The transcript is a pull-quote if it fits cleanly.
    body = f"Flash News: {emojis} {hook}"
    if transcript and len(body) + 4 + len(transcript) <= 270:
        body = f'{body}\n\n"{transcript}"'
    body = body[:270].rstrip()

    title = hook[:200] or source_title[:200]
    return title, body


# ---------------------------------------------------------------------------
# One end-to-end ship for a single channel upload.
# ---------------------------------------------------------------------------
def _ship_one_upload(upload, *, dry_run: bool) -> dict:
    """fetch_and_cut → build piece → run_event_clip. Returns a summary dict."""
    summary = {
        "title": upload.title,
        "url": upload.url,
        "channel": upload.channel_url,
        "shipped": False,
        "outcome": "",
    }

    work_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"event_clip_{upload.video_id}_"))
    try:
        fetched = event_clip_fetch_and_cut(upload, work_dir=work_dir)
    except SourceTooLong as e:
        summary["outcome"] = f"skipped:too_long:{e}"
        return summary
    except GeminiPickerError as e:
        summary["outcome"] = f"skipped:gemini:{e}"
        return summary
    except EventClipError as e:
        summary["outcome"] = f"skipped:event_clip:{e}"
        return summary

    title, body = _format_caption(
        hook=fetched.pick.hook,
        transcript=fetched.pick.transcript,
        source_title=upload.title,
    )

    piece = ContentPiece(
        content_type=ContentType.EVENT_CLIP,
        title=title,
        body=body,
        topic=upload.title,
    )
    piece.source_video_url = upload.url
    piece.local_artifact_dir = str(work_dir)

    piece.video = GeneratedAsset(
        url="",
        kind="video",
        width=1920,
        height=1080,
        duration_s=fetched.assets.duration_s,
        model="event_clip:ffmpeg",
        cost_usd=0.0,
        local_path=fetched.assets.landscape_path,
    )
    piece.video_vertical = GeneratedAsset(
        url="",
        kind="video",
        width=1080,
        height=1920,
        duration_s=fetched.assets.duration_s,
        model="event_clip:ffmpeg",
        cost_usd=0.0,
        local_path=fetched.assets.vertical_path,
    )

    for model, usd in fetched.cost_breakdown.items():
        piece.add_cost(
            model, usd, kind="audio",
            source=fetched.cost_sources.get(model, "estimate"),
        )

    if dry_run:
        summary["outcome"] = "dry_run"
        summary["title"] = title
        summary["body"] = body
        log.info("[DRY] would ship %s -> %s", upload.url, body[:120])
        return summary

    # Lazy import — pipeline_test pulls in heavy deps (fal, etc.) and we'd
    # rather pay that cost only when we're about to publish.
    from pipeline_test import run_event_clip  # type: ignore

    try:
        piece = run_event_clip(piece, do_publish=True, wait_for_live=False)
        summary["shipped"] = True
        summary["outcome"] = f"posted run_id={piece.run_id} status={piece.status}"
    except Exception as exc:
        summary["outcome"] = f"publish_failed:{exc}"
        log.exception("EVENT_CLIP publish failed for %s", upload.url)
    return summary


def run_once(
    *,
    max_ships: int = MAX_SHIPS_PER_FIRE,
    hard_cap_per_hour: int = HARD_CAP_PER_HOUR,
    hard_cap_per_day: int = HARD_CAP_PER_DAY,
    dry_run: bool = False,
) -> dict:
    """Single watcher pass. Returns a summary dict."""
    summary: dict = {
        "channels": 0,
        "fresh_uploads": 0,
        "shipped": [],
        "skipped": [],
        "rate_cap_tripped": None,
        "skipped_locked": False,
    }

    with _exclusive_lock() as acquired:
        if not acquired:
            log.warning("another event-clip watcher pass is in flight — skipping")
            summary["skipped_locked"] = True
            return summary

        conn = _conn()
        _prune_old(conn)

        shipped_last_hour = _shipped_count_since(conn, 3600)
        shipped_last_day = _shipped_count_since(conn, 86400)
        if shipped_last_hour >= hard_cap_per_hour or shipped_last_day >= hard_cap_per_day:
            tripped = (
                f"hour={shipped_last_hour}/{hard_cap_per_hour}"
                if shipped_last_hour >= hard_cap_per_hour
                else f"day={shipped_last_day}/{hard_cap_per_day}"
            )
            log.warning("event-clip rate cap tripped (%s); pass yields nothing", tripped)
            summary["rate_cap_tripped"] = tripped
            return summary

        channels = channels_from_env()
        lookback = event_clip_lookback_hours()
        summary["channels"] = len(channels)

        ships_done = 0
        for channel_url in channels:
            if ships_done >= max_ships:
                break
            try:
                uploads = event_clip_list_fresh_uploads(channel_url, hours_back=lookback)
            except Exception as exc:
                log.warning("channel %s list failed: %s", channel_url, exc)
                continue
            if not uploads:
                continue
            for upload in uploads:
                if ships_done >= max_ships:
                    break
                item_id = _item_id(channel_url, upload.video_id)
                if _is_seen(conn, item_id):
                    continue
                summary["fresh_uploads"] += 1

                log.info("evaluating: %s (%s)", upload.title[:80], upload.url)
                result = _ship_one_upload(upload, dry_run=dry_run)

                _mark_seen(
                    conn,
                    item_id=item_id,
                    channel=channel_url,
                    video_id=upload.video_id,
                    title=upload.title,
                    url=upload.url,
                    shipped=result["shipped"],
                    outcome=result["outcome"],
                )
                if result["shipped"]:
                    ships_done += 1
                    summary["shipped"].append(result)
                else:
                    summary["skipped"].append(result)

    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-ships", type=int, default=MAX_SHIPS_PER_FIRE)
    p.add_argument("--hard-cap-per-hour", type=int, default=HARD_CAP_PER_HOUR)
    p.add_argument("--hard-cap-per-day", type=int, default=HARD_CAP_PER_DAY)
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch + analyse + cut, but don't publish.")
    args = p.parse_args()

    required = ["OPENROUTER_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error("Missing env: %s", ", ".join(missing))
        return 2

    summary = run_once(
        max_ships=args.max_ships,
        hard_cap_per_hour=args.hard_cap_per_hour,
        hard_cap_per_day=args.hard_cap_per_day,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
