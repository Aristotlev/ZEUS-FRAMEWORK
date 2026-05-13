"""Breaking-news watcher.

Polls a small set of finance RSS feeds + Finnhub general news, dedups against
~/.hermes/breaking_news_seen.db, scores fresh headlines with one OpenRouter
call apiece, and auto-fires the ARTICLE pipeline (short, text-only) for items
clearing the threshold.

Sources are user-mandated (2026-05-13): MarketWatch + Investing.com +
InvestingLive RSS + Finnhub general news. Do not add other sources without
the user's explicit ask.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional
from xml.etree import ElementTree as ET

import requests

log = logging.getLogger(__name__)

DB_PATH = Path.home() / ".hermes" / "breaking_news_seen.db"

RSS_FEEDS: list[str] = [
    "http://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.investing.com/rss/news.rss",
    # investinglive.com/rss/ serves their marketing HTML page; /feed/ is the
    # actual application/rss+xml endpoint.
    "https://investinglive.com/feed/",
]
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"

DEDUP_WINDOW_HOURS = 48
ITEM_MAX_AGE_MINUTES = 90
SCORE_THRESHOLD = 0.7
FETCH_TIMEOUT = 15
# Investing.com 403s anything that doesn't look like a real browser.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS seen (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            score REAL,
            shipped INTEGER NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS seen_ts ON seen(ts)")
    return c


def _item_id(source: str, url: str, title: str) -> str:
    """Stable id keyed on URL when present, falling back to title hash."""
    key = url.strip() or title.strip()
    return hashlib.sha1(f"{source}|{key}".encode("utf-8", errors="ignore")).hexdigest()[:16]


def _is_seen(conn: sqlite3.Connection, item_id: str) -> bool:
    cutoff = int(time.time()) - DEDUP_WINDOW_HOURS * 3600
    row = conn.execute(
        "SELECT 1 FROM seen WHERE id = ? AND ts >= ?",
        (item_id, cutoff),
    ).fetchone()
    return row is not None


def _mark_seen(
    conn: sqlite3.Connection,
    item_id: str,
    source: str,
    title: str,
    url: str,
    score: Optional[float],
    shipped: bool,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO seen (id, source, title, url, score, shipped, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_id, source, title, url, score, 1 if shipped else 0, int(time.time())),
    )
    conn.commit()


def _prune_old(conn: sqlite3.Connection) -> None:
    cutoff = int(time.time()) - DEDUP_WINDOW_HOURS * 3600
    conn.execute("DELETE FROM seen WHERE ts < ?", (cutoff,))
    conn.commit()


def _fetch_rss(feed_url: str) -> list[dict]:
    try:
        r = requests.get(
            feed_url,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as exc:
        log.warning("rss fetch failed for %s: %s", feed_url, exc)
        return []

    items: list[dict] = []
    for entry in root.iter():
        # Match both RSS 2.0 <item> and Atom <entry>
        tag = entry.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        title = ""
        link = ""
        pub_raw = ""
        for child in entry:
            ctag = child.tag.rsplit("}", 1)[-1].lower()
            if ctag == "title" and not title:
                title = (child.text or "").strip()
            elif ctag == "link" and not link:
                # RSS 2.0: text; Atom: href attribute
                link = (child.text or "").strip() or child.get("href", "").strip()
            elif ctag in {"pubdate", "published", "updated"} and not pub_raw:
                pub_raw = (child.text or "").strip()
        if not title or not link:
            continue
        dt: Optional[datetime] = None
        if pub_raw:
            try:
                dt = parsedate_to_datetime(pub_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                dt = None
        items.append({"title": title, "url": link, "published_at": dt, "source": feed_url})
    return items


def _fetch_finnhub() -> list[dict]:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        log.info("FINNHUB_API_KEY not set; skipping Finnhub source")
        return []
    try:
        r = requests.get(
            FINNHUB_NEWS_URL,
            params={"category": "general", "token": key},
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        rows = r.json() or []
    except Exception as exc:
        log.warning("finnhub fetch failed: %s", exc)
        return []

    items: list[dict] = []
    for row in rows:
        title = (row.get("headline") or "").strip()
        url = (row.get("url") or "").strip()
        ts = row.get("datetime")
        if not title or not url:
            continue
        dt: Optional[datetime] = None
        if isinstance(ts, (int, float)) and ts > 0:
            try:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            except Exception:
                dt = None
        items.append({"title": title, "url": url, "published_at": dt, "source": "finnhub"})
    return items


def fetch_all() -> list[dict]:
    items: list[dict] = []
    for feed_url in RSS_FEEDS:
        items.extend(_fetch_rss(feed_url))
    items.extend(_fetch_finnhub())
    return items


def _is_fresh(item: dict, max_age_minutes: int = ITEM_MAX_AGE_MINUTES) -> bool:
    pub = item.get("published_at")
    if not pub:
        # No timestamp — treat as fresh, dedup handles repeats.
        return True
    return (datetime.now(timezone.utc) - pub) <= timedelta(minutes=max_age_minutes)


SCORER_PROMPT = """\
Score this financial news headline 0.00-1.00 for how Watcher.Guru-worthy it is.
Watcher.Guru posts: market-moving events, major POTUS/Fed/Treasury actions, big
company news (M&A, earnings beats/misses, exec changes), macro prints (CPI, jobs,
GDP, rate decisions), geopolitics/conflict that moves markets, and large crypto
moves. They do NOT post: op-eds, listicles, evergreen commentary, generic
analyst chatter, or already-stale stories.

