#!/usr/bin/env python3
"""
publish_watcher — resolves pending Publer publishes out-of-process.

The pipeline used to block ~6 min per run polling Publer for live URLs. Now it
schedules posts and exits; this watcher does the polling, captures permalinks,
patches Notion, writes the final ledger row, and sends the "posts live" email.

Modes:
    --once     run a single pass over the queue and exit (cron-friendly)
    --daemon   run forever, polling every --interval seconds (default 120)

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


def _load_env_file() -> None:
    # Same as pipeline_test.py: when invoked from the agent's execute_code
    # subprocess (cron path), the env doesn't propagate. Without this the
    # watcher daemon spawns with no PUBLER_API_KEY → silent no-op → no
    # permalinks resolved → no email. Stdlib parser since python-dotenv
    # isn't in the system python.
    for path in ("/opt/data/.env", os.path.expanduser("~/.hermes/.env"), os.path.expanduser("~/.env")):
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
# PENDING_UPLOAD retry cap (LONG_ARTICLE only). When pipeline_test.publish()
# exhausts its in-process ~5min upload retry budget, every Publer platform
# is marked "FAILED: PENDING_UPLOAD: ...". This prefix is a transient signal
# — Publer's /media endpoint hit a workspace-wide 429 window. Retry every
# tick (2 min) up to this cap so the post eventually ships with its image
# once Publer recovers. 30 ticks × 2min ≈ 1hr — covers normal Publer 429
# windows. Past that the run goes terminal-failed (user emailed).
PENDING_UPLOAD_MAX_RETRIES = 30

# TikTok permalink-callback grace period. Publer marks TikTok posts as
# state=published the moment TikTok accepts them, but the live URL only
# arrives via TikTok's webhook → Publer, which can lag minutes-to-hours
# (or never if the webhook drops). Without this, a healthy run sits in
# the pending queue for the whole 24h deadline waiting on TikTok and the
# user never gets the run-completion email. Once every other platform on
# the run reaches a terminal state, we give TikTok this many seconds of
# grace before dropping it (URL marker + state=dropped) so the email can
# fire on time. The post is still live on TikTok — only the link tracking
# is forfeited.
TIKTOK_GRACE_SECONDS = 180
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


# Process-wide Publer 429 cooldown. Watcher + breaking-news cron + verify
# GETs together saturate Publer's per-workspace rate limit; once we trip
# 429, every helper short-circuits until the cooldown expires instead of
# hammering. Honors `Retry-After` when Publer sets it; otherwise 90s.
# Observed 2026-05-13: 13 ARTICLE rows stuck `pending` for hours because
# every /job_status + /posts GET 429ed and no log line surfaced.
_PUBLER_BACKOFF_UNTIL: float = 0.0
_DEFAULT_BACKOFF_S: float = 90.0


def _publer_rate_limited() -> bool:
    return time.monotonic() < _PUBLER_BACKOFF_UNTIL


def _publer_cooldown_remaining() -> float:
    return max(0.0, _PUBLER_BACKOFF_UNTIL - time.monotonic())


def _publer_note_429(resp: requests.Response | None = None) -> None:
    global _PUBLER_BACKOFF_UNTIL
    wait = _DEFAULT_BACKOFF_S
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                wait = max(wait, float(ra))
            except (TypeError, ValueError):
                pass
    target = time.monotonic() + wait
    if target > _PUBLER_BACKOFF_UNTIL:
        _PUBLER_BACKOFF_UNTIL = target
        log.warning(f"Publer rate-limited (HTTP 429) — pausing all GETs for {wait:.0f}s")


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
    if _publer_rate_limited():
        return None
    try:
        r = requests.get(f"{PUBLER_BASE}/job_status/{job_id}", headers=_publer_headers(), timeout=15)
        if r.status_code == 429:
            _publer_note_429(r)
            return None
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
    if _publer_rate_limited():
        return None
    try:
        r = requests.get(f"{PUBLER_BASE}/posts?limit=30", headers=_publer_headers(), timeout=15)
        if r.status_code == 429:
            _publer_note_429(r)
            return None
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
    if _publer_rate_limited():
        return None
    try:
        r = requests.get(f"{PUBLER_BASE}/posts/{post_id}", headers=_publer_headers(), timeout=15)
        if r.status_code == 429:
            _publer_note_429(r)
            return None
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
        # Persisted across passes via piece.publer_job_ids[f"{platform}_post_id"]
        # so subsequent watcher passes skip the /job_status GET entirely. Before
        # this, every 30s pass re-resolved every pending platform's post_id from
        # the schedule job_id — for 13 stuck rows × ~3 platforms = 78 GETs/min
        # in steady state, which is the main contributor to the Publer 429
        # cliff seen 2026-05-13. The post_id is stable once Publer assigns it.
        post_id = post_id_cache.get(cache_key) or piece.publer_job_ids.get(f"{platform}_post_id", "") or ""
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
                    and piece.content_type not in (ContentType.ARTICLE, ContentType.LONG_ARTICLE)
                    and needs_thread(piece.body)
                    and media_count <= 1
                ):
                    snippet = split_thread(piece.body)[0]
                else:
                    snippet = _caption_for(piece, platform) or piece.body
                post_id = _find_publer_post_id(account, snippet)
            if post_id:
                post_id_cache[cache_key] = post_id
                piece.publer_job_ids[f"{platform}_post_id"] = post_id
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


def _has_pending_upload(piece: ContentPiece) -> bool:
    """True if any Publer platform's job_id is the PENDING_UPLOAD transient
    marker emitted by pipeline_test.publish() when /media 429'd. Drives both
    immediate-retry (no 24h deadline wait) and the higher retry cap in
    _retry_publish.
    """
    for plat in piece.target_platforms:
        if plat == "substack":
            continue
        jid = str(piece.publer_job_ids.get(plat, ""))
        if "PENDING_UPLOAD:" in jid:
            return True
    return False


def _final_status(states: dict[str, str], piece: ContentPiece, past_deadline: bool) -> str:
    confirmed = [p for p, s in states.items() if s == "live"]
    failed = [p for p, s in states.items() if s == "failed"]
    pending = [p for p, s in states.items() if s == "pending"]
    # PENDING_UPLOAD: Publer /media 429'd on the original publish run so every
    # Publer platform is marked FAILED with a "PENDING_UPLOAD:" prefix. These
    # are transient — Substack may already be live. Skip the 24h past_deadline
    # gate and return "failed" immediately so _retry_publish re-runs publish()
    # this tick. Higher retry cap (PENDING_UPLOAD_MAX_RETRIES) applies there.
    if _has_pending_upload(piece) and not confirmed and not pending:
        return "failed"
    # 'skipped' platforms — never attempted (no Publer account ID) — and
    # 'dropped' platforms (TikTok grace expired without a permalink) are
    # ignored when judging status. A run that landed on twitter+linkedin
    # with facebook+reddit skipped is fully posted, not partial. Same for
    # a run where TikTok went live but the URL never came back.
    if confirmed and not failed and not pending:
        return "posted"
    if confirmed and failed and not pending:
        # No platforms left to advance — the failed ones won't recover
        # (TikTok rate-limit, ghost account, etc.) and the rest are live.
        # Finalize now rather than waiting for the 24h deadline. Was:
        # stayed "scheduled" until past_deadline, which left 4 rows in
        # the queue 9–13h on 2026-05-10 when TikTok hit its OpenAPI
        # daily cap; the completion email never fired and the user
        # thought posting had stopped.
        return "partial"
    if past_deadline:
        # Past the row's hard deadline. Stop waiting on still-pending
        # platforms — finalize now. Treating pending as failed lets the row
        # leave the queue and the email/ledger/notion close-out happen.
        # Was: returned "scheduled" forever whenever any platform stayed
        # pending past deadline, leaking 4 May-7 + 3 May-8 rows for 37-55h.
        if confirmed:
            return "partial"
        return "failed"
    return "scheduled"  # pre-deadline: keep waiting on Publer


def _retry_publish(piece: ContentPiece, row: dict, meta: dict) -> bool:
    """Re-publish a fully-failed piece using its on-disk artifacts.

    Returns True if the retry was attempted and the row should stay in the
    queue (so the next watcher tick resolves the new per-platform jobs).
    Returns False to fall through to the terminal-failed flow (notion +
    ledger + email).

    Skipped (returns False) when:
      - retry_count is already at MAX_PUBLISH_RETRIES
      - no images/video have a local_path (artifact gone — can't re-upload)
      - any platform's existing job_id was accepted by Publer (re-publishing
        would duplicate live posts; see ARM TikTok dupe 2026-05-09)
      - any platform has a live post matching this snippet on Publer
      - pipeline_test.publish() raises (e.g. PUBLER_API_KEY suddenly unset)
    """
    # Distinguish PENDING_UPLOAD (transient Publer /media 429 — retry up to
    # PENDING_UPLOAD_MAX_RETRIES, ~1hr at 2-min ticks) from real publish
    # failures (schedule errors / misconfig — MAX_PUBLISH_RETRIES = 3 to
    # surface permanent issues to the user via the failure email). Separate
    # counter keys so a Publer outage doesn't burn the 3-attempt cap meant
    # for misconfig detection.
    pending_upload = _has_pending_upload(piece)
    if pending_upload:
        counter_key = "upload_retry_count"
        cap = PENDING_UPLOAD_MAX_RETRIES
    else:
        counter_key = "retry_count"
        cap = MAX_PUBLISH_RETRIES
    attempts = int(meta.get(counter_key) or 0)
    if attempts >= cap:
        log.warning(
            f"  run={piece.run_id} {'PENDING_UPLOAD' if pending_upload else 'all platforms failed'} "
            f"and retry cap ({cap}) reached -- giving up"
        )
        return False
    # Idempotency guard: distinguish "Publer rejected the schedule"
    # (FAILED: marker on the job_id) from "Publer accepted but the
    # watcher couldn't resolve the post" (real id, no _url marker).
    # _final_status returns "failed" for both when past_deadline + no
    # confirmed -- but only the first is safe to retry. The second
    # almost always means the post WENT LIVE and the watcher just
    # couldn't see it (Publer rate-limit, transient API errors).
    # Re-publishing in that case duplicates every platform that worked.
    accepted_jobs = [
        (plat, jid) for plat, jid in piece.publer_job_ids.items()
        if not plat.endswith("_url") and jid and not str(jid).startswith("FAILED")
    ]
    if accepted_jobs:
        log.error(
            f"  run={piece.run_id} ABORTING retry: Publer accepted "
            f"{len(accepted_jobs)} platform job(s) ({[p for p,_ in accepted_jobs]}) "
            f"on the original publish -- the watcher couldn't resolve the live "
            f"post(s), but Publer almost certainly published them. Re-running "
            f"publish() would duplicate. Finalizing as failed instead."
        )
        return False
    # Last-ditch cross-check: even if every job_id is FAILED:, snippet-match
    # against Publer's recent posts per account. If a post on this account
    # already matches this snippet, that platform DID go live -- abort retry.
    snippet = (piece.body or "")[:60]
    if snippet:
        for plat in piece.target_platforms:
            account = PUBLER_ACCOUNTS.get(plat)
            if not account:
                continue
            try:
                if _find_publer_post_id(account, snippet):
                    log.error(
                        f"  run={piece.run_id} ABORTING retry: Publer already "
                        f"has a post on {plat} (acct ...{account[-6:]}) matching "
                        f"this snippet. Likely a stale FAILED marker -- the "
                        f"original publish actually worked."
                    )
                    return False
            except Exception:
                pass  # snippet check is best-effort; fall through to retry
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
        f"  run={piece.run_id} {'PENDING_UPLOAD retry' if pending_upload else 'all platforms failed -- retry'} "
        f"#{attempts + 1}/{cap} (reusing local media)"
    )
    # Clear stale per-platform job ids + URL markers so publish() schedules
    # fresh jobs and the next watcher pass starts URL resolution from clean.
    # EXCEPT substack: when PENDING_UPLOAD is the cause, Substack already
    # shipped on the original run (it doesn't go through Publer /media),
    # and re-running _publish_substack would create a duplicate post.
    # publish_substack's idempotency guard also checks for the http-prefixed
    # substack_url, but preserving the marker here is belt-and-braces.
    substack_state = {
        k: v for k, v in piece.publer_job_ids.items()
        if k.startswith("substack")
    }
    piece.publer_job_ids = {}
    piece.publer_job_ids.update(substack_state)
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        import pipeline_test  # lazy: avoid circular cost at module load
        pipeline_test.publish(piece)
    except Exception as e:
        log.error(f"  retry failed: {e} -- finalizing as failed")
        return False
    # Stamp the queue row so the next pass sees the new state. Bumping
    # the counter here (rather than in enqueue) keeps retries observable
    # in zeus_publish_done.jsonl after the run eventually resolves.
    row["piece"]["publer_job_ids"] = dict(piece.publer_job_ids)
    row["piece"]["status"] = piece.status
    row[counter_key] = attempts + 1
    return True


def _maybe_drop_tiktok(piece: ContentPiece, states: dict[str, str], row: dict, post_id_cache: dict) -> None:
    """If TikTok is the only platform still pending and every other one has
    reached a terminal state, give TikTok TIKTOK_GRACE_SECONDS to surface a
    permalink. Past that, drop it: write a PENDING url marker so the email +
    Notion show "posted but URL not resolved" instead of "still scheduled",
    and override states['tiktok']='dropped' so _final_status finalizes the
    run. The post itself is already live on TikTok — only the link is lost.

    Mutates `states` and `row` in place (the watcher persists row updates
    back to the queue at the end of the pass).
    """
    if states.get("tiktok") != "pending":
        return
    others_terminal = all(
        s in ("live", "failed", "skipped", "dropped")
        for p, s in states.items()
        if p != "tiktok"
    )
    if not others_terminal:
        return
    stamp = row.get("non_tiktok_done_at")
    now = datetime.now(timezone.utc)
    if not stamp:
        row["non_tiktok_done_at"] = now.isoformat()
        return
    try:
        elapsed = (now - datetime.fromisoformat(stamp)).total_seconds()
    except ValueError:
        row["non_tiktok_done_at"] = now.isoformat()
        return
    if elapsed < TIKTOK_GRACE_SECONDS:
        return
    post_id = post_id_cache.get(f"{piece.run_id}:tiktok") or piece.publer_job_ids.get("tiktok") or "unknown"
    piece.publer_job_ids["tiktok_url"] = f"PENDING: post_id={post_id}"
    states["tiktok"] = "dropped"
    log.info(
        f"  run={piece.run_id} dropping tiktok after {int(elapsed)}s grace "
        f"(post live, permalink callback never arrived)"
    )


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
        _maybe_drop_tiktok(piece, states, row, post_id_cache)
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
            # ARTICLE (short-form) is mute by design — breaking-news cron
            # fires too often to email per run. LONG_ARTICLE / CAROUSEL /
            # video types still email when their permalinks resolve.
            if piece.content_type == ContentType.ARTICLE:
                log.info("  email suppressed — ARTICLE is mute by design")
            else:
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
    ap.add_argument("--interval", type=int, default=120, help="Daemon poll interval in seconds (default 120)")
    args = ap.parse_args()

    if not PUBLER_KEY:
        log.error("PUBLER_API_KEY not set")
        return 2

    if args.daemon:
        log.info(f"watcher daemon: poll every {args.interval}s")
        try:
            while True:
                # If a prior pass tripped Publer's 429, sleep through the
                # cooldown before starting another pass — running a full pass
                # with every GET short-circuiting just spins the queue and
                # keeps the rate-limit window open via verify path elsewhere.
                cooldown = _publer_cooldown_remaining()
                if cooldown > 0:
                    sleep_s = max(args.interval, cooldown + 5)
                    log.info(f"  Publer cooldown active ({cooldown:.0f}s left) — sleeping {sleep_s:.0f}s")
                    time.sleep(sleep_s)
                    continue
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
