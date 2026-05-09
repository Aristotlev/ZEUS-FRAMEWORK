#!/usr/bin/env python3
"""Weekly Publer analytics → Notion DB row + email rollup.

Runs Sundays at 20:00 Europe/Athens (= 17:00 UTC) via the gateway cron. Pulls
last-7-days post-level insights from Publer for every connected social account
(facebook, instagram, twitter, tiktok, youtube, linkedin), ranks top performers,
asks DeepSeek V4 to write a "what's working / why / patterns" analysis, writes
one row into a Notion 'Weekly Analytics' database (auto-created on first run
under the content-hub page), and emails the rollup to ZEUS_NOTIFY_EMAIL using
the same backend rail as the per-post pipeline (Resend → AgentMail → Gmail SMTP
→ local file).

CLI:
    python weekly_analytics.py                      # last 7 days, full pipeline
    python weekly_analytics.py --since 2026-05-02 --to 2026-05-09
    python weekly_analytics.py --dry-run            # skip Notion + email
    python weekly_analytics.py --no-llm             # skip the writeup (cheap)
    python weekly_analytics.py --no-email           # write Notion, skip email

Exit codes: 0 on full success, 2 on Publer auth/fetch error, 3 on LLM error,
4 on Notion error, 5 on email error. Cost ledger gets one row per run with
content_type='weekly_analytics'.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional

import requests

# --- env loader -----------------------------------------------------------

def _load_dotenv() -> None:
    """Load .env from the first parent dir that has one, then ~/.hermes/.env."""
    here = Path(__file__).resolve()
    seen: set[Path] = set()
    candidates: list[Path] = []
    for parent in here.parents:
        candidates.append(parent / ".env")
    candidates.append(Path.home() / ".hermes" / ".env")
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

logging.basicConfig(
    level=os.getenv("ZEUS_ANALYTICS_LOG", "INFO"),
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger("zeus.weekly")

# --- constants -----------------------------------------------------------

PUBLER_BASE = "https://app.publer.com/api/v1"
NOTION_API = "https://api.notion.com/v1"
NOTION_TEXT_LIMIT = 2000  # per rich_text chunk

# OpenRouter model id. Default is deepseek-v4-flash because this is the model
# the rest of the cron stack uses (DEFAULT_CRON_MODEL in setup_content_cron.py)
# and because v4-pro is a reasoning model that frequently burns the entire
# max_tokens budget on hidden reasoning and emits empty content on prompts of
# this size. v4-flash delivers analysis of equivalent quality for this task at
# ~10% the cost and predictable latency. Override with ZEUS_ANALYTICS_MODEL
# (or --model) to swap in v4-pro if you want the deeper reasoning trace.
ANALYTICS_MODEL = os.getenv("ZEUS_ANALYTICS_MODEL", "deepseek/deepseek-v4-flash")

# Where the cached DB id lives (parallel to ensure_ideas_db.py / lib/notion.py).
NOTION_IDS_PATH = Path.home() / ".hermes" / "notion_ids.json"
LEDGER_PATH = Path.home() / ".hermes" / "zeus_cost_ledger.jsonl"
LOCAL_INBOX = Path.home() / ".hermes" / "zeus_email_outbox"

PLATFORM_LABEL = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "twitter": "X / Twitter",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "linkedin": "LinkedIn",
}

# --- Publer client -------------------------------------------------------

def _publer_headers() -> dict[str, str]:
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
    r = requests.get(f"{PUBLER_BASE}/accounts", headers=_publer_headers(), timeout=20)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("accounts", [])


def fetch_post_insights(account_id: str, since: date, until: date) -> list[dict]:
    """Page through /analytics/{account}/post_insights and return all posts.
    Each Publer page contains ~10 posts; we keep going until we've collected
    `total` rows or hit an empty page (whichever first)."""
    out: list[dict] = []
    page = 0
    seen_total: Optional[int] = None
    while True:
        r = requests.get(
            f"{PUBLER_BASE}/analytics/{account_id}/post_insights",
            params={
                "from": since.isoformat(),
                "to": until.isoformat(),
                "page": page,
                "sort_by": "engagement",
                "sort_type": "DESC",
            },
            headers=_publer_headers(),
            timeout=30,
        )
        if r.status_code != 200:
            log.warning(f"  insights {account_id} page={page} -> {r.status_code}: {r.text[:200]}")
            break
        body = r.json()
        posts = body.get("posts") or []
        if seen_total is None:
            seen_total = body.get("total") or 0
        if not posts:
            break
        out.extend(posts)
        if seen_total and len(out) >= seen_total:
            break
        page += 1
        if page > 20:  # hard guard; 200+ posts/week/account would be exceptional
            log.warning(f"  stopping pagination at 20 pages for {account_id}")
            break
    return out


# --- metric flattening / ranking ----------------------------------------

@dataclass
class FlatPost:
    """Publer wraps each metric as {name, value, tooltip}; flatten to scalars."""
    account_id: str
    provider: str
    post_id: str
    scheduled_at: str
    post_type: Optional[str]
    text: str
    url: Optional[str]
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def reach(self) -> float:
        return float(self.metrics.get("reach") or 0)

    @property
    def engagement(self) -> float:
        return float(self.metrics.get("engagement") or 0)

    @property
    def engagement_rate(self) -> float:
        return float(self.metrics.get("engagement_rate") or 0)


def _val(envelope: Any) -> Optional[float]:
    """Publer returns metrics as {name, value, tooltip}. Pull the value, or None."""
    if isinstance(envelope, dict):
        v = envelope.get("value")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    if isinstance(envelope, (int, float)):
        return float(envelope)
    return None


def flatten(provider: str, account_id: str, raw: dict) -> FlatPost:
    analytics = raw.get("analytics") or {}
    metrics: dict[str, float] = {}
    for key, env in analytics.items():
        v = _val(env)
        if v is not None:
            metrics[key] = v
    # Publer's response doesn't always carry the engagement total; derive if
    # missing (likes + comments + shares + saves).
    if "engagement" not in metrics:
        metrics["engagement"] = sum(
            metrics.get(k, 0) for k in ("likes", "comments", "shares", "saves")
        )
    # Permalink lives in different fields per platform; try the common ones.
    url = (
        raw.get("permalink")
        or raw.get("url")
        or (raw.get("post") or {}).get("url")
        or None
    )
    text = raw.get("text") or raw.get("title") or ""
    return FlatPost(
        account_id=account_id,
        provider=provider,
        post_id=str(raw.get("id") or ""),
        scheduled_at=str(raw.get("scheduled_at") or ""),
        post_type=raw.get("post_type"),
        text=text[:500],
        url=url,
        metrics=metrics,
    )


@dataclass
class WeekRollup:
    since: date
    until: date
    posts: list[FlatPost] = field(default_factory=list)
    by_platform: dict[str, list[FlatPost]] = field(default_factory=dict)

    @property
    def total_posts(self) -> int:
        return len(self.posts)

    @property
    def total_reach(self) -> float:
        return sum(p.reach for p in self.posts)

    @property
    def total_engagement(self) -> float:
        return sum(p.engagement for p in self.posts)

    @property
    def avg_engagement_rate(self) -> float:
        rated = [p.engagement_rate for p in self.posts if p.engagement_rate > 0]
        return round(sum(rated) / len(rated), 2) if rated else 0.0

    def top_post(self) -> Optional[FlatPost]:
        if not self.posts:
            return None
        return max(self.posts, key=lambda p: (p.engagement, p.reach))

    def top_per_platform(self) -> dict[str, FlatPost]:
        out: dict[str, FlatPost] = {}
        for platform, posts in self.by_platform.items():
            if posts:
                out[platform] = max(posts, key=lambda p: (p.engagement, p.reach))
        return out

    def platform_totals(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for platform, posts in self.by_platform.items():
            out[platform] = {
                "posts": len(posts),
                "reach": sum(p.reach for p in posts),
                "engagement": sum(p.engagement for p in posts),
                "avg_engagement_rate": round(
                    sum(p.engagement_rate for p in posts if p.engagement_rate > 0)
                    / max(1, sum(1 for p in posts if p.engagement_rate > 0)),
                    2,
                ),
            }
        return out


def gather_week(since: date, until: date) -> WeekRollup:
    accounts = list_accounts()
    log.info(f"accounts: {len(accounts)} ({', '.join(a.get('provider') or '?' for a in accounts)})")
    week = WeekRollup(since=since, until=until)
    for a in accounts:
        provider = a.get("provider") or "unknown"
        aid = a.get("id")
        if not aid:
            continue
        raw_posts = fetch_post_insights(aid, since, until)
        log.info(f"  {provider:>10}  {len(raw_posts):>3} posts in window")
        flat = [flatten(provider, aid, p) for p in raw_posts]
        week.posts.extend(flat)
        week.by_platform.setdefault(provider, []).extend(flat)
    return week


# --- LLM analysis --------------------------------------------------------

def _build_llm_prompt(week: WeekRollup) -> str:
    totals = week.platform_totals()
    top = week.top_post()
    top_per = week.top_per_platform()

    lines: list[str] = []
    lines.append(
        f"You are analyzing one week of social-media performance for an "
        f"AI/finance content brand (handle 'omnifolio'). Window: "
        f"{week.since.isoformat()} → {week.until.isoformat()}.\n"
    )
    lines.append("=== AGGREGATE TOTALS ===")
    lines.append(f"Posts published: {week.total_posts}")
    lines.append(f"Total reach: {int(week.total_reach):,}")
    lines.append(f"Total engagement: {int(week.total_engagement):,}")
    lines.append(f"Avg engagement rate: {week.avg_engagement_rate}%\n")

    lines.append("=== PER-PLATFORM ===")
    for plat, t in sorted(totals.items(), key=lambda kv: -kv[1]["engagement"]):
        lines.append(
            f"{PLATFORM_LABEL.get(plat, plat)}: {int(t['posts'])} posts, "
            f"reach {int(t['reach']):,}, engagement {int(t['engagement']):,}, "
            f"avg ER {t['avg_engagement_rate']}%"
        )
    lines.append("")

    if top:
        lines.append("=== OVERALL TOP POST ===")
        lines.append(f"Platform: {PLATFORM_LABEL.get(top.provider, top.provider)}")
        lines.append(f"Type: {top.post_type or 'unknown'}")
        lines.append(f"Reach: {int(top.reach):,}  Engagement: {int(top.engagement):,}  ER: {top.engagement_rate}%")
        if top.url:
            lines.append(f"URL: {top.url}")
        lines.append(f"Text: {top.text[:300]}")
        lines.append("")

    lines.append("=== TOP POST PER PLATFORM ===")
    for plat, p in top_per.items():
        lines.append(
            f"- {PLATFORM_LABEL.get(plat, plat)}: reach {int(p.reach):,}, "
            f"eng {int(p.engagement):,}, ER {p.engagement_rate}% — {p.text[:120]}"
        )
    lines.append("")

    lines.append("=== TASK ===")
    lines.append(
        "Write a tight executive briefing (450-700 words) covering:\n"
        "1. **What worked** — name the specific posts/formats/topics that drove the week. "
        "Cite reach/engagement numbers. Be concrete.\n"
        "2. **Why it worked** — your hypothesis on the format, hook, timing, or topic angle. "
        "Distinguish format effects from topic effects when you can.\n"
        "3. **Per-platform read** — for each platform with >0 posts, one short paragraph: "
        "is the platform pulling weight or under-performing relative to its post count?\n"
        "4. **Patterns to repeat** — 3-5 bullets, each actionable for next week's calendar.\n"
        "5. **Patterns to drop** — anything clearly not landing.\n\n"
        "Tone: Bloomberg Terminal condensed. No fluff, no 'in conclusion', no hedging. "
        "Format: GitHub-flavored markdown with H2 section headers. Numbers must match the data above."
    )
    return "\n".join(lines)


def openrouter_chat(prompt: str, *, model: str, max_tokens: int = 4000) -> tuple[str, float, str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.4,
        "usage": {"include": True},
    }
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    r.raise_for_status()
    payload = r.json()
    msg = payload["choices"][0]["message"]
    text = msg.get("content") or ""
    if not text:
        # DeepSeek v4-pro and other reasoning models can spend the entire
        # max_tokens budget on hidden reasoning and emit no content. Surface
        # what's actually wrong so the caller can bump max_tokens or switch
        # models, instead of blaming the API.
        finish = payload["choices"][0].get("native_finish_reason") or payload["choices"][0].get("finish_reason")
        usage = payload.get("usage") or {}
        reasoning_tok = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        raise RuntimeError(
            f"OpenRouter returned empty content (finish={finish}, "
            f"completion_tokens={usage.get('completion_tokens')}, "
            f"reasoning_tokens={reasoning_tok}). Bump --model max_tokens or "
            f"switch to a non-reasoning model like deepseek/deepseek-v4-flash."
        )
    usage = payload.get("usage") or {}
    raw_cost = usage.get("cost")
    cost: float
    source: str
    try:
        cost = float(raw_cost) if raw_cost is not None else 0.0
        source = "actual" if raw_cost is not None else "estimate"
    except (TypeError, ValueError):
        cost = 0.0
        source = "estimate"
    log.info(f"openrouter model={model} cost=${cost:.6f} src={source}")
    return text, cost, source


# --- Notion ---------------------------------------------------------------

def _notion_headers() -> dict[str, str]:
    key = os.environ.get("NOTION_API_KEY")
    if not key:
        raise RuntimeError("NOTION_API_KEY not set")
    return {
        "Authorization": f"Bearer {key}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _resolve_hub_page_id() -> str:
    hub = os.environ.get("ZEUS_NOTION_HUB_PAGE_ID") or os.environ.get("NOTION_CONTENT_HUB_PAGE_ID")
    if not hub and NOTION_IDS_PATH.exists():
        try:
            hub = json.loads(NOTION_IDS_PATH.read_text()).get("hub_page_id")
        except Exception:
            hub = None
    if not hub:
        raise RuntimeError(
            "Hub page id not found. Set ZEUS_NOTION_HUB_PAGE_ID or seed ~/.hermes/notion_ids.json"
        )
    # Notion accepts both hyphenated and unhyphenated. Hyphenate if needed.
    hub = hub.replace("-", "")
    if len(hub) == 32:
        hub = f"{hub[0:8]}-{hub[8:12]}-{hub[12:16]}-{hub[16:20]}-{hub[20:32]}"
    return hub


_PLATFORM_SELECT_OPTIONS = [
    {"name": "Facebook"}, {"name": "Instagram"}, {"name": "X / Twitter"},
    {"name": "TikTok"}, {"name": "YouTube"}, {"name": "LinkedIn"}, {"name": "Mixed"},
]

_STATUS_SELECT_OPTIONS = [
    {"name": "Draft"}, {"name": "Reviewed"}, {"name": "Action Taken"},
]

WANTED_PROPERTIES: dict[str, dict] = {
    "Period Start": {"date": {}},
    "Period End": {"date": {}},
    "Posts Tracked": {"number": {}},
    "Total Reach": {"number": {}},
    "Total Engagement": {"number": {}},
    "Avg Engagement Rate %": {"number": {}},
    "Top Platform": {"select": {"options": _PLATFORM_SELECT_OPTIONS}},
    "Top Post Reach": {"number": {}},
    "Top Post Engagement": {"number": {}},
    "Top Post URL": {"url": {}},
    "Per-Platform Summary": {"rich_text": {}},
    "AI Analysis": {"rich_text": {}},
    "Cost USD": {"number": {}},
    "Generated At": {"date": {}},
    "Status": {"select": {"options": _STATUS_SELECT_OPTIONS}},
}


def _list_hub_databases(hub_page_id: str) -> list[dict]:
    r = requests.get(
        f"{NOTION_API}/blocks/{hub_page_id}/children?page_size=100",
        headers=_notion_headers(),
        timeout=20,
    )
    r.raise_for_status()
    return [b for b in r.json().get("results", []) if b.get("type") == "child_database"]


def _find_weekly_db(hub_page_id: str) -> Optional[str]:
    for b in _list_hub_databases(hub_page_id):
        title = (b.get("child_database") or {}).get("title", "").lower()
        if "weekly analytics" in title or title == "weekly analytics":
            return b["id"]
    return None


def _create_weekly_db(hub_page_id: str) -> str:
    log.info(f"creating Weekly Analytics DB under hub page {hub_page_id}")
    body = {
        "parent": {"type": "page_id", "page_id": hub_page_id},
        "title": [{"type": "text", "text": {"content": "Weekly Analytics"}}],
        "properties": {
            "Title": {"title": {}},
            **WANTED_PROPERTIES,
        },
    }
    r = requests.post(f"{NOTION_API}/databases", headers=_notion_headers(), json=body, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"create weekly analytics DB failed {r.status_code}: {r.text[:400]}")
    return r.json()["id"]


def _audit_schema(db_id: str) -> None:
    r = requests.get(f"{NOTION_API}/databases/{db_id}", headers=_notion_headers(), timeout=20)
    r.raise_for_status()
    schema = r.json().get("properties", {})
    missing = {n: s for n, s in WANTED_PROPERTIES.items() if n not in schema}
    if not missing:
        return
    log.info(f"patching schema, adding: {sorted(missing.keys())}")
    r = requests.patch(
        f"{NOTION_API}/databases/{db_id}",
        headers=_notion_headers(),
        json={"properties": missing},
        timeout=20,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"weekly analytics schema patch failed {r.status_code}: {r.text[:400]}")


def ensure_weekly_db() -> str:
    hub = _resolve_hub_page_id()
    cached: dict = {}
    if NOTION_IDS_PATH.exists():
        try:
            cached = json.loads(NOTION_IDS_PATH.read_text())
        except Exception:
            cached = {}
    db_id = cached.get("weekly_analytics_db_id")
    if db_id:
        # Verify still accessible.
        r = requests.get(f"{NOTION_API}/databases/{db_id}", headers=_notion_headers(), timeout=20)
        if r.status_code == 200:
            _audit_schema(db_id)
            return db_id
        log.warning("cached weekly analytics db id no longer accessible — re-discovering")
    db_id = _find_weekly_db(hub) or _create_weekly_db(hub)
    cached["weekly_analytics_db_id"] = db_id
    NOTION_IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTION_IDS_PATH.write_text(json.dumps(cached, indent=2))
    _audit_schema(db_id)
    return db_id


def _chunk_rich_text(s: str, limit: int = NOTION_TEXT_LIMIT) -> list[dict]:
    chunks = [s[i : i + limit] for i in range(0, len(s), limit)] or [""]
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def _platform_summary_text(week: WeekRollup) -> str:
    rows = []
    for plat, t in sorted(week.platform_totals().items(), key=lambda kv: -kv[1]["engagement"]):
        rows.append(
            f"{PLATFORM_LABEL.get(plat, plat)}: {int(t['posts'])} posts | "
            f"reach {int(t['reach']):,} | eng {int(t['engagement']):,} | "
            f"ER {t['avg_engagement_rate']}%"
        )
    return "\n".join(rows)


def write_notion_row(db_id: str, week: WeekRollup, analysis: str, cost_usd: float) -> str:
    top = week.top_post()
    top_platform_label = (
        PLATFORM_LABEL.get(top.provider, top.provider).strip() if top else "Mixed"
    )
    # Normalize to one of the schema's select options (else Notion 400s).
    select_match = next(
        (opt["name"] for opt in _PLATFORM_SELECT_OPTIONS if opt["name"] == top_platform_label),
        "Mixed",
    )
    title = f"Week of {week.until.isoformat()}"

    props: dict[str, Any] = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Period Start": {"date": {"start": week.since.isoformat()}},
        "Period End": {"date": {"start": week.until.isoformat()}},
        "Posts Tracked": {"number": week.total_posts},
        "Total Reach": {"number": int(week.total_reach)},
        "Total Engagement": {"number": int(week.total_engagement)},
        "Avg Engagement Rate %": {"number": week.avg_engagement_rate},
        "Top Platform": {"select": {"name": select_match}},
        "Per-Platform Summary": {"rich_text": _chunk_rich_text(_platform_summary_text(week))},
        "AI Analysis": {"rich_text": _chunk_rich_text(analysis or "(no analysis generated)")},
        "Cost USD": {"number": round(cost_usd, 6)},
        "Generated At": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        "Status": {"select": {"name": "Draft"}},
    }
    if top:
        props["Top Post Reach"] = {"number": int(top.reach)}
        props["Top Post Engagement"] = {"number": int(top.engagement)}
        if top.url:
            props["Top Post URL"] = {"url": top.url}

    body = {"parent": {"database_id": db_id}, "properties": props}
    r = requests.post(f"{NOTION_API}/pages", headers=_notion_headers(), json=body, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"write weekly row failed {r.status_code}: {r.text[:600]}")
    page_id = r.json()["id"]
    log.info(f"  notion row written: {page_id}")
    return page_id


# --- email ---------------------------------------------------------------

def _email_subject(week: WeekRollup) -> str:
    return f"Zeus weekly analytics — wk of {week.until.isoformat()}"


def _email_text(week: WeekRollup, analysis: str, notion_url: Optional[str]) -> str:
    lines = [
        f"Zeus weekly analytics rollup",
        f"Window: {week.since} → {week.until}",
        "",
        f"Posts tracked: {week.total_posts}",
        f"Total reach: {int(week.total_reach):,}",
        f"Total engagement: {int(week.total_engagement):,}",
        f"Avg engagement rate: {week.avg_engagement_rate}%",
        "",
        "Per-platform:",
        _platform_summary_text(week),
        "",
        "=== ANALYSIS ===",
        analysis,
    ]
    if notion_url:
        lines += ["", f"Saved to Notion: {notion_url}"]
    return "\n".join(lines)


def _email_html(week: WeekRollup, analysis: str, notion_url: Optional[str]) -> str:
    rows = []
    for plat, t in sorted(week.platform_totals().items(), key=lambda kv: -kv[1]["engagement"]):
        rows.append(
            f"<tr><td>{PLATFORM_LABEL.get(plat, plat)}</td>"
            f"<td style='text-align:right'>{int(t['posts'])}</td>"
            f"<td style='text-align:right'>{int(t['reach']):,}</td>"
            f"<td style='text-align:right'>{int(t['engagement']):,}</td>"
            f"<td style='text-align:right'>{t['avg_engagement_rate']}%</td></tr>"
        )
    table = (
        "<table cellpadding='6' style='border-collapse:collapse;border:1px solid #ddd'>"
        "<thead><tr><th>Platform</th><th>Posts</th><th>Reach</th><th>Engagement</th><th>Avg ER</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )
    # Render markdown analysis as <pre> for fidelity (Resend/Gmail render OK).
    safe_analysis = (
        analysis.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    notion_link = (
        f"<p><a href='{notion_url}'>Open in Notion</a></p>" if notion_url else ""
    )
    return (
        f"<html><body style='font-family:-apple-system,sans-serif;max-width:780px'>"
        f"<h2>Zeus weekly analytics — wk of {week.until.isoformat()}</h2>"
        f"<p>Window: <b>{week.since}</b> → <b>{week.until}</b></p>"
        f"<p>Posts tracked: <b>{week.total_posts}</b> · "
        f"Total reach: <b>{int(week.total_reach):,}</b> · "
        f"Total engagement: <b>{int(week.total_engagement):,}</b> · "
        f"Avg ER: <b>{week.avg_engagement_rate}%</b></p>"
        f"{table}"
        f"<h3>Analysis</h3>"
        f"<pre style='white-space:pre-wrap;font-family:inherit;font-size:14px'>{safe_analysis}</pre>"
        f"{notion_link}"
        f"</body></html>"
    )


def _split_recipients(value: str) -> list[str]:
    return [a.strip() for a in value.split(",") if a.strip()]


def _real(v: Optional[str]) -> bool:
    return bool(v) and not v.startswith("REPLACE_WITH") and v.strip() != ""


def _pick_email_backend() -> str:
    if _real(os.getenv("RESEND_API_KEY")):
        return "resend"
    if _real(os.getenv("AGENTMAIL_API_KEY")):
        return "agentmail"
    if _real(os.getenv("HERMES_GMAIL_APP_PASSWORD")) and _real(os.getenv("HERMES_GMAIL_USER")):
        return "smtp"
    return "file"


def send_email(week: WeekRollup, analysis: str, notion_url: Optional[str]) -> str:
    recipient_raw = os.getenv("ZEUS_NOTIFY_EMAIL", "")
    to_list = _split_recipients(recipient_raw)
    if not to_list:
        raise RuntimeError("ZEUS_NOTIFY_EMAIL not set")
    subject = _email_subject(week)
    html = _email_html(week, analysis, notion_url)
    text = _email_text(week, analysis, notion_url)
    backend = _pick_email_backend()
    log.info(f"email backend: {backend} -> {to_list}")
    try:
        if backend == "resend":
            _send_resend(to_list, subject, html, text)
        elif backend == "agentmail":
            _send_agentmail(to_list, subject, html, text)
        elif backend == "smtp":
            _send_gmail_smtp(to_list, subject, html, text)
        else:
            _save_email_file(to_list, subject, html, text)
            backend = "file"
    except Exception as e:
        log.error(f"email backend {backend} failed: {e}; falling back to file")
        _save_email_file(to_list, subject, html, text)
        backend = "file"
    return backend


def _send_resend(to: list[str], subject: str, html: str, text: str) -> None:
    sender = os.getenv("RESEND_FROM") or os.getenv("ZEUS_NOTIFY_FROM_EMAIL", "")
    name = os.getenv("ZEUS_NOTIFY_FROM_NAME", "Zeus Pipeline")
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={"from": f"{name} <{sender}>", "to": to, "subject": subject, "html": html, "text": text},
        timeout=20,
    )
    r.raise_for_status()


def _send_agentmail(to: list[str], subject: str, html: str, text: str) -> None:
    inbox = os.environ.get("AGENTMAIL_INBOX")
    if not inbox:
        raise RuntimeError("AGENTMAIL_INBOX not set")
    r = requests.post(
        f"https://api.agentmail.to/v0/inboxes/{inbox}/messages/send",
        headers={
            "Authorization": f"Bearer {os.environ['AGENTMAIL_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={"to": to, "subject": subject, "html": html, "text": text},
        timeout=20,
    )
    r.raise_for_status()


def _send_gmail_smtp(to: list[str], subject: str, html: str, text: str) -> None:
    user = os.environ["HERMES_GMAIL_USER"]
    password = os.environ["HERMES_GMAIL_APP_PASSWORD"]
    msg = EmailMessage()
    msg["From"] = f"Zeus Pipeline <{user}>"
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg, to_addrs=to)


def _save_email_file(to: list[str], subject: str, html: str, text: str) -> Path:
    LOCAL_INBOX.mkdir(parents=True, exist_ok=True)
    name = datetime.utcnow().strftime("%Y%m%dT%H%M%S") + "_weekly_analytics"
    base = LOCAL_INBOX / name
    base.with_suffix(".txt").write_text(text)
    base.with_suffix(".html").write_text(html)
    base.with_suffix(".meta.json").write_text(
        json.dumps({"to": to, "subject": subject}, indent=2)
    )
    log.warning(f"no email backend; wrote to {base.with_suffix('.txt')}")
    return base


# --- ledger --------------------------------------------------------------

def append_ledger(run_id: str, cost: float, source: str, model: str) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    cost_key = f"text:{model}"
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "content_type": "weekly_analytics",
        "status": "complete",
        "total_cost_usd": round(cost, 6),
        "actual_cost_usd": round(cost, 6) if source == "actual" else 0.0,
        "estimated_cost_usd": 0.0 if source == "actual" else round(cost, 6),
        "cost_breakdown": {cost_key: round(cost, 6)},
        "cost_sources": {cost_key: source},
        "models": [model],
    }
    with LEDGER_PATH.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    log.info(f"  ledger row appended: {run_id}")


# --- CLI -----------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Weekly Publer analytics → Notion + email")
    ap.add_argument("--since", help="window start YYYY-MM-DD; default = until - 6 days")
    ap.add_argument("--to", "--until", dest="until", help="window end YYYY-MM-DD; default = today")
    ap.add_argument("--dry-run", action="store_true", help="skip Notion write + email")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM writeup (smoke test)")
    ap.add_argument("--no-email", action="store_true", help="write Notion, skip email")
    ap.add_argument("--print-analysis", action="store_true", help="print the LLM writeup to stdout (debug)")
    ap.add_argument("--model", default=ANALYTICS_MODEL, help=f"OpenRouter model id (default: {ANALYTICS_MODEL})")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    until = date.fromisoformat(args.until) if args.until else date.today()
    since = date.fromisoformat(args.since) if args.since else (until - timedelta(days=6))
    log.info(f"window: {since} → {until}")

    try:
        week = gather_week(since, until)
    except Exception as e:
        log.error(f"publer fetch failed: {e}")
        return 2

    log.info(
        f"rollup: posts={week.total_posts} reach={int(week.total_reach):,} "
        f"engagement={int(week.total_engagement):,} avgER={week.avg_engagement_rate}%"
    )
    if week.total_posts == 0:
        log.warning("no posts in window — emitting a 'quiet week' note")

    analysis = ""
    cost = 0.0
    cost_source = "estimate"
    if not args.no_llm and week.total_posts > 0:
        try:
            prompt = _build_llm_prompt(week)
            analysis, cost, cost_source = openrouter_chat(prompt, model=args.model)
        except Exception as e:
            log.error(f"llm writeup failed: {e}")
            return 3
    elif week.total_posts == 0:
        analysis = (
            "No posts went live in the window. Either the publishing pipeline "
            "didn't fire or accounts were disconnected. Check `hermes cron logs "
            "zeus-content-article-slot` for the prior week."
        )

    notion_url: Optional[str] = None
    if not args.dry_run:
        try:
            db_id = ensure_weekly_db()
            page_id = write_notion_row(db_id, week, analysis, cost)
            notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
        except Exception as e:
            log.error(f"notion write failed: {e}")
            return 4

    if not args.dry_run and not args.no_email:
        try:
            send_email(week, analysis, notion_url)
        except Exception as e:
            log.error(f"email send failed: {e}")
            return 5

    run_id = f"weekly_{since.isoformat()}_to_{until.isoformat()}"
    if not args.dry_run and cost > 0:
        append_ledger(run_id, cost, cost_source, args.model)

    if args.print_analysis and analysis:
        print("\n=== ANALYSIS ===\n")
        print(analysis)
        print("\n=== END ===\n")

    print(json.dumps({
        "run_id": run_id,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "posts": week.total_posts,
        "total_reach": int(week.total_reach),
        "total_engagement": int(week.total_engagement),
        "cost_usd": round(cost, 6),
        "notion_url": notion_url,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