HEADLINE: {title}

Rubric:
  0.90+   blockbuster: surprise Fed move, war escalation, top-cap M&A, surprise CPI
  0.70-89 market-moving: notable earnings, mid-cap M&A, exec firing, sanctions, large crypto flow
  0.50-69 noteworthy: sector trend, analyst calls, commodity moves
  <0.50   generic/commentary/listicle/repeat

Respond with ONLY the number (e.g. "0.84"). No prose.
"""


def _score_item(title: str, openrouter_chat: Callable) -> tuple[float, float]:
    """Return (score, cost_usd). Score 0.0 on any failure."""
    try:
        text, cost, _src = openrouter_chat(SCORER_PROMPT.format(title=title), max_tokens=10)
    except Exception as exc:
        log.warning("scorer failed for %r: %s", title[:80], exc)
        return 0.0, 0.0
    raw = (text or "").strip().split()
    if not raw:
        return 0.0, float(cost or 0.0)
    try:
        return max(0.0, min(1.0, float(raw[0].rstrip(",.")))), float(cost or 0.0)
    except ValueError:
        log.warning("scorer returned non-numeric for %r: %r", title[:80], text)
        return 0.0, float(cost or 0.0)


def run_once(
    *,
    threshold: float = SCORE_THRESHOLD,
    max_age_minutes: int = ITEM_MAX_AGE_MINUTES,
    dry_run: bool = False,
) -> dict:
    """Single watcher pass. Returns a summary dict (JSON-serializable)."""
    # Lazy import to avoid pulling all of pipeline_test on module import.
    _ensure_pipeline_on_path()
    from pipeline_test import openrouter_chat, run as run_pipeline  # type: ignore
    from lib.content_types import ContentType  # type: ignore

    conn = _conn()
    _prune_old(conn)

    items = fetch_all()
    summary: dict = {
        "fetched": len(items),
        "new": 0,
        "fresh": 0,
        "scored": 0,
        "score_cost_usd": 0.0,
        "shipped": [],
        "rejected": [],
        "errors": [],
    }

    for item in items:
        item_id = _item_id(item["source"], item["url"], item["title"])
        if _is_seen(conn, item_id):
            continue
        summary["new"] += 1

        if not _is_fresh(item, max_age_minutes=max_age_minutes):
            _mark_seen(conn, item_id, item["source"], item["title"], item["url"], None, False)
            continue
        summary["fresh"] += 1

        score, cost = _score_item(item["title"], openrouter_chat)
        summary["scored"] += 1
        summary["score_cost_usd"] = round(summary["score_cost_usd"] + cost, 6)
        log.info("score=%.2f | %s", score, item["title"][:100])

        if score < threshold:
            _mark_seen(conn, item_id, item["source"], item["title"], item["url"], score, False)
            summary["rejected"].append({"title": item["title"], "score": score, "url": item["url"]})
            continue

        if dry_run:
            log.info("[DRY] would ship: %s", item["title"])
            _mark_seen(conn, item_id, item["source"], item["title"], item["url"], score, False)
            summary["shipped"].append(
                {"title": item["title"], "score": score, "url": item["url"], "dry": True}
            )
            continue

        try:
            piece = run_pipeline(
                content_type=ContentType.ARTICLE,
                topic=item["title"],
                slides=4,
                duration=5,
                do_publish=True,
                audio_mode=None,
                wait_for_live=False,
                quality=None,
                picker_cost=None,
                avatar_mode="talking",
                source_url=item["url"],
                source_niche="finance",
            )
            run_id = getattr(piece, "run_id", "")
            _mark_seen(conn, item_id, item["source"], item["title"], item["url"], score, True)
            summary["shipped"].append(
                {"title": item["title"], "score": score, "url": item["url"], "run_id": run_id}
            )
            log.info("shipped run_id=%s: %s", run_id, item["title"])
        except Exception as exc:
            # Don't mark seen — let it retry on the next pass.
            log.exception("ARTICLE pipeline failed for: %s", item["title"])
            summary["errors"].append(
                {"title": item["title"], "score": score, "url": item["url"], "error": str(exc)}
            )

    conn.close()
    return summary


def _ensure_pipeline_on_path() -> None:
    """Allow `from pipeline_test import ...` regardless of CWD."""
    here = Path(__file__).resolve()
    scripts_dir = here.parent.parent / "scripts"
    pipeline_root = here.parent.parent
    for p in (str(scripts_dir), str(pipeline_root)):
        if p not in sys.path:
            sys.path.insert(0, p)
