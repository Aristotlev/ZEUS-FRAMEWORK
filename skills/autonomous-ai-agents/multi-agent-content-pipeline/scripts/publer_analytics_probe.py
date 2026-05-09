#!/usr/bin/env python3
"""One-shot probe: does Publer's analytics API work on our current plan?

Lists accounts, then calls /analytics/{account_id}/post_insights for the past 7
days against each one. Prints status + a slim summary so we can decide whether
to build the weekly-analytics cron on top of Publer or fall back to native APIs.

Run:
    python scripts/publer_analytics_probe.py

Exit codes: 0 if at least one account returned 200 with usable analytics, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

PUBLER_BASE = "https://app.publer.com/api/v1"


def _load_dotenv() -> None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".env"
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def _headers() -> dict[str, str]:
    key = os.environ.get("PUBLER_API_KEY")
    workspace = os.environ.get("PUBLER_WORKSPACE_ID")
    if not key or not workspace:
        raise RuntimeError("PUBLER_API_KEY and PUBLER_WORKSPACE_ID must be set")
    return {
        "Authorization": f"Bearer-API {key}",
        "Publer-Workspace-Id": workspace,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://app.publer.com",
    }


def list_accounts() -> list[dict]:
    r = requests.get(f"{PUBLER_BASE}/accounts", headers=_headers(), timeout=15)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("accounts", [])


def post_insights(account_id: str, since: date, until: date) -> tuple[int, dict | str]:
    url = f"{PUBLER_BASE}/analytics/{account_id}/post_insights"
    params = {"from": since.isoformat(), "to": until.isoformat(), "page": 0}
    r = requests.get(url, params=params, headers=_headers(), timeout=20)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


def main() -> int:
    _load_dotenv()
    until = date.today()
    since = until - timedelta(days=7)
    print(f"window: {since} -> {until}\n")

    accounts = list_accounts()
    print(f"accounts found: {len(accounts)}")
    for a in accounts:
        print(f"  - {a.get('provider'):>10}  {a.get('id')}  {a.get('name')}")
    print()

    ok_any = False
    for a in accounts:
        aid = a.get("id")
        prov = a.get("provider")
        status, body = post_insights(aid, since, until)
        if status == 200 and isinstance(body, dict):
            posts = body.get("posts", [])
            total = body.get("total", len(posts))
            ok_any = True
            print(f"[200] {prov:>10}  posts={len(posts)}  total={total}")
            if posts:
                sample = posts[0]
                analytics = sample.get("analytics", {})
                print(f"       sample analytics keys: {sorted(analytics.keys())}")
                print(f"       sample row: {json.dumps({k: sample.get(k) for k in ('id','scheduled_at','post_type')}, default=str)}")
                print(f"       sample analytics: {json.dumps(analytics)[:400]}")
        else:
            snippet = json.dumps(body)[:300] if isinstance(body, dict) else body
            print(f"[{status}] {prov:>10}  {snippet}")

    return 0 if ok_any else 1


if __name__ == "__main__":
    sys.exit(main())
