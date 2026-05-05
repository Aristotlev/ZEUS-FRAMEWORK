#!/usr/bin/env python3
"""
publish_watcher — resolves pending Publer publishes out-of-process.

The pipeline used to block ~6 min per run polling Publer for live URLs. Now it
schedules posts and exits; this watcher does the polling, captures permalinks,
patches Notion, writes the final ledger row, and sends the "posts live" email.

Modes:
    --once     run a single pass over the queue and exit (cron-friendly)
    --daemon   run forever, polling every --interval seconds (default 30)

Cron example (every 2 minutes):
    */2 * * * * cd /path/to/pipeline && /path/to/.venv/bin/python scripts/publish_watcher.py --once

Each pending row stays in the queue until either:
  1. every platform on the run has a live permalink captured, or
  2. its deadline (default 12 min after enqueue) has passed.
On either, the watcher writes the final ledger row, patches Notion, sends the
"posts live" email, archives the row to ~/.hermes/zeus_publish_done.jsonl, and
removes it from the active queue.

Required env (same as pipeline_test.py):
    PUBLER_API_KEY, PUBLER_WORKSPACE_ID  — to query Publer
    NOTION_API_KEY                       — to patch the archive page
    (RESEND_API_KEY|AGENTMAIL_API_KEY|HERMES_GMAIL_*) — for email
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

import requests

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from lib import (  # noqa: E402
    ContentPiece,
    LIMITS,
    NotionArchive,
    ledger_append,
    needs_thread,
    publish_archive_done,
    publish_hydrate,
    publish_is_past_deadline,
    publish_read_pending,
    publish_rewrite_queue,
    send_pipeline_summary,
    split_thread,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("zeus-watcher")

PUBLER_BASE = "https://app.publer.com/api/v1"
PUBLER_KEY = os.getenv("PUBLER_API_KEY", "")
PUBLER_WORKSPACE = os.getenv("PUBLER_WORKSPACE_ID", "your-workspace-id")
PUBLER_ACCOUNTS = {
    "twitter": os.getenv("PUBLER_TWITTER_ID", "69f783d1afc106b8869cf50b"),
    "instagram": os.getenv("PUBLER_INSTAGRAM_ID", "69f6511c5cf7421d7047fc4e"),
    "linkedin": os.getenv("PUBLER_LINKEDIN_ID", "69f783c63642e046435f7707"),
    "tiktok": os.getenv("PUBLER_TIKTOK_ID", "69f783de2c63a6ec70868731"),
    "youtube": os.getenv("PUBLER_YOUTUBE_ID", ""),
    "reddit": os.getenv("PUBLER_REDDIT_ID", ""),
    "facebook": os.getenv("PUBLER_FACEBOOK_ID", ""),
}


def _publer_headers() -> dict:
    return {
        "Authorization": f"Bearer-API {PUBLER_KEY}",
        "Publer-Workspace-Id": PUBLER_WORKSPACE,
        "Accept": "application/json",
    }


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _caption_for(piece: ContentPiece, platform: str) -> str:
    limit = LIMITS.get(platform, len(piece.body))
    return piece.body[:limit]


def _find_publer_post_id(account_id: str, snippet: str) -> str | None:
    """Same matching strategy as pipeline_test._publer_find_post_id."""
    try:
        r = requests.get(f"{PUBLER_BASE}/posts?limit=30", headers=_publer_headers(), timeout=15)
        if r.status_code != 200:
            return None
        target = _norm(snippet)[:40]
        posts = r.json().get("posts", [])
        for p in posts:
            if p.get("account_id") != account_id:
                continue
            if target and target in _norm(p.get("text") or ""):
                return p.get("id")
        for p in posts:
            if p.get("account_id") == account_id:
                return p.get("id")
    except Exception as e:
        log.warning(f"_find_publer_post_id error: {e}")
    return None


def _get_post(post_id: str) -> dict | None:
    try:
        r = requests.get(f"{PUBLER_BASE}/posts/{post_id}", headers=_publer_headers(), timeout=15)
        if r.status_code != 200:
            return None
        body = r.json()
        return body.get("post") or body
    except Exception:
        return None


def _extract_url(post: dict) -> str | None:
    for k in ("post_link", "url", "permalink", "public_url", "external_url", "social_url", "live_url"):
        v = post.get(k)
        if v and isinstance(v, str) and v.startswith("http"):
            return v
    nested = post.get("platform_data") or post.get("response") or {}
    if isinstance(nested, dict):
        for k in ("url", "permalink", "post_link"):
            v = nested.get(k)
            if v and isinstance(v, str) and v.startswith("http"):
                return v
    return None


def _resolve_one(piece: ContentPiece, post_id_cache: dict) -> dict:
    """
    Try to advance every still-pending platform on `piece`. Returns
    {platform: state} where state is 'live'|'failed'|'pending'.
    Mutates piece.publer_job_ids in place: adds '<platform>_url' for confirmed
    posts (and uses 'FAILED:'/'PENDING:' prefixes consistent with the pipeline).
    """
    states: dict[str, str] = {}
    for platform in piece.target_platforms:
        scheduled = piece.publer_job_ids.get(platform, "")
        if not scheduled or scheduled.startswith("FAILED"):
            states[platform] = "failed"
            continue
        already = piece.publer_job_ids.get(f"{platform}_url", "")
        if already and already.startswith("http"):
            states[platform] = "live"
            continue
        if already.startswith("FAILED"):
            states[platform] = "failed"
            continue
        account = PUBLER_ACCOUNTS.get(platform)
        if not account:
            states[platform] = "failed"
            continue
        cache_key = f"{piece.run_id}:{platform}"
        post_id = post_id_cache.get(cache_key)
        if not post_id:
            if platform == "twitter" and needs_thread(piece.body):
                snippet = split_thread(piece.body)[0]
            else:
                snippet = _caption_for(piece, platform) or piece.body
            post_id = _find_publer_post_id(account, snippet)
            if post_id:
                post_id_cache[cache_key] = post_id
        if not post_id:
            states[platform] = "pending"
            continue
        post = _get_post(post_id)
        if not post:
            states[platform] = "pending"
            continue
        state = post.get("state")
        link = _extract_url(post)
        if state == "posted" and link:
            piece.publer_job_ids[f"{platform}_url"] = link
            states[platform] = "live"
        elif state in ("error", "failed"):
            err = post.get("error") or "unknown error"
            piece.publer_job_ids[f"{platform}_url"] = f"FAILED: {err}"
            states[platform] = "failed"
        else:
            states[platform] = "pending"
    return states


def _final_status(states: dict[str, str], piece: ContentPiece, past_deadline: bool) -> str:
    confirmed = [p for p, s in states.items() if s == "live"]
    failed = [p for p, s in states.items() if s == "failed"]
    pending = [p for p, s in states.items() if s == "pending"]
    if confirmed and not failed and not pending:
        return "posted"
    if past_deadline:
        if confirmed:
            return "partial"
        return "failed"
    return "scheduled"  # still in flight


def _process_pass() -> tuple[int, int, int]:
    """Returns (resolved, advanced, still_pending)."""
    rows = publish_read_pending()
    if not rows:
        return 0, 0, 0
    log.info(f"watcher: {len(rows)} pending row(s)")

    archive_buffer: list[dict] = []
    remaining: list[dict] = []
    post_id_cache: dict = {}
    advanced = 0
    resolved = 0

    notion: NotionArchive | None = None
    if os.getenv("NOTION_API_KEY"):
        try:
            notion = NotionArchive()
        except Exception as e:
            log.warning(f"watcher: NotionArchive init failed ({e}); will skip notion patch")

    for row in rows:
        piece, meta = publish_hydrate(row)
        past_deadline = publish_is_past_deadline(row)
        states = _resolve_one(piece, post_id_cache)
        new_status = _final_status(states, piece, past_deadline)
        log.info(
            f"  run={piece.run_id} type={piece.content_type.value} "
            f"states={states} -> {new_status}"
        )
        if new_status in ("posted", "partial", "failed"):
            piece.status = new_status
            piece.posted_at = datetime.now(timezone.utc)
            try:
                if notion is not None and piece.notion_page_id:
                    notion.update_status(piece)
            except Exception as e:
                log.error(f"  notion update_status failed: {e}")
            try:
                ledger_append(piece)
            except Exception as e:
                log.error(f"  ledger_append failed: {e}")
            try:
                backend = send_pipeline_summary(piece)
                log.info(f"  notified -> backend={backend}")
            except Exception as e:
                log.error(f"  email failed: {e}")
            archive_buffer.append({**row, "final_status": new_status})
            resolved += 1
        else:
            # advance the in-queue row's state so we don't re-resolve URLs we
            # already captured next pass
            row["piece"]["publer_job_ids"] = dict(piece.publer_job_ids)
            row["piece"]["status"] = piece.status
            remaining.append(row)
            if any(s == "live" for s in states.values()):
                advanced += 1

    if archive_buffer:
        publish_archive_done(archive_buffer)
    publish_rewrite_queue(remaining)
    return resolved, advanced, len(remaining)


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve pending Publer publishes (queue → live URLs)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="Run one pass and exit (default)")
    g.add_argument("--daemon", action="store_true", help="Loop forever, polling --interval seconds")
    ap.add_argument("--interval", type=int, default=30, help="Daemon poll interval in seconds (default 30)")
    args = ap.parse_args()

    if not PUBLER_KEY:
        log.error("PUBLER_API_KEY not set")
        return 2

    if args.daemon:
        log.info(f"watcher daemon: poll every {args.interval}s")
        try:
            while True:
                resolved, advanced, pending = _process_pass()
                log.info(f"  pass: resolved={resolved} advanced={advanced} pending={pending}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            log.info("watcher stopped")
            return 0
    else:
        resolved, advanced, pending = _process_pass()
        log.info(f"watcher pass: resolved={resolved} advanced={advanced} pending={pending}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
