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
PUBLER_WORKSPACE = os.getenv("PUBLER_WORKSPACE_ID", "")

# Paid-but-not-posted retry cap. When every target platform fails at Publer
# (e.g. all 4 hit "no available timeslot" — a misconfigured Publer schedule
# or an expired auth token), the artifact is already paid for. Re-call
# pipeline_test.publish() to re-upload + re-schedule per-platform. Retry
# costs zero credits (no fal/LLM, just Publer API). Cap prevents an
# infinite loop on permanent misconfig — after 3 attempts the run goes
# terminal-failed and the user gets the email so they can fix the root
# cause manually.
MAX_PUBLISH_RETRIES = 3
PUBLER_ACCOUNTS = {
    "twitter": os.getenv("PUBLER_TWITTER_ID", ""),
    "instagram": os.getenv("PUBLER_INSTAGRAM_ID", ""),
    "linkedin": os.getenv("PUBLER_LINKEDIN_ID", ""),
    "tiktok": os.getenv("PUBLER_TIKTOK_ID", ""),
    "youtube": os.getenv("PUBLER_YOUTUBE_ID", ""),
    "reddit": os.getenv("PUBLER_REDDIT_ID", ""),
    "facebook": os.getenv("PUBLER_FACEBOOK_ID", ""),
}


def _publer_headers() -> dict:
    # User-Agent + Origin are required — Publer sits behind Cloudflare and
    # returns 1010 "browser_signature_banned" without them.
    return {
        "Authorization": f"Bearer-API {PUBLER_KEY}",
        "Publer-Workspace-Id": PUBLER_WORKSPACE,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; ZeusPipeline/1.0)",
        "Origin": "https://app.publer.com",
    }


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _caption_for(piece: ContentPiece, platform: str) -> str:
    """Mirror of pipeline_test.caption_for — same body, truncated cleanly."""
    limit = LIMITS.get(platform, len(piece.body))
    body = piece.body
    if len(body) <= limit:
        return body
    cut = body.rfind(" ", 0, limit - 1)
    if cut < limit // 2:
        cut = limit - 1
    return body[:cut].rstrip(" ,;:.") + "…"


