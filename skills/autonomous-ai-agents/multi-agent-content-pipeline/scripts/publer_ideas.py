#!/usr/bin/env python3
"""
Read draft posts ("Ideas") from Publer.

Standalone helper — NOT wired into the pipeline. Once the Publer workspace is
upgraded to Business/Enterprise (API access is plan-gated), the topic-picker
can `from publer_ideas import fetch_ideas` to fold Publer Ideas into its
candidate pool alongside the Notion Content Ideas DB.

Until then this script's CLI is the only entry point and is safe to run by
hand for spot-checks (will return 401 on free/trial plans — that's expected,
not a bug).

CLI:
    publer_ideas.py                          # all draft states, JSON
    publer_ideas.py --state draft_public     # public idea board only
    publer_ideas.py --state draft --state draft_private --format text
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Iterable

import requests

PUBLER_BASE = "https://app.publer.com/api/v1"

# Per Publer docs, drafts are stored as posts with one of these states.
# `draft_public` = visible on the workspace Ideas board to all members.
# `draft_private` = only the author sees it.
# `draft_dated` / `draft_undated` = drafts with/without a target date.
DRAFT_STATES = ("draft", "draft_public", "draft_private", "draft_dated", "draft_undated")


def _headers() -> dict[str, str]:
    key = os.environ.get("PUBLER_API_KEY")
    workspace = os.environ.get("PUBLER_WORKSPACE_ID")
    if not key or not workspace:
        raise RuntimeError("PUBLER_API_KEY and PUBLER_WORKSPACE_ID must be set")
    return {
        "Authorization": f"Bearer-API {key}",
        "Publer-Workspace-Id": workspace,
        "Accept": "application/json",
    }


def fetch_ideas(state: str = "draft_public", limit: int = 30) -> list[dict]:
    """Pull Publer drafts for a single state.

    Returns the raw post dicts as Publer ships them. Each typically has:
        id, state, text, networks, accounts, scheduled_at, created_at, ...

    Caller filters on whatever fields it cares about. We deliberately don't
    map into ContentPiece here — Ideas may not have media or platforms set,
    so the picker decides how to interpret each draft.
    """
    if state not in DRAFT_STATES:
        raise ValueError(f"state must be one of {DRAFT_STATES}, got {state!r}")
    r = requests.get(
        f"{PUBLER_BASE}/posts",
        params={"state": state, "limit": limit},
        headers=_headers(),
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("posts", [])


def fetch_ideas_multi(states: Iterable[str], limit: int = 30) -> list[dict]:
    """Fetch across several states; dedupes by post id.

    One request per state — Publer's API doesn't accept comma-separated states.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for st in states:
        for post in fetch_ideas(state=st, limit=limit):
            pid = post.get("id")
            if pid and pid not in seen:
                seen.add(pid)
                out.append(post)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="List Publer drafts (Ideas board).")
    ap.add_argument(
        "--state",
        action="append",
        choices=DRAFT_STATES,
        help="filter to specific draft state(s); repeatable. default = all draft states.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=30,
        help="how many recent posts per state (Publer caps at 30).",
    )
    ap.add_argument("--format", choices=("json", "text"), default="json")
    args = ap.parse_args()

    states = tuple(args.state) if args.state else DRAFT_STATES

    try:
        ideas = fetch_ideas_multi(states=states, limit=args.limit)
    except requests.HTTPError as e:
        sys.stderr.write(
            f"publer error: {e.response.status_code} {e.response.text[:200]}\n"
        )
        return 1
    except RuntimeError as e:
        sys.stderr.write(f"{e}\n")
        return 2

    if args.format == "json":
        print(json.dumps(ideas, indent=2, default=str))
    else:
        if not ideas:
            print("(no ideas)")
            return 0
        for p in ideas:
            text = (p.get("text") or "").strip().replace("\n", " ")
            print(f"[{p.get('state')}] {p.get('id')}: {text[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
