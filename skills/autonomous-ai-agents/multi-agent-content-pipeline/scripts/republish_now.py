#!/usr/bin/env python3
"""
One-off rescheduler: read 14 scheduled Publer posts (1 IG + 1 LI + 1 TT + 11 Twitter thread),
delete them, re-schedule with corrected UTC time, wait for them to go live, send email.

Use this when a previous pipeline run scheduled posts in the wrong timezone.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from lib import (  # noqa: E402
    ContentPiece, ContentType, GeneratedAsset,
    NotionArchive, send_pipeline_summary, ledger_append,
)

PUBLER_BASE = "https://app.publer.com/api/v1"
PUBLER_KEY = os.environ["PUBLER_API_KEY"]
PUBLER_WORKSPACE = os.environ["PUBLER_WORKSPACE_ID"]

# Account ids are read from env. Set PUBLER_<PLATFORM>_ID for any platform you
# want to republish to. Missing platforms are skipped.
ACCOUNTS_BY_PROVIDER: dict[str, str] = {
    prov: os.environ.get(f"PUBLER_{prov.upper()}_ID", "")
    for prov in ("instagram", "linkedin", "tiktok", "twitter", "youtube", "reddit", "facebook")
}
PROVIDER_BY_ACCOUNT: dict[str, str] = {
    acct_id: prov for prov, acct_id in ACCOUNTS_BY_PROVIDER.items() if acct_id
}

H_AUTH = {
    "Authorization": f"Bearer-API {PUBLER_KEY}",
    "Publer-Workspace-Id": PUBLER_WORKSPACE,
    "Accept": "application/json",
}
H_JSON = {**H_AUTH, "Content-Type": "application/json"}


def fetch_scheduled() -> list[dict]:
    r = requests.get(f"{PUBLER_BASE}/posts?limit=30", headers=H_AUTH, timeout=15)
    r.raise_for_status()
    return [p for p in r.json().get("posts", []) if p.get("state") == "scheduled"]


def delete_post(post_id: str) -> bool:
    r = requests.delete(f"{PUBLER_BASE}/posts/{post_id}", headers=H_AUTH, timeout=15)
    return r.status_code in (200, 204)


def schedule_simple(provider: str, account_id: str, text: str, media_id: str, when: str) -> str | None:
    payload = {
        "bulk": {
            "state": "scheduled",
            "posts": [{
                "networks": {provider: {
                    "type": "reel" if provider == "instagram" else "photo",
                    "text": text,
                    "media": [{"id": media_id}],
                }},
                "accounts": [{"id": account_id, "scheduled_at": when}],
            }],
        }
    }
    r = requests.post(f"{PUBLER_BASE}/posts/schedule", headers=H_JSON, json=payload, timeout=20)
    if r.status_code != 200:
        print(f"    schedule failed {r.status_code}: {r.text[:200]}")
        return None
    return r.json().get("job_id")


def schedule_twitter_thread(account_id: str, tweets: list[str], media_id: str, when: str) -> str | None:
    posts = []
    for i, t in enumerate(tweets):
        p = {
            "networks": {"twitter": {
                "type": "photo" if i == 0 else "status",
                "text": t,
            }},
            "accounts": [{"id": account_id, "scheduled_at": when}],
        }
        if i == 0:
            p["networks"]["twitter"]["media"] = [{"id": media_id}]
        posts.append(p)
    payload = {"bulk": {"state": "scheduled", "posts": posts, "thread": True}}
    r = requests.post(f"{PUBLER_BASE}/posts/schedule", headers=H_JSON, json=payload, timeout=30)
    if r.status_code != 200:
        print(f"    twitter thread schedule failed {r.status_code}: {r.text[:200]}")
        return None
    return r.json().get("job_id")


def wait_for_live(post_ids_by_platform: dict[str, str], max_wait_s: int = 360, poll_s: int = 15) -> dict[str, str]:
    """Poll each Publer post until state='posted'+post_link. Returns {platform: link_or_status}."""
    pending = dict(post_ids_by_platform)
    results: dict[str, str] = {}
    deadline = time.time() + max_wait_s
    print(f"  polling for {len(pending)} posts to go live (max {max_wait_s}s)...")
    while pending and time.time() < deadline:
        time.sleep(poll_s)
        for platform in list(pending.keys()):
            r = requests.get(f"{PUBLER_BASE}/posts/{pending[platform]}", headers=H_AUTH, timeout=15)
            if r.status_code != 200:
                continue
            post = r.json()
            state = post.get("state")
            link = post.get("post_link") or post.get("url")
            if state == "posted" and link:
                results[platform] = link
                print(f"  ✓ {platform} live: {link}")
                pending.pop(platform)
            elif state in ("error", "failed"):
                results[platform] = f"FAILED: {post.get('error') or 'unknown'}"
                print(f"  ✗ {platform} failed")
                pending.pop(platform)
    for platform, pid in pending.items():
        results[platform] = f"pending publer_post_id={pid}"
    return results


def main():
    print("=" * 60)
    print("  Zeus republish: fix timezone-bugged scheduled posts")
    print("=" * 60)

    scheduled = fetch_scheduled()
    print(f"\nFound {len(scheduled)} scheduled posts")

    # Group by provider
    by_provider: dict[str, list[dict]] = {"twitter": [], "instagram": [], "linkedin": [], "tiktok": []}
    media_id = None
    for p in scheduled:
        prov = PROVIDER_BY_ACCOUNT.get(p["account_id"])
        if not prov:
            continue
        by_provider[prov].append(p)
        if not media_id and p.get("media"):
            media_id = p["media"][0]["id"]

    if not media_id:
        print("ERROR: no media_id found in scheduled posts")
        return 1

    print(f"  reusing media_id: {media_id}")
    for prov, posts in by_provider.items():
        print(f"  {prov}: {len(posts)} posts")

    # Delete them all
    print(f"\nDeleting {len(scheduled)} posts...")
    deleted = 0
    for p in scheduled:
        if delete_post(p["id"]):
            deleted += 1
    print(f"  deleted {deleted}/{len(scheduled)}")

    time.sleep(3)

    # Reschedule with correct UTC time
    when = (datetime.now(timezone.utc) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    print(f"\nRescheduling for UTC {when}")

    job_ids: dict[str, str] = {}
    accounts = ACCOUNTS_BY_PROVIDER
    for prov in ("instagram", "linkedin", "tiktok"):
        posts = by_provider[prov]
        if not posts:
            continue
        if not accounts.get(prov):
            print(f"  ! skip {prov}: PUBLER_{prov.upper()}_ID not set")
            continue
        text = posts[0].get("text", "")
        jid = schedule_simple(prov, accounts[prov], text, media_id, when)
        if jid:
            job_ids[prov] = jid
            print(f"  ✓ {prov} scheduled (job {jid})")

    # Twitter thread - posts come back in some order; sort by old scheduled_at to preserve ordering
    twitter_posts = sorted(by_provider["twitter"], key=lambda p: p.get("updated_at", ""))
    if twitter_posts:
        # The thread posts in the original order have suffixes like "1/11", "2/11" etc
        # Sort by the suffix number
        def thread_idx(p):
            t = p.get("text", "")
            # find " N/M" at end
            import re
            m = re.search(r"\s(\d+)/(\d+)\s*$", t)
            return int(m.group(1)) if m else 999
        twitter_posts.sort(key=thread_idx)
        tweets = [p.get("text", "") for p in twitter_posts]
        jid = schedule_twitter_thread(accounts["twitter"], tweets, media_id, when)
        if jid:
            job_ids["twitter"] = jid
            print(f"  ✓ twitter thread ({len(tweets)} tweets) scheduled (job {jid})")

    # Now find each new Publer post_id (the IG/LI/TT one + the lead tweet) so we can poll
    print("\nResolving new Publer post IDs...")
    time.sleep(5)
    new_scheduled = fetch_scheduled()
    post_ids_by_platform: dict[str, str] = {}
    for prov, account_id in accounts.items():
        for p in new_scheduled:
            if p["account_id"] == account_id:
                # for twitter, we want the LEAD tweet (the one with media)
                if prov == "twitter" and not p.get("media"):
                    continue
                post_ids_by_platform[prov] = p["id"]
                break
    print(f"  tracking: {post_ids_by_platform}")

    # Poll until live
    results = wait_for_live(post_ids_by_platform, max_wait_s=420, poll_s=15)

    # Reconstruct ContentPiece for the email
    piece = ContentPiece(
        content_type=ContentType.LONG_ARTICLE,
        title="Claude Opus 4.7: Agentic AI Redefines Autonomous Workflows",
        body=by_provider.get("linkedin", [{}])[0].get("text", "") if by_provider.get("linkedin") else "",
        topic="Anthropic releases Claude Opus 4.7 — agentic AI breakthrough",
    )
    piece.status = "posted"
    piece.posted_at = datetime.now(timezone.utc)
    piece.notion_page_id = "35620419-31f5-8144-b490-d5997c66c1a5"
    piece.publer_job_ids = dict(job_ids)
    for plat, link in results.items():
        piece.publer_job_ids[f"{plat}_url"] = link
    piece.cost_breakdown = {
        "text:google/gemini-2.5-flash": 0.0015,
        "image:gpt-image-2": 0.0530,
    }

    # Send email
    backend = send_pipeline_summary(piece)
    print(f"\nEmail sent via: {backend}")
    print("\nFinal post links:")
    for plat, link in results.items():
        print(f"  {plat:12s} {link}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