def _post_id_from_job(job_id: str) -> str | None:
    """Resolve a Publer schedule job_id directly to the post id.

    Eliminates the fuzzy snippet match for correct cases — concurrent runs on
    the same topic used to alias their permalinks because the snippet matcher
    would lock onto whichever post landed in the timeline first.
    """
    if not job_id or job_id.startswith("FAILED"):
        return None
    try:
        r = requests.get(f"{PUBLER_BASE}/job_status/{job_id}", headers=_publer_headers(), timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        posts = data.get("posts") or (data.get("payload") or {}).get("posts") or []
        for p in posts:
            pid = p.get("id") or p.get("post_id")
            if pid:
                return pid
    except Exception as e:
        log.warning(f"_post_id_from_job error: {e}")
    return None


def _find_publer_post_id(account_id: str, snippet: str) -> str | None:
    """Find the Publer post id by matching account + text snippet.

    The text-based fallback ('any post for this account') was removed: it
    cross-attributed URLs when two runs targeting the same account were both
    in flight (run B's match returned run A's most-recent post as B's URL).
    Now: snippet must actually match. If not, return None so the watcher
    keeps polling until the real post lands.
    """
    if not snippet:
        return None
    try:
        r = requests.get(f"{PUBLER_BASE}/posts?limit=30", headers=_publer_headers(), timeout=15)
        if r.status_code != 200:
            return None
        target = _norm(snippet)[:40]
        if not target:
            return None
        for p in r.json().get("posts", []):
            if p.get("account_id") != account_id:
                continue
            if target in _norm(p.get("text") or ""):
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
    {platform: state} where state is 'live'|'failed'|'skipped'|'pending'.

    'skipped' = the platform is in target_platforms but was never actually
    scheduled (typically because PUBLER_<PLATFORM>_ID is unset). _final_status
    must NOT count skipped toward failure — the run is still healthy with
    fewer platforms.

    Mutates piece.publer_job_ids in place: adds '<platform>_url' for confirmed
    posts (and uses 'FAILED:'/'PENDING:' prefixes consistent with the pipeline).
    """
    states: dict[str, str] = {}
    for platform in piece.target_platforms:
        scheduled = piece.publer_job_ids.get(platform, "")
        if not scheduled:
            # Empty job_id means publish() never even attempted this platform
            # — likely no PUBLER_<PLATFORM>_ID configured. Distinct from FAILED.
            states[platform] = "skipped"
            continue
        if scheduled.startswith("FAILED"):
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
            # Prefer direct job_id -> post_id resolution. The snippet matcher
            # only runs as a backstop for the (rare) race where job_status
            # doesn't yet reflect the post.
            post_id = _post_id_from_job(scheduled)
            if not post_id:
                media_count = (
                    1 if piece.video and piece.video.local_path
                    else sum(1 for img in piece.images if img.local_path)
                )
                if (
                    platform == "twitter"
                    and needs_thread(piece.body)
                    and media_count <= 1
                ):
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
        # Publer's API uses 'published' as the live-on-platform state. The
        # original watcher only checked for 'posted', so every successful run
        # silently went to 'pending' forever. Accept both — 'posted' may have
        # been an older Publer naming.
        if state in ("posted", "published") and link:
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
    # 'skipped' platforms — never attempted (no Publer account ID) — are
    # ignored when judging status. A run that landed on twitter+linkedin
    # with facebook+reddit skipped is fully posted, not partial.
    if confirmed and not failed and not pending:
        return "posted"
    # KEY FIX: don't archive a run as failed just because the deadline passed
    # while platforms are still pending. Publer commonly publishes minutes-
    # to-hours after our scheduling call (their feed-spacing logic), so a
    # 12-min deadline + a pending platform used to dump healthy runs into
    # done.jsonl with status=failed and no URLs. Now we only finalise when
    # there's nothing left pending — past_deadline just enables the
    # partial-vs-failed distinction at that point.
    if not pending:
        if past_deadline:
            if confirmed:
                return "partial"
            return "failed"
        # Nothing pending, nothing confirmed yet, deadline not reached:
        # everything's still in flight on Publer's side.
    return "scheduled"  # still in flight


def _retry_publish(piece: ContentPiece, row: dict, meta: dict) -> bool:
    """Re-publish a fully-failed piece using its on-disk artifacts.

    Returns True if the retry was attempted and the row should stay in the
    queue (so the next watcher tick resolves the new per-platform jobs).
    Returns False to fall through to the terminal-failed flow (notion +
    ledger + email).

    Skipped (returns False) when:
      - retry_count is already at MAX_PUBLISH_RETRIES
      - no images/video have a local_path (artifact gone — can't re-upload)
      - pipeline_test.publish() raises (e.g. PUBLER_API_KEY suddenly unset)
    """
    attempts = int(meta.get("retry_count") or 0)
    if attempts >= MAX_PUBLISH_RETRIES:
        log.warning(
            f"  run={piece.run_id} all platforms failed and retry cap "
            f"({MAX_PUBLISH_RETRIES}) reached -- giving up"
        )
        return False
    has_local = any(getattr(img, "local_path", None) for img in piece.images) or (
        piece.video and getattr(piece.video, "local_path", None)
    )
    if not has_local:
        log.warning(
            f"  run={piece.run_id} all platforms failed but no local "
            f"artifact to re-upload -- giving up"
        )
        return False
    log.warning(
        f"  run={piece.run_id} all platforms failed -- retry "
        f"#{attempts + 1}/{MAX_PUBLISH_RETRIES} (reusing local media)"
    )
    # Clear stale per-platform job ids + URL markers so publish() schedules
    # fresh jobs and the next watcher pass starts URL resolution from clean.
    piece.publer_job_ids = {}
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        import pipeline_test  # lazy: avoid circular cost at module load
        pipeline_test.publish(piece)
    except Exception as e:
        log.error(f"  retry failed: {e} -- finalizing as failed")
        return False
    # Stamp the queue row so the next pass sees the new state. Bumping
    # retry_count here (rather than in enqueue) keeps retries observable
    # in zeus_publish_done.jsonl after the run eventually resolves.
    row["piece"]["publer_job_ids"] = dict(piece.publer_job_ids)
    row["piece"]["status"] = piece.status
    row["retry_count"] = attempts + 1
    return True


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
        if new_status == "failed" and _retry_publish(piece, row, meta):
            remaining.append(row)
            continue
        if new_status in ("posted", "partial", "failed"):
            piece.status = new_status
            piece.posted_at = datetime.now(timezone.utc)
            try:
                if notion is not None and piece.notion_page_id:
                    notion.update_status(piece)
            except Exception as e:
                log.error(f"  notion update_status failed: {e}")
            # Pipeline DB row: if pipeline_test.py created one earlier, patch
            # it; otherwise create a fresh row now (so runs that pre-date the
            # feature, or runs where NOTION_PIPELINE_DB_ID hadn't been set
            # yet, still end up with a properly populated row including the
            # newly-resolved Post URLs).
            try:
                if notion is not None:
                    if getattr(piece, "notion_pipeline_page_id", None):
                        notion.update_pipeline_row(piece)
                    else:
                        notion.write_pipeline_row(piece)
            except Exception as e:
                log.error(f"  notion pipeline-row sync failed: {e}")
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
