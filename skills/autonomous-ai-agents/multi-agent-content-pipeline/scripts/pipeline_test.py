#!/usr/bin/env python3
"""
Zeus Content Pipeline — orchestrator + on-demand test runner.

Generates one of four content types and ALWAYS archives to Notion before any
publishing step, so generation spend can never be lost again.

Stack (May 2026):
    Text:  OpenRouter (gemini-2.5-flash)
    Media: fal.ai (GPT Image 2 for images, Kling 2.5 Turbo Pro for video)
    Archive: Notion (your content-hub page -> Archive DB)
    Publish: Publer (optional, with --publish flag)

Usage:
    export $(grep -v '^#' ~/.hermes/.env | xargs)
    python3 pipeline_test.py --type article --topic "Bitcoin breaks 100K"
    python3 pipeline_test.py --type carousel --topic "..." --slides 4
    python3 pipeline_test.py --type short_video --topic "..." --duration 8
    python3 pipeline_test.py --type long_video --topic "..." --duration 10 --publish

Required env:
    OPENROUTER_API_KEY  — text generation
    FAL_KEY             — image/video generation
    NOTION_API_KEY      — archive
    PUBLER_API_KEY      — only if --publish
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

# Make sibling lib/ package importable when running this script directly.
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))


def _load_env_file() -> None:
    # The cron agent shells out via execute_code, which spawns a clean subprocess
    # that does NOT inherit the gateway's env — so OPENROUTER_API_KEY / FAL_KEY /
    # NOTION_API_KEY / PUBLER_API_KEY come back missing and the script aborts at
    # exit code 2. Stdlib parse, since python-dotenv is not in the system python.
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
    AudioMode,
    ContentPiece,
    ContentType,
    GeneratedAsset,
    LIMITS,
    NotionArchive,
    download,
    generate_image,
    generate_video_kling,
    ledger_append,
    ledger_checkpoint,
    mix_audio_for_video,
    needs_thread,
    publish_enqueue,
    send_pipeline_summary,
    split_thread,
)
from lib.paths import zeus_data_path  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("zeus-pipeline")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
ORCHESTRATOR_MODEL = "google/gemini-2.5-flash"
# Picker model is intentionally different from the orchestrator: the picker
# needs to know what's actually in today's news, not regurgitate famous stories
# from its training cutoff.
#
# Re-tested 2026-05-06 under the NEW strict contract (must return JSON with
# canonical source_url + ISO 8601 published_at + cite an allowlisted domain):
#   google/gemini-2.5-flash:online ✅ $0.022 — real bloomberg/cnbc URLs, exact
#                                              ISO timestamps, obeys allowlist
#                                              (DEFAULT under strict regime)
#   perplexity/sonar-pro           ❌ $0.006 — citations are reutersbest.com /
#                                              YouTube / agency pages, not real
#                                              news articles. Fails domain check
#                                              and returns NO_RECENT_STORY.
#   openai/gpt-4o-mini:online      ✅ $0.021 — works (alternate)
# Override via PICKER_MODEL env if you want to swap.
PICKER_MODEL = os.getenv("PICKER_MODEL", "google/gemini-2.5-flash:online")

# Publer (only used with --publish)
PUBLER_BASE = "https://app.publer.com/api/v1"
PUBLER_KEY = os.getenv("PUBLER_API_KEY", "")
PUBLER_AUTH = f"Bearer-API {PUBLER_KEY}"
PUBLER_WORKSPACE = os.getenv("PUBLER_WORKSPACE_ID", "")
PUBLER_ACCOUNTS = {
    "twitter": os.getenv("PUBLER_TWITTER_ID", ""),
    "instagram": os.getenv("PUBLER_INSTAGRAM_ID", ""),
    "linkedin": os.getenv("PUBLER_LINKEDIN_ID", ""),
    "tiktok": os.getenv("PUBLER_TIKTOK_ID", ""),
    "youtube": os.getenv("PUBLER_YOUTUBE_ID", ""),
    "reddit": os.getenv("PUBLER_REDDIT_ID", ""),
    "facebook": os.getenv("PUBLER_FACEBOOK_ID", ""),
}

# Image dimensions per content type. Carousel uses 2:3 portrait — IG, LinkedIn,
# and TikTok all give portrait carousels noticeably more feed real estate than
# 1:1 squares. Twitter renders portrait fine. Article stays square (single
# image, in-feed scroll-stopper). Override per-run via --quality.
IMAGE_SPECS = {
    ContentType.ARTICLE: (1024, 1024, "medium"),
    ContentType.CAROUSEL: (1024, 1536, "medium"),
    # videos don't use images directly, but a thumbnail is generated for the Notion record
}


# ---------------------------------------------------------------------------
# Niche loading — pulls content_pipeline.niche from ~/.hermes/config.yaml.
# Without this the LLM produces generic copy regardless of what the user's
# pipeline is actually about (whatever niche/subcategories you've configured).
# ---------------------------------------------------------------------------
def _candidate_config_paths() -> list[pathlib.Path]:
    """Where to look for hermes config, in priority order.

    HERMES_HOME first (set in containerised/prod deploys, points at the live
    config the gateway is actually using), then the conventional dev path
    `~/.hermes/config.yaml`. Without HERMES_HOME-awareness, prod runs invoked
    via `docker exec` saw an empty niche regardless of the configured value.
    """
    paths: list[pathlib.Path] = []
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        paths.append(pathlib.Path(hermes_home) / "config.yaml")
    paths.append(pathlib.Path(os.path.expanduser("~/.hermes/config.yaml")))
    return paths


def _load_niche() -> list[str]:
    cfg_path = next((p for p in _candidate_config_paths() if p.exists()), None)
    if cfg_path is None:
        return []
    try:
        import yaml  # type: ignore

        with cfg_path.open() as fh:
            cfg = yaml.safe_load(fh) or {}
        niche = (cfg.get("content_pipeline") or {}).get("niche") or []
        if isinstance(niche, str):
            niche = [niche]
        return [str(n).strip() for n in niche if str(n).strip()]
    except Exception as e:  # pyyaml missing or malformed file — degrade to no-niche, log loudly
        log.warning(f"niche: could not read {cfg_path} ({e}); proceeding without niche context")
        return []


NICHE: list[str] = _load_niche()
if NICHE:
    log.info(f"niche: {', '.join(NICHE)}")
else:
    log.warning("niche: empty — content will be generic. Set content_pipeline.niche in ~/.hermes/config.yaml")


# ---------------------------------------------------------------------------
# Source allowlist — auto-pick MUST cite a story from one of these domains.
# Without this, "in the last 12 hours" is unenforceable: the LLM can pick
# anything and we have no way to verify provenance. With it: the picker
# returns a JSON object {topic, source_url, published_at_utc}, we parse the
# URL host and the timestamp, and we reject anything that doesn't match a
# domain on the per-niche allowlist OR is older than the freshness window.
#
# Defaults below are user-curated (2026-05-06). Override per-niche via
# `content_pipeline.sources` in ~/.hermes/config.yaml.
# ---------------------------------------------------------------------------
# Allowlist note (2026-05-08): domains that 4xx the prod VM (Hetzner FSN1
# datacenter IP) regardless of UA were removed — keeping them in the list
# meant the picker would land on them ~half the time and the verifier would
# reject every one, killing the slot. Confirmed-blocked from prod (so out):
# cnbc.com, bloomberg.com, wsj.com, reuters.com, theblock.co, economist.com.
# The replacements are all top-tier (MarketWatch is Dow Jones, AP is wire-of-
# record, Fortune/BI/Axios/Guardian/TechCrunch are major desks).
#
# Expansion (2026-05-10): widened thin niches after a forex slot lost the
# ~06:00 cron tick — picker found two candidates, both off the 5-domain list,
# slot dropped. Additions below are all (a) datacenter-IP friendly per spot
# checks, (b) carry parseable <meta property="article:published_time"> or
# JSON-LD datePublished, (c) aren't behind a hard paywall. Skipped:
# theinformation.com / foreignpolicy.com (paywalled), semianalysis.com
# (Substack, no consistent pub-date metadata).
DEFAULT_SOURCES_BY_NICHE: dict[str, list[str]] = {
    "finance": [
        "finance.yahoo.com", "ft.com", "marketwatch.com",
        "businessinsider.com", "fortune.com", "axios.com",
        "investing.com", "barrons.com",
        "pymnts.com", "finextra.com",
        "unusualwhales.com",
    ],
    "stocks": [
        "finance.yahoo.com", "marketwatch.com", "seekingalpha.com",
        "businessinsider.com", "investing.com", "fortune.com",
        "barrons.com",
        "benzinga.com", "nasdaq.com",
        "unusualwhales.com",
    ],
    "forex": [
        "ft.com", "marketwatch.com", "investing.com",
        "businessinsider.com", "axios.com",
        "fxstreet.com", "dailyfx.com", "forexlive.com", "kitco.com",
        "unusualwhales.com",
    ],
    "crypto": [
        "coindesk.com", "decrypt.co", "cointelegraph.com",
        "bitcoinmagazine.com", "cryptoslate.com", "blockworks.co",
        "dlnews.com",
        "unusualwhales.com",
    ],
    "geopolitics": [
        "aljazeera.com", "bbc.com", "ft.com", "apnews.com",
        "dw.com", "theguardian.com",
        "france24.com", "politico.eu", "npr.org",
        "unusualwhales.com",
    ],
    "ai_economy": [
        "newsdigest.ai", "techcrunch.com", "theverge.com",
        "venturebeat.com", "arstechnica.com", "axios.com",
        "wired.com", "technologyreview.com", "theregister.com",
        "restofworld.org",
        "unusualwhales.com",
    ],
}


def _normalize_niche(name: str) -> str:
    # SOURCES_BY_NICHE keys use underscores ("ai_economy") but users configure
    # niches in YAML with whatever spacing they like ("ai economy"). Without
    # this, the rotation silently fails the slot every time it lands on a
    # space-separated niche — picker raises "no source allowlist" before any
    # API call, slot is lost.
    return " ".join((name or "").strip().lower().split()).replace(" ", "_")


def _load_sources_override() -> dict[str, list[str]]:
    """Read content_pipeline.sources from hermes config; return {} if missing.

    Caller merges this on top of DEFAULT_SOURCES_BY_NICHE so YAML wins per-key
    but absent entries fall back to defaults — that way a partial override
    (e.g. only `crypto:`) doesn't wipe out other niches.
    """
    cfg_path = next((p for p in _candidate_config_paths() if p.exists()), None)
    if cfg_path is None:
        return {}
    try:
        import yaml  # type: ignore
        with cfg_path.open() as fh:
            cfg = yaml.safe_load(fh) or {}
        srcs = (cfg.get("content_pipeline") or {}).get("sources") or {}
        out: dict[str, list[str]] = {}
        for niche, domains in srcs.items():
            if isinstance(domains, str):
                domains = [domains]
            cleaned = [str(d).strip().lower().lstrip(".") for d in (domains or []) if str(d).strip()]
            if cleaned:
                out[_normalize_niche(str(niche))] = cleaned
        return out
    except Exception as e:
        log.warning(f"sources: could not read override from {cfg_path} ({e}); using defaults")
        return {}


SOURCES_BY_NICHE: dict[str, list[str]] = {
    **{k: list(v) for k, v in DEFAULT_SOURCES_BY_NICHE.items()},
    **_load_sources_override(),
}


def _allowed_domains_for_niches(niches: list[str]) -> set[str]:
    """Union of allowed domains across the configured niches (lowercased)."""
    out: set[str] = set()
    for n in niches:
        for d in SOURCES_BY_NICHE.get(_normalize_niche(n), []):
            out.add(d.strip().lower().lstrip("."))
    return out


# ---------------------------------------------------------------------------
# Niche rotation — every --auto run picks ONE niche from NICHE in round-robin
# order, persisted to ~/.hermes/niche_rotation.json. Without rotation, the
# picker faces the full niche list every time and "hottest story wins" — in
# practice that means finance/geopolitics dominate and ai_economy / forex
# rarely appear. Round-robin gives even coverage: with 6 niches and ~5 cron
# slots/day, every niche gets a turn every ~1.2 days.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Picker proxy — opt-in residential proxy for the verifier's HTTP fetches.
# Used to (a) bypass publisher CDN datacenter-IP blocks (CNBC/Bloomberg/WSJ
# etc. 4xx the Hetzner IP regardless of UA) and (b) target geo-localized
# news by encoding the country in the proxy creds (Bright Data syntax:
# user-country-XX). Only the picker verifier uses this — fal, Publer,
# OpenRouter, Notion go direct (proxying them adds latency without moving
# audience reach). Unset = passthrough (current default, zero risk).
# ---------------------------------------------------------------------------
def _picker_proxies() -> Optional[dict]:
    url = os.environ.get("ZEUS_PICKER_PROXY_URL", "").strip()
    if not url:
        return None
    return {"http": url, "https": url}


def _log_proxy_status_once() -> None:
    raw = os.environ.get("ZEUS_PICKER_PROXY_URL", "").strip()
    if not raw:
        log.info("picker proxy: disabled (ZEUS_PICKER_PROXY_URL unset) — direct HTTP")
        return
    try:
        from urllib.parse import urlparse
        p = urlparse(raw)
        host_port = p.netloc.split("@", 1)[-1] if "@" in p.netloc else p.netloc
        log.info(f"picker proxy: enabled → {p.scheme}://<creds>@{host_port}")
    except Exception:
        log.info("picker proxy: enabled (unparseable URL — creds redacted)")


_log_proxy_status_once()


_NICHE_ROTATION_PATH = zeus_data_path("niche_rotation.json")

# ---------------------------------------------------------------------------
# Picker history — every accepted pick is recorded (url, topic, niche,
# timestamp). On the next pick we (a) tell the LLM to AVOID these recent URLs
# in its prompt, and (b) reject post-hoc if it picks one anyway. Without this
# the picker reconverges on whatever's hottest, so consecutive niche slots
# (e.g. finance at 12:00 and stocks at 14:00) often returned the SAME story
# — a single Alphabet/Nvidia headline got published twice on different dates.
# ---------------------------------------------------------------------------
_PICKER_HISTORY_PATH = zeus_data_path("picker_history.json")
# 7-day window; long enough to cover a story's news cycle, short enough not
# to bloat the prompt. ~12 picks/day × 7 = 84, capped at 120 below for safety.
_PICKER_HISTORY_DEDUP_HOURS = 24 * 7
_PICKER_HISTORY_MAX = 120


def _load_picker_history() -> list[dict]:
    try:
        with _PICKER_HISTORY_PATH.open() as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def _recent_picked_urls(window_hours: int = _PICKER_HISTORY_DEDUP_HOURS) -> list[dict]:
    """Picker history entries from the last `window_hours` hours, oldest-first.
    Each entry: {niche, topic, url, picked_at}."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    out: list[dict] = []
    for entry in _load_picker_history():
        try:
            picked = datetime.fromisoformat(str(entry.get("picked_at", "")))
            if picked.tzinfo is None:
                picked = picked.replace(tzinfo=timezone.utc)
            if picked >= cutoff and entry.get("url"):
                out.append(entry)
        except (ValueError, TypeError):
            continue
    return out


def _record_picker_choice(niche: str, topic: str, url: str) -> None:
    hist = _load_picker_history()
    hist.append({
        "niche": niche,
        "topic": topic,
        "url": url,
        "picked_at": datetime.now(timezone.utc).isoformat(),
    })
    hist = hist[-_PICKER_HISTORY_MAX:]
    try:
        _PICKER_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _PICKER_HISTORY_PATH.open("w") as fh:
            json.dump(hist, fh, indent=2)
    except OSError as e:
        log.warning(f"picker history: could not persist ({e}) — next pick may dupe")


def _pick_next_niche() -> str:
    """Read rotation state, return next niche, advance and persist. On any
    error (missing file, corrupt JSON, niche list shrunk) reset to index 0
    rather than crash — a run is more important than perfect ordering."""
    if not NICHE:
        raise RuntimeError("niche rotation: NICHE list is empty")
    idx = 0
    try:
        with _NICHE_ROTATION_PATH.open() as fh:
            state = json.load(fh)
        idx = int(state.get("index", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError, TypeError):
        idx = 0
    chosen = NICHE[idx % len(NICHE)]
    next_idx = (idx + 1) % (len(NICHE) * 1000)  # cap so the int doesn't grow unboundedly
    try:
        _NICHE_ROTATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _NICHE_ROTATION_PATH.open("w") as fh:
            json.dump(
                {
                    "index": next_idx,
                    "last_niche": chosen,
                    "advanced_at": datetime.now(timezone.utc).isoformat(),
                    "configured_niches": list(NICHE),
                },
                fh,
                indent=2,
            )
    except OSError as e:
        log.warning(
            f"niche rotation: could not persist state to {_NICHE_ROTATION_PATH} "
            f"({e}) — next run may repeat this niche"
        )
    return chosen


def _url_host(url: str) -> str:
    """Lowercased hostname for `url`, or '' if unparseable / scheme-less."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return ""
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def _host_in_allowlist(host: str, allowed: set[str]) -> bool:
    """True if `host` exactly matches an allowed domain or is a subdomain of one."""
    h = host.lower().lstrip(".")
    if h.startswith("www."):
        h = h[4:]
    for d in allowed:
        d = d.lower().lstrip(".")
        if not d:
            continue
        if h == d or h.endswith("." + d):
            return True
    return False


def _parse_iso8601_utc(s: str) -> Optional[datetime]:
    """Parse ISO-8601-ish timestamps (handles trailing Z, naive→UTC). Returns
    None on failure rather than raising — callers reject with a clear log."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fetch_pub_date_from_url(url: str) -> Optional[datetime]:
    """Fetch `url` and extract the real published date from page metadata.

    The picker model has been caught hallucinating fresh timestamps for
    archival stories (2022 FTX retros surfacing as "2 hours ago" on
    2026-05-06). This fetcher is the second line of defense — we trust
    the article's own ``article:published_time`` / JSON-LD ``datePublished``
    over anything the picker claims. Returns None on any failure; the
    caller treats that as a rejection (better to skip the slot than ship
    stale content).
    """
    # Use a current Chrome UA + browser-shaped headers. CNBC, Yahoo Finance,
    # WSJ etc. 403 anything that self-identifies as a bot, which silently
    # killed every finance/stocks slot (the picker found a real story, the
    # verifier got blocked, and the slot was rejected as "unverified").
    try:
        r = requests.get(
            url,
            timeout=10,
            proxies=_picker_proxies(),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                # No "br" — requests doesn't decompress brotli without the
                # brotli package, and silently hands you raw compressed bytes
                # if the server picks it. That makes every regex match fail.
                "Accept-Encoding": "gzip, deflate",
                "Referer": "https://www.google.com/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "cross-site",
                "Upgrade-Insecure-Requests": "1",
            },
            allow_redirects=True,
        )
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning(f"auto-pick: pub-date fetch failed ({url}): {e}")
        return None

    import re
    # OpenGraph/AMP/news-meta tags first — they're authored as full ISO
    # timestamps. JSON-LD's "datePublished" comes last because publishers
    # frequently emit only the date there (e.g. BusinessInsider:
    # "datePublished":"2026-05-07") while the full timestamp lives in a
    # <meta> tag — picking up the date-only first made stories look
    # midnight-published and dropped them outside the 24h window.
    patterns = (
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
        r'<meta[^>]+name=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']datePublished["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']datePublished["\']',
        r'<meta[^>]+name=["\']pubdate["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']publish[-_]?date["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)["\']',
        r'<time[^>]+pubdate[^>]+datetime=["\']([^"\']+)["\']',
        r'<time[^>]+datetime=["\']([^"\']+)["\'][^>]+pubdate',
        r'"datePublished"\s*:\s*"([^"]+)"',
    )
    fallback: Optional[datetime] = None
    for pat in patterns:
        for m in re.finditer(pat, html, flags=re.IGNORECASE):
            dt = _parse_iso8601_utc(m.group(1))
            if dt is None:
                continue
            # Prefer any timestamp that carries an actual time-of-day; date-
            # only matches resolve to 00:00 UTC and would falsely age the
            # story by up to a day. Keep one as a last-resort fallback if
            # nothing better turns up.
            if "T" in m.group(1):
                return dt
            if fallback is None:
                fallback = dt
    return fallback


def _extract_json_object(text: str) -> Optional[dict]:
    """Defensively pull a JSON object out of `text`. Handles ```json fences,
    leading prose ('Here is the result:'), and trailing citation blocks."""
    if not text:
        return None
    s = text.strip()
    # Strip fenced code blocks
    if s.startswith("```"):
        s = s.lstrip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
        # remove a closing fence if present
        if s.endswith("```"):
            s = s[:-3].strip()
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    # Fall back to substring between the first { and last }
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            v = json.loads(s[start:end + 1])
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _niche_clause() -> str:
    if not NICHE:
        return ""
    return (
        f"\nDOMAIN: this piece is for a {' / '.join(NICHE)} audience. "
        f"Use vocabulary, references, tickers, and framing native to those fields. "
        f"No generic platitudes — be specific to the domain.\n"
    )


MAX_NICHE_ATTEMPTS_PER_SLOT = 3


def auto_pick_topic(content_type: "ContentType") -> tuple[str, float, str]:
    """Pick a current, niche-specific topic when no --topic is provided.

    Strict provenance contract (added 2026-05-06): the picker MUST return a
    JSON object with `topic`, `source_url`, and `published_at_utc`. We then
    programmatically verify (a) the URL host is on the per-niche allowlist
    and (b) the publish timestamp is within the freshness window. Anything
    else is rejected — this is the only way "from headlines in the last 12h"
    becomes enforceable instead of a wish in the prompt.

    Niche scope: every run starts with one niche from the configured list,
    chosen by round-robin rotation (see _pick_next_niche). If that niche's
    picker rejects (no qualifying story / no allowlist), we advance to the
    next niche in rotation and try again, up to MAX_NICHE_ATTEMPTS_PER_SLOT
    distinct niches (added 2026-05-10). This prevents a single thin/slow
    niche from burning a 2h slot entirely. Niches that "had their turn"
    (success or fail) move to the back of the rotation, so fairness is
    preserved across slots — a niche that failed isn't immediately retried.

    Freshness fallback (per niche): try 24h first; if no valid story is
    found, retry once with a 72h window (logged loudly). 72h is the hard
    cap (user-mandated 2026-05-07: "make the window 72 hours" — articles
    are still relevant within that window for the niches we cover; older
    = stale). Both windows failing on a niche → advance to the next niche.

    Returns (topic, cost_usd, cost_source). Cost is the sum of ALL picker
    calls made across every attempted niche this slot.
    """
    if not NICHE:
        raise RuntimeError(
            "auto-pick: no niche configured. Set content_pipeline.niche in "
            "~/.hermes/config.yaml (e.g. [finance, crypto, geopolitics]) "
            "or pass --topic explicitly."
        )

    type_hint = {
        ContentType.ARTICLE: "punchy article (one angle, one strong take)",
        ContentType.LONG_ARTICLE: "deep-dive analysis (multi-angle, data-rich)",
        ContentType.CAROUSEL: "visual-breakdown story (timeline, ranking, or step-by-step)",
        ContentType.SHORT_VIDEO: "high-energy 30-90s video story",
        ContentType.LONG_VIDEO: "explainer / breakdown video",
    }.get(content_type, "story")

    total_cost = 0.0
    cost_source = "actual"
    tried: list[tuple[str, str]] = []  # (niche, reason_rejected)
    seen_niches: set[str] = set()
    max_attempts = min(MAX_NICHE_ATTEMPTS_PER_SLOT, len(NICHE))

    for attempt in range(max_attempts):
        active_niche = _pick_next_niche()
        # Defend against tiny NICHE lists wrapping around mid-slot.
        if active_niche in seen_niches:
            break
        seen_niches.add(active_niche)

        log.info(
            f"auto-pick: rotation → '{active_niche}' "
            f"(attempt {attempt + 1}/{max_attempts})"
        )

        active_niches = [active_niche]
        allowed = _allowed_domains_for_niches(active_niches)
        if not allowed:
            log.warning(
                f"auto-pick: niche {active_niche!r} has no source allowlist "
                f"configured — skipping to next niche. Add it to "
                f"DEFAULT_SOURCES_BY_NICHE or content_pipeline.sources in "
                f"~/.hermes/config.yaml to unblock."
            )
            tried.append((active_niche, "no source allowlist configured"))
            continue

        last_failure = ""
        # Two-pass with shared 72h max-age: 24h prompt first, then 72h prompt
        # if nothing qualifying. max_age_hours=72 keeps the verifier lenient
        # so a 37.9h-old allowlisted story doesn't get rejected just because
        # the prompt asked for 24h.
        for window_hours in (24, 72):
            topic, url, published, cost, csource = _pick_with_constraints(
                type_hint, allowed, window_hours, active_niches, max_age_hours=72
            )
            total_cost += cost
            if csource == "estimate":
                cost_source = "estimate"
            if topic:
                tag = "" if window_hours == 24 else f" [RETRY {window_hours}h]"
                log.info(
                    f"auto-pick{tag}: niche={active_niche} '{topic}' "
                    f"(model={PICKER_MODEL}, src={url}, published={published}, "
                    f"cost ~${total_cost:.5f}, source={cost_source})"
                )
                if window_hours > 24:
                    log.warning(
                        f"auto-pick: 24h prompt yielded no candidate for niche "
                        f"{active_niche!r} within allowlist {sorted(allowed)} — "
                        f"retried with {window_hours}h prompt. If this happens "
                        f"often, consider expanding sources for this niche."
                    )
                if tried:
                    log.warning(
                        f"auto-pick: succeeded on niche {active_niche!r} "
                        f"after earlier rejections — "
                        f"{', '.join(f'{n} ({r})' for n, r in tried)}"
                    )
                _record_picker_choice(active_niche, topic, url)
                return topic, total_cost, cost_source
            last_failure = f"no qualifying story in {window_hours}h window"

        log.warning(
            f"auto-pick: niche {active_niche!r} rejected (24h + 72h windows "
            f"both empty within allowlist) — advancing to next niche"
        )
        tried.append((active_niche, last_failure))

    tried_summary = "; ".join(f"{n}: {r}" for n, r in tried)
    raise RuntimeError(
        f"auto-pick: picker model {PICKER_MODEL} could not find a real story "
        f"in the last 72h for any of the {len(tried)} niche(s) attempted "
        f"this slot ({tried_summary}). Pass --topic explicitly, widen the "
        f"allowlists, or set PICKER_MODEL to a different web-search model. "
        f"(cost spent: ${total_cost:.5f})"
    )


def _pick_with_constraints(
    type_hint: str, allowed: set[str], window_hours: int, active_niches: list[str],
    max_age_hours: Optional[int] = None,
) -> tuple[str, str, str, float, str]:
    """Single picker call constrained to `allowed` domains and freshness.

    `window_hours` controls the PROMPT — what we ask the model to find.
    `max_age_hours` controls the VERIFIER — what we'll actually accept after
    fetching the page's real publish date. Defaults to `window_hours` (strict).
    Setting max_age_hours > window_hours biases toward fresh stories via the
    prompt while still accepting reasonably-stale ones without an extra API
    call (fixes the 2026-05-10 regression where the 24h pass rejected a 37.9h
    story, then the 72h retry returned a different off-allowlist story and
    burned a second picker call for nothing — losing the slot entirely).

    Returns (topic, source_url, published_at_iso, cost, cost_source).
    Empty topic means the picker call failed validation — caller decides
    whether to widen the window or give up. Validation failures log a warning
    and return; only empty/unparseable picker responses are silent.
    """
    if max_age_hours is None:
        max_age_hours = window_hours
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y at %H:%M UTC")
    domain_list = ", ".join(sorted(allowed))
    niche_str = " / ".join(active_niches)

    recent = _recent_picked_urls()
    avoid_block = ""
    if recent:
        # Show the most recent first; cap at 30 to keep the prompt small while
        # still covering ~2.5 days of picks at the current cron rate.
        recent_lines = [
            f'- {e["url"]}  ("{e.get("topic","")[:80]}", {e.get("niche","?")})'
            for e in recent[-30:][::-1]
        ]
        avoid_block = (
            f"\nDO NOT pick any story whose source_url matches — or whose "
            f"headline is the same news event as — any of these recently-"
            f"covered stories (we already published these):\n"
            + "\n".join(recent_lines)
            + "\n\nPick a DIFFERENT, fresh story."
        )

    prompt = (
        f"You are a news picker. Today is {today}.\n\n"
        f"Search the live web RIGHT NOW for the single most newsworthy "
        f"{niche_str} story PUBLISHED IN THE LAST {window_hours} HOURS.\n\n"
        f"HARD REQUIREMENTS:\n"
        f"1. The story MUST be cited from one of these domains (no others): "
        f"{domain_list}\n"
        f"2. The story MUST have been published within the last {window_hours} "
        f"hours from now ({today}). Not days, not weeks — hours.\n"
        f"3. Output ONLY a JSON object — no preamble, no markdown fences, no "
        f"commentary, no trailing prose.\n"
        f"{avoid_block}\n\n"
        f"JSON schema:\n"
        f"{{\n"
        f'  "headline": "<exact published headline>",\n'
        f'  "topic": "<6-14 word topic suitable for a {type_hint}, concrete: '
        f'tickers/names/numbers, no dates, no quotes>",\n'
        f'  "source_url": "<https://... full canonical URL of the source '
        f'article on one of the allowed domains>",\n'
        f'  "published_at_utc": "<ISO 8601 UTC timestamp like '
        f'2026-05-06T14:30:00Z>"\n'
        f"}}\n\n"
        f"If no qualifying story exists in the last {window_hours} hours from "
        f"the allowed domains, output exactly: "
        f'{{"error": "NO_RECENT_STORY"}}'
    )

    # NOTE: not using json_mode=True here — perplexity/sonar-pro rejects
    # `response_format: json_object`, accepting only text/json_schema/regex.
    # The prompt is explicit and _extract_json_object defensively strips
    # ```json fences and surrounding prose, so we get reliable JSON without
    # coupling to a single picker model's API surface.
    # max_tokens=900 because gemini frequently wraps in ```json fences and
    # adds prose before the JSON; 500 truncated mid-object on long topics.
    text, cost, csource = openrouter_chat(
        prompt, max_tokens=900, model=PICKER_MODEL
    )
    data = _extract_json_object(text)
    if data is None:
        log.warning(
            f"auto-pick: picker returned non-JSON ({window_hours}h window): "
            f"{text[:200]!r}"
        )
        return "", "", "", cost, csource

    if "error" in data or "NO_RECENT_STORY" in str(data.get("error", "")).upper():
        log.info(
            f"auto-pick: picker reported no qualifying story "
            f"({window_hours}h window, error={data.get('error')!r})"
        )
        return "", "", "", cost, csource

    topic = str(data.get("topic", "")).strip().strip('"').strip("'").rstrip(".")
    url = str(data.get("source_url", "")).strip()
    published = str(data.get("published_at_utc", "")).strip()

    if not topic:
        log.warning(f"auto-pick: rejected — empty 'topic' field in {data!r}")
        return "", "", "", cost, csource
    if len(topic.split()) > 25:  # apology-paragraph guard preserved
        log.warning(f"auto-pick: rejected — topic too long ({len(topic.split())} words): {topic[:120]!r}")
        return "", "", "", cost, csource

    host = _url_host(url)
    if not host:
        log.warning(f"auto-pick: rejected — unparseable source_url={url!r}")
        return "", "", "", cost, csource
    if not _host_in_allowlist(host, allowed):
        log.warning(
            f"auto-pick: rejected — host {host!r} not in allowlist "
            f"{sorted(allowed)} (url={url!r})"
        )
        return "", "", "", cost, csource

    # Cross-niche dedup: even with the AVOID block in the prompt the picker
    # sometimes ignores it and re-returns a story we already covered. Hard
    # reject if its url is in our recent history. The caller's window-widen
    # retry will give it another shot at finding something new.
    recent_urls = {e["url"] for e in _recent_picked_urls() if e.get("url")}
    if url in recent_urls:
        log.warning(
            f"auto-pick: rejected — already covered recently (url={url!r}). "
            f"Picker ignored AVOID list."
        )
        return "", "", "", cost, csource

    # Trust-but-verify (added 2026-05-07): the picker model has been caught
    # fabricating fresh timestamps for archival stories — a 2022 FTX retro
    # got published as breaking news because the model claimed it was 2h
    # old. Fetch the URL and use the page's own pub-date metadata as the
    # authoritative timestamp. If we can't verify, REJECT — better to skip
    # a slot than ship stale content. The picker will retry the next call
    # with a different story / wider window.
    claimed_dt = _parse_iso8601_utc(published)
    real_dt = _fetch_pub_date_from_url(url)
    if real_dt is None:
        log.warning(
            f"auto-pick: rejected — could not verify published date for "
            f"{url!r} via HTTP fetch (picker claimed published_at_utc="
            f"{published!r}). Refusing to trust unverified dates."
        )
        return "", "", "", cost, csource

    pub_dt = real_dt
    if claimed_dt is not None:
        diff_h = abs((real_dt - claimed_dt).total_seconds()) / 3600.0
        if diff_h > 6.0:
            log.warning(
                f"auto-pick: picker claimed published={published} but page "
                f"meta says {real_dt.isoformat()} (diff {diff_h:.1f}h); "
                f"using the page date"
            )

    age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600.0
    # Half-hour grace for clock skew + small future-dated tolerance for
    # publishers that timestamp ahead (rare, but happens with embargoed wires).
    if age_hours < -1.0 or age_hours > max_age_hours + 0.5:
        log.warning(
            f"auto-pick: rejected — story is {age_hours:.1f}h old "
            f"(limit {max_age_hours}h), real_published={pub_dt.isoformat()}, "
            f"picker_claimed={published}, url={url}"
        )
        return "", "", "", cost, csource
    if age_hours > window_hours + 0.5:
        log.info(
            f"auto-pick: accepting story {age_hours:.1f}h old "
            f"(prompt asked for {window_hours}h, max accept {max_age_hours}h) "
            f"— picker had nothing fresher within allowlist; saved one retry"
        )

    return topic, url, pub_dt.isoformat(), cost, csource


# ---------------------------------------------------------------------------
# OpenRouter text generation
# ---------------------------------------------------------------------------
def openrouter_chat(
    prompt: str, *, max_tokens: int = 800, json_mode: bool = False, model: Optional[str] = None
) -> tuple[str, float, str]:
    """
    Call OpenRouter chat. Returns (text, cost_usd, source) where source is
    "actual" if OpenRouter returned `usage.cost` in the response (the standard
    behavior — this is the dollar amount they billed), or "estimate" if not
    present (rare). Callers feed this into piece.add_cost(..., source=source).

    `model` defaults to ORCHESTRATOR_MODEL but auto_pick_topic overrides it
    with PICKER_MODEL (web-search-enabled) so picker queries hit live news
    instead of the orchestrator model's training cutoff.
    """
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    body = {
        "model": model or ORCHESTRATOR_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        # Force OpenRouter to include accounting fields. usage.cost is the
        # dollar amount billed for THIS call — we record it as the actual.
        "usage": {"include": True},
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    text = payload["choices"][0]["message"]["content"]
    if not text:
        raise RuntimeError("OpenRouter returned empty content")
    usage = payload.get("usage") or {}
    cost: Optional[float] = None
    source = "estimate"
    raw_cost = usage.get("cost")
    if raw_cost is not None:
        try:
            cost = float(raw_cost)
            source = "actual"
        except (TypeError, ValueError):
            cost = None
    if cost is None:
        # Cheap fallback: gemini-2.5-flash list price ~ $0.075/1M input, $0.30/1M output.
        # Rough enough that the email shows SOMETHING, but flagged as estimate so
        # the user knows to reconcile (or upgrade their OpenRouter response parsing).
        prompt_tok = float(usage.get("prompt_tokens") or 0)
        comp_tok = float(usage.get("completion_tokens") or 0)
        cost = round((prompt_tok * 0.075 + comp_tok * 0.30) / 1_000_000.0, 6)
    log.debug(f"openrouter cost={cost} source={source} usage={usage}")
    return text, cost, source


def generate_article_text(topic: str, content_type: ContentType) -> tuple[str, str, float, str]:
    """Generate (title, body, cost_usd, cost_source). Body length tuned for 'read more' on every visual platform."""
    target_chars = {
        ContentType.ARTICLE: "250-450",
        ContentType.LONG_ARTICLE: "550-900",
        # Carousel body MUST be <450 chars total — visuals do the heavy lifting.
        ContentType.CAROUSEL: "300-440",
        ContentType.SHORT_VIDEO: "300-500",
        ContentType.LONG_VIDEO: "700-1200",
    }[content_type]
    prompt = (
        f"Write a sharp, data-driven post about: {topic}\n"
        f"{_niche_clause()}"
        f"Format:\n"
        f"- First line: a punchy 5-10 word title (no dates).\n"
        f"- Body: {target_chars} characters. The body must be long enough that Instagram, "
        f"LinkedIn, TikTok and Facebook all truncate it with a 'read more' affordance "
        f"(thresholds 125 / 210 / 80 / 480 chars respectively).\n"
        f"- Tone: Bloomberg Terminal condensed. Concrete numbers, sectors, take.\n"
        f"- No hashtags. No 'in conclusion'. No filler.\n"
    )
    raw, cost, source = openrouter_chat(prompt, max_tokens=800)
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    title = lines[0].lstrip("#").strip()
    body = "\n\n".join(lines[1:]) if len(lines) > 1 else raw
    return title, body, cost, source


def caption_for(piece: ContentPiece, platform: str) -> str:
    """Same body for every platform — truncated to the platform's char cap.

    User mandate: identical description on every platform; no per-platform LLM
    rewrites. Twitter's >480-char path is handled separately in publish() via
    split_thread(piece.body) (mechanical chunking, not a rewrite). If the body
    still exceeds a platform's hard cap, cut at the last word boundary and
    append an ellipsis so the truncation reads cleanly.
    """
    limit = LIMITS.get(platform, len(piece.body))
    body = piece.body
    if len(body) <= limit:
        return body
    cut = body.rfind(" ", 0, limit - 1)
    if cut < limit // 2:  # no reasonable word boundary — fall back to hard cut
        cut = limit - 1
    return body[:cut].rstrip(" ,;:.") + "…"


# ---------------------------------------------------------------------------
# Media generation
# ---------------------------------------------------------------------------
def _safe_topic(topic: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in topic)[:40] or "untitled"


ARTIFACT_ROOT = zeus_data_path("zeus_artifacts")


class _Phase:
    """
    Context manager that records wall-clock duration of a pipeline phase onto
    `piece.phase_durations_ms`. Multiple usages of the same phase name accumulate.
    Lets ledger_summary() report p50/p90 latency per phase + content type so
    optimization decisions are data-driven instead of guessed.
    """

    def __init__(self, piece: ContentPiece, name: str):
        self.piece = piece
        self.name = name

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc):
        ms = int((time.monotonic() - self._start) * 1000)
        d = self.piece.phase_durations_ms
        d[self.name] = d.get(self.name, 0) + ms
        return False


def _local_dir(piece: ContentPiece) -> pathlib.Path:
    """
    Return the stable on-disk artifact dir for `piece`. NEVER uses /tmp — bytes
    must survive process death and OS reaping so a crashed run can be recovered.
    `run()` sets piece.local_artifact_dir before any paid call; this function
    falls back to creating one lazily for direct callers.
    """
    if piece.local_artifact_dir:
        out = pathlib.Path(piece.local_artifact_dir)
        out.mkdir(parents=True, exist_ok=True)
        return out
    out = ARTIFACT_ROOT / f"{piece.run_id}_{_safe_topic(piece.topic)}"
    out.mkdir(parents=True, exist_ok=True)
    piece.local_artifact_dir = str(out)
    return out


def generate_media_for(
    piece: ContentPiece,
    slides: int = 4,
    video_seconds: int = 5,
    quality_override: Optional[str] = None,
) -> None:
    """Dispatch media generation by content type. Mutates `piece` in place.

    quality_override: if set ('low'|'medium'|'high'), replaces the per-type
    default quality from IMAGE_SPECS. 'low' is great for carousel iteration
    (~$0.005/slide vs $0.04), 'high' for marketing-grade ship quality (~$0.16).
    """
    out_dir = _local_dir(piece)
    if piece.content_type in (ContentType.ARTICLE, ContentType.LONG_ARTICLE):
        _gen_article_image(piece, out_dir, quality_override=quality_override)
    elif piece.content_type == ContentType.CAROUSEL:
        _gen_carousel_images(piece, out_dir, slides, quality_override=quality_override)
    elif piece.content_type == ContentType.SHORT_VIDEO:
        _gen_video(piece, out_dir, aspect="9:16", duration=min(video_seconds, 10))
    elif piece.content_type == ContentType.LONG_VIDEO:
        _gen_video(piece, out_dir, aspect="16:9", duration=min(video_seconds, 10))
    piece.status = "media_generated"


def _gen_article_image(piece: ContentPiece, out_dir: pathlib.Path, quality_override: Optional[str] = None) -> None:
    # User-supplied hero (from the ideas DB Files column) supersedes
    # generation — saves the $0.04-0.16 image call and uses the exact
    # photo the user wants on the post.
    if any(img.local_path for img in piece.images):
        log.info(f"  hero image: using user upload ({piece.images[0].local_path}) — skipping fal")
        return
    w, h, q = IMAGE_SPECS[ContentType.ARTICLE]
    if quality_override:
        q = quality_override
    # Symbolic, on-theme illustration — NEVER dump the article body.
    # gpt-image-2 has no internet access; if asked to render charts/dates/
    # numbers it FABRICATES them (user caught fake 2027 dates and made-up
    # data points 2026-05-07). The fix is prompt design: describe scene +
    # mood only, hard-forbid on-image text/numerics/dates/charts/logos.
    # Real data lives in the post body / caption, not on the image.
    prompt = (
        f'Editorial magazine-cover illustration for a finance and markets '
        f'article. THEME: {piece.topic}. MOOD: cinematic, premium '
        f'broadsheet-style photography or 3D render. '
        f'STRICT VISUAL RULES (must follow):\n'
        f'- NO on-image text of any kind (no headlines, captions, watermarks, logos)\n'
        f'- NO numbers, prices, percentages, tickers, or data labels\n'
        f'- NO dates, years, timestamps, or calendar imagery\n'
        f'- NO charts with axis labels or numeric tick marks\n'
        f'- NO real company logos, brand marks, or recognizable trademarks\n'
        f'Render the topic SYMBOLICALLY through composition, lighting, '
        f'and metaphor — abstract shapes, subjects, and environments only. '
        f'Hyper-real depth of field, dramatic editorial lighting, '
        f'high color contrast, single hero subject.'
    )
    url, cost = generate_image(prompt, width=w, height=h, quality=q, run_id=piece.run_id)
    local = download(url, str(out_dir / "image_1.png"))
    piece.images.append(
        GeneratedAsset(url=url, kind="image", width=w, height=h, model="gpt-image-2", cost_usd=cost, local_path=local)
    )
    # fal's standard response has no cost field for openai/gpt-image-2 — flagged
    # as estimate so scripts/fal_reconcile.py can reconcile against fal billing.
    piece.add_cost("gpt-image-2", cost, kind="image", source="estimate")
    ledger_checkpoint(piece, "article_image_generated")


def _gen_carousel_images(
    piece: ContentPiece, out_dir: pathlib.Path, slides: int, quality_override: Optional[str] = None,
) -> None:
    """
    Generate the N carousel slides in parallel. fal's queue handles concurrent
    jobs fine — sequentially this loop was the dominant carousel-pipeline cost
    (~60s × N). With ThreadPoolExecutor it collapses to ~max-of-N (one slowest
    slide). On exception in one slide, surviving slides still land on `piece`
    and a checkpoint row is written before re-raising — so artifact-first
    recovery still works.

    NOTE: gpt-image-2 occasionally renders text overlays despite the
    "no text overlays, no captions" instruction in the slide prompt. The model
    has no negative-prompt or "text=false" knob today; we accept this as a
    known limitation and rely on prompt phrasing.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    slides = max(3, min(5, slides))
    # User uploads (from the ideas DB Files column) take the first slots
    # — uploaded slide 1 is slide 1, etc. We only generate the remaining.
    # If the user supplies >= `slides` images, we generate nothing.
    pre_uploaded = [img for img in piece.images if img.local_path]
    if len(pre_uploaded) >= slides:
        log.info(
            f"  carousel: using {len(pre_uploaded)} user-uploaded slide(s); "
            f"skipping all fal generation"
        )
        # Cap to the requested slide count — extras are dropped.
        del piece.images[slides:]
        return
    if pre_uploaded:
        log.info(
            f"  carousel: using {len(pre_uploaded)} user-uploaded slide(s); "
            f"generating remaining {slides - len(pre_uploaded)} via fal"
        )
    slide_prompts = _carousel_slide_prompts(piece, slides)
    w, h, q = IMAGE_SPECS[ContentType.CAROUSEL]
    if quality_override:
        q = quality_override

    def _gen_one(idx: int, prompt: str) -> tuple[int, GeneratedAsset, float]:
        # One slide failure used to abort the whole carousel and skip publish.
        # Retry transient fal errors (timeouts, 5xx, post-lock flakes) up to 3
        # times with exponential backoff before giving up on this slide.
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                url, cost = generate_image(prompt, width=w, height=h, quality=q, run_id=piece.run_id)
                local = download(url, str(out_dir / f"slide_{idx + 1}.png"))
                asset = GeneratedAsset(
                    url=url, kind="image", width=w, height=h,
                    model="gpt-image-2", cost_usd=cost, local_path=local,
                )
                return idx, asset, cost
            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                log.warning(f"  slide {idx + 1} attempt {attempt + 1}/3 failed: {e}; retry in {wait}s")
                time.sleep(wait)
        assert last_err is not None
        raise last_err

    # Slot generated slides into a list keyed by slide index. Pre-uploaded
    # slots are already on piece.images (in order); only indices >= K need
    # generation. Order (slide 1 hook → slide N closer) is preserved by
    # appending in slide-index order.
    K = len(pre_uploaded)
    results: list[Optional[GeneratedAsset]] = [None] * slides
    first_error: Optional[BaseException] = None

    to_generate = [(i, sp) for i, sp in enumerate(slide_prompts) if i >= K]
    with ThreadPoolExecutor(max_workers=max(1, len(to_generate))) as pool:
        futures = {pool.submit(_gen_one, i, sp): i for i, sp in to_generate}
        for fut in as_completed(futures):
            try:
                idx, asset, cost = fut.result()
            except BaseException as e:
                if first_error is None:
                    first_error = e
                log.error(f"  carousel slide {futures[fut] + 1} failed: {e}")
                continue
            results[idx] = asset
            piece.add_cost("gpt-image-2", cost, kind="image", source="estimate")
            ledger_checkpoint(piece, f"carousel_slide_{idx + 1}_generated")

    # Append generated slides in order, skipping empty slots from failed
    # slides. User uploads (indices < K) are already on piece.images.
    for i in range(K, slides):
        if results[i] is not None:
            piece.images.append(results[i])

    # Cheap byte-level dedupe — fal occasionally serves the same cached image
    # for similar prompts, which would ship a carousel with 2 identical slides.
    # MD5 catches exact byte matches; perceptual dedupe would need a new dep
    # (imagehash + PIL) for marginal benefit. Soft warning, no abort — the
    # ledger row + Notion update still get the truth.
    seen_hashes: dict[str, int] = {}
    for i, asset in enumerate(piece.images):
        if not asset.local_path:
            continue
        try:
            with open(asset.local_path, "rb") as fh:
                h = hashlib.md5(fh.read()).hexdigest()
        except OSError:
            continue
        if h in seen_hashes:
            log.warning(
                f"  carousel slide {i + 1} is byte-identical to slide "
                f"{seen_hashes[h] + 1} — fal cache hit, visual is duplicated"
            )
        else:
            seen_hashes[h] = i

    if first_error is not None:
        raise first_error


_SLIDE_FALLBACK_LABELS = [
    "hook visual: bold, eye-catching opener that telegraphs the topic",
    "key data point: chart, table, or stat-heavy composition",
    "secondary insight: contextual scene reinforcing the article's claim",
    "supporting evidence: alternative visual angle or comparison",
    "closing CTA-style visual: simple, conclusive frame",
]


def _carousel_slide_prompts(piece: ContentPiece, slides: int) -> list[str]:
    # Tightened 2026-05-07: gpt-image-2 fabricates dates and data labels when
    # asked to depict charts/stats. We forbid all on-image text and any
    # specific numerics/dates — slides communicate via composition and
    # metaphor, body text carries the actual data.
    prompt = (
        f"You are designing a {slides}-slide social carousel based on this article. "
        f"{_niche_clause()}"
        f"Output ONLY a JSON object {{\"slides\": [\"prompt1\", \"prompt2\", ...]}} with "
        f"exactly {slides} image-generation prompts.\n\n"
        f"EVERY slide prompt MUST forbid on-image text, numbers, dates, prices, "
        f"percentages, tickers, real company logos, and chart axis labels — gpt-image-2 "
        f"has no internet and FABRICATES any data labels you ask it to draw. "
        f"Each slide is purely SYMBOLIC: vivid scenes, metaphors, composition, mood. "
        f"Slide 1 is a hook visual; later slides depict beats from the article "
        f"through metaphor, never through literal data viz.\n\n"
        f"ARTICLE TITLE: {piece.title}\n\nARTICLE BODY:\n{piece.body}"
    )
    raw, cost, source = openrouter_chat(prompt, max_tokens=900, json_mode=True)
    piece.add_cost(ORCHESTRATOR_MODEL, cost, kind="text", source=source)

    # Hard safety footer prepended to every prompt before it reaches
    # gpt-image-2 — defends against the orchestrator LLM dropping the
    # no-text/no-data constraints. gpt-image-2 fabricates any numbers or
    # dates we ask it to render (no internet access).
    safety = (
        " STRICT: render NO on-image text of any kind, NO numbers, NO "
        "prices, NO percentages, NO tickers, NO dates, NO years, NO "
        "real logos or trademarks, NO chart axis labels. Symbolic "
        "composition only. If the prompt above asks for any of these, "
        "render the scene WITHOUT them."
    )

    try:
        data = json.loads(raw)
        out = [str(s) for s in data.get("slides", [])]
        if len(out) >= slides:
            return [s + safety for s in out[:slides]]
    except json.JSONDecodeError:
        pass
    # JSON parse failed — vary fallback prompts deterministically so we never
    # ship a carousel of N identical slides. Each gets a distinct visual brief
    # plus the article body for context.
    log.warning("slide prompt JSON parse failed; using varied fallback prompts")
    body_excerpt = piece.body[:600]
    return [
        f"{_SLIDE_FALLBACK_LABELS[i % len(_SLIDE_FALLBACK_LABELS)]}. "
        f"Article context: {body_excerpt}{safety}"
        for i in range(slides)
    ]


def _gen_video(piece: ContentPiece, out_dir: pathlib.Path, aspect: str, duration: int) -> None:
    width, height = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    prompt = piece.body[:800]

    # User-supplied video (uploaded to the ideas DB Files column) wins
    # outright — no generation needed, just use the local file.
    if piece.video and piece.video.local_path:
        log.info(f"  video: using user upload ({piece.video.local_path}) — skipping Kling")
        # Only audio mixing still applies, fall through to it below.
    elif any(img.local_path for img in piece.images):
        # User-supplied keyframe → image-to-video Kling. Picks the first
        # uploaded image as the starting frame; fal needs an http URL so
        # we upload the local file to fal first.
        from lib import generate_video_kling_i2v, fal_upload_local_file
        keyframe = piece.images[0]
        log.info(f"  video: using user image as keyframe ({keyframe.local_path}) -> Kling i2v")
        try:
            image_url = fal_upload_local_file(keyframe.local_path)
        except Exception as e:
            log.warning(f"  fal upload failed ({e}); falling back to text-to-video")
            image_url = None
        if image_url:
            url, cost = generate_video_kling_i2v(
                prompt, image_url, aspect_ratio=aspect,
                duration_s=duration, run_id=piece.run_id,
            )
            local = download(url, str(out_dir / "video.mp4"))
            piece.video = GeneratedAsset(
                url=url, kind="video", width=width, height=height,
                duration_s=duration, model="kling-v2.5-turbo-pro-i2v",
                cost_usd=cost, local_path=local,
            )
            piece.add_cost("kling-v2.5-turbo-pro-i2v", cost, kind="video", source="estimate")
            ledger_checkpoint(piece, "video_generated_i2v")
        else:
            url, cost = generate_video_kling(prompt, aspect_ratio=aspect, duration_s=duration, run_id=piece.run_id)
            local = download(url, str(out_dir / "video.mp4"))
            piece.video = GeneratedAsset(
                url=url, kind="video", width=width, height=height,
                duration_s=duration, model="kling-v2.5-turbo-pro",
                cost_usd=cost, local_path=local,
            )
            piece.add_cost("kling-v2.5-turbo-pro", cost, kind="video", source="estimate")
            ledger_checkpoint(piece, "video_generated")
    else:
        url, cost = generate_video_kling(prompt, aspect_ratio=aspect, duration_s=duration, run_id=piece.run_id)
        local = download(url, str(out_dir / "video.mp4"))
        piece.video = GeneratedAsset(
            url=url, kind="video", width=width, height=height,
            duration_s=duration, model="kling-v2.5-turbo-pro",
            cost_usd=cost, local_path=local,
        )
        piece.add_cost("kling-v2.5-turbo-pro", cost, kind="video", source="estimate")
        ledger_checkpoint(piece, "video_generated")

    if piece.audio_mode:
        local_video = piece.video.local_path  # works for all branches above
        final_path, audio_costs = mix_audio_for_video(
            piece, local_video, str(out_dir), narration_text=piece.body,
        )
        if final_path != local_video:
            piece.video.local_path = final_path
        for model, model_cost in audio_costs.items():
            # fish.audio bills per character — char count IS the actual billing
            # primitive, so it's "actual". Music is fal-side, still "estimate".
            src = "actual" if model.startswith("fish-audio") else "estimate"
            piece.add_cost(model, model_cost, kind="audio", source=src)
        ledger_checkpoint(piece, "audio_mixed")


# ---------------------------------------------------------------------------
# Publer (optional --publish)
# ---------------------------------------------------------------------------
def _publer_headers(json_body: bool = True) -> dict:
    # User-Agent + Origin are required — Publer sits behind Cloudflare and
    # returns 1010 "browser_signature_banned" without them
    # (references/publer-api-reference.md).
    h = {
        "Authorization": PUBLER_AUTH,
        "Publer-Workspace-Id": PUBLER_WORKSPACE,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; ZeusPipeline/1.0)",
        "Origin": "https://app.publer.com",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _publer_upload(local_path: str, mime: str) -> str:
    with open(local_path, "rb") as fh:
        r = requests.post(
            f"{PUBLER_BASE}/media",
            headers=_publer_headers(json_body=False),
            files={"file": (os.path.basename(local_path), fh, mime)},
            timeout=120,
        )
    if r.status_code != 200:
        raise RuntimeError(f"Publer media upload failed {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def _publer_schedule(provider: str, account_id: str, post_type: str, text: str, media_ids: list[str]) -> str:
    # Publer interprets timezone-less ISO timestamps as UTC. Use UTC explicitly.
    when = (datetime.now(timezone.utc) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    payload = {
        "bulk": {
            "state": "scheduled",
            "posts": [
                {
                    "networks": {
                        provider: {
                            "type": post_type,
                            "text": text,
                            "media": [{"id": mid} for mid in media_ids],
                        }
                    },
                    "accounts": [{"id": account_id, "scheduled_at": when}],
                }
            ],
        }
    }
    r = requests.post(
        f"{PUBLER_BASE}/posts/schedule", headers=_publer_headers(), json=payload, timeout=20
    )
    if r.status_code != 200:
        raise RuntimeError(f"Publer schedule failed {r.status_code}: {r.text[:300]}")
    return r.json()["job_id"]


def _publer_schedule_thread(
    account_id: str, tweets: list[str], media_ids: list[str],
) -> str:
    """Post a Twitter text thread via Publer.

    Publer's thread shape: ONE post object with the head tweet in
    networks.twitter.text and the remaining tweets in accounts[0].comments[]
    as {text: "..."} entries (in order). A previous version sent N posts with
    a top-level `thread: True` flag, which Publer doesn't recognize — it
    treated each as an independent scheduled tweet, producing 5 separate posts
    instead of one chained thread (observed on long_article runs).
    """
    when = (datetime.now(timezone.utc) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    head_text = tweets[0]
    follow_ups = [{"text": t} for t in tweets[1:]]
    head_network: dict = {
        "type": "photo" if media_ids else "status",
        "text": head_text,
    }
    if media_ids:
        head_network["media"] = [{"id": media_ids[0]}]
    payload = {
        "bulk": {
            "state": "scheduled",
            "posts": [
                {
                    "networks": {"twitter": head_network},
                    "accounts": [
                        {
                            "id": account_id,
                            "scheduled_at": when,
                            "comments": follow_ups,
                        }
                    ],
                }
            ],
        }
    }
    r = requests.post(
        f"{PUBLER_BASE}/posts/schedule", headers=_publer_headers(), json=payload, timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Publer thread schedule failed {r.status_code}: {r.text[:300]}")
    return r.json()["job_id"]


def _publer_post_id_from_job(job_id: str) -> str | None:
    """Resolve a Publer schedule job_id to the actual post id.

    Far more reliable than the snippet-match fallback in `_publer_find_post_id`
    — same topic posted twice + concurrent runs make snippet matching collide.
    Publer's job_status response wraps the post objects under either `posts` or
    `payload.posts` depending on plan; both are checked.
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
        log.warning(f"_publer_post_id_from_job error: {e}")
    return None


def _publer_find_post_id(account_id: str, text_snippet: str) -> str | None:
    """Find the most recent Publer post matching account_id + text snippet. Returns post id or None."""
    def _norm(s: str) -> str:
        # Normalize whitespace + case so Publer's text mangling (smart quotes,
        # tracking-param re-encoding, leading emoji) doesn't break the match.
        return " ".join((s or "").lower().split())
    try:
        r = requests.get(f"{PUBLER_BASE}/posts?limit=30", headers=_publer_headers(), timeout=15)
        if r.status_code != 200:
            log.warning(f"Publer GET /posts returned {r.status_code}: {r.text[:200]}")
            return None
        snippet_norm = _norm(text_snippet)[:40]
        posts = r.json().get("posts", [])
        # Pass 1: substring match on normalized text (handles Publer's text edits)
        for post in posts:
            if post.get("account_id") != account_id:
                continue
            if snippet_norm and snippet_norm in _norm(post.get("text") or ""):
                return post.get("id")
        # Pass 2: most recent post on this account (Publer returns newest-first)
        for post in posts:
            if post.get("account_id") == account_id:
                log.info(f"  Publer match fallback: most-recent post on account (snippet match failed)")
                return post.get("id")
    except Exception as e:
        log.warning(f"_publer_find_post_id error: {e}")
        return None
    return None


def _publer_get_post(post_id: str) -> dict | None:
    try:
        r = requests.get(f"{PUBLER_BASE}/posts/{post_id}", headers=_publer_headers(), timeout=15)
        if r.status_code == 200:
            return r.json().get("post") or r.json()
        # some Publer instances return the post directly without wrapping
    except Exception:
        return None
    return None


def _extract_post_url(post: dict) -> str | None:
    """Try every Publer URL field we've seen across accounts/platforms."""
    for k in ("post_link", "url", "permalink", "public_url", "external_url", "social_url", "live_url"):
        v = post.get(k)
        if v and isinstance(v, str) and v.startswith("http"):
            return v
    # Some Publer responses nest under 'platform_data' or similar
    nested = post.get("platform_data") or post.get("response") or {}
    if isinstance(nested, dict):
        for k in ("url", "permalink", "post_link"):
            v = nested.get(k)
            if v and isinstance(v, str) and v.startswith("http"):
                return v
    return None


def _wait_for_posts_live(piece: ContentPiece, *, max_wait_s: int = 720, poll_interval_s: int = 15) -> None:
    """
    Poll Publer until each scheduled post goes live (state='posted' with a public URL).
    Mutates piece.publer_job_ids in place: adds '<platform>_url' for each live post.
    Default 12-min window covers Publer's 2-min schedule offset + platform lag.
    """
    pending: dict[str, str] = {}  # platform -> publer_post_id
    for platform in piece.target_platforms:
        if platform not in piece.publer_job_ids:
            continue
        job_id = str(piece.publer_job_ids[platform])
        if job_id.startswith("FAILED"):
            continue
        account = PUBLER_ACCOUNTS.get(platform)
        if not account:
            continue
        # Prefer direct job_id -> post_id resolution; falls back to snippet
        # match if Publer's job_status doesn't yet have the post (can race).
        post_id = _publer_post_id_from_job(job_id)
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
                snippet = caption_for(piece, platform) or piece.body
            post_id = _publer_find_post_id(account, snippet)
        if post_id:
            pending[platform] = post_id
            log.info(f"  tracking {platform} -> publer_post_id={post_id}")
        else:
            log.warning(f"  could not resolve Publer post_id for {platform}")

    if not pending:
        log.warning("no Publer post IDs resolved; skipping live-wait")
        return

    deadline = time.time() + max_wait_s
    log.info(f"  waiting up to {max_wait_s}s for {len(pending)} posts to go live...")
    first_poll = True
    while pending and time.time() < deadline:
        time.sleep(poll_interval_s)
        for platform in list(pending.keys()):
            post = _publer_get_post(pending[platform])
            if not post:
                continue
            if first_poll:
                # One-time diagnostic so we can see what Publer actually returns
                log.info(f"  Publer post payload sample [{platform}]: keys={sorted(post.keys())[:20]}, state={post.get('state')!r}")
            state = post.get("state")
            link = _extract_post_url(post)
            if state == "posted" and link:
                piece.publer_job_ids[f"{platform}_url"] = link
                log.info(f"  ✓ {platform} live: {link}")
                pending.pop(platform)
            elif state == "posted" and not link:
                # Posted but no URL field — log keys so we can add the right field name
                log.warning(f"  {platform} state=posted but no URL field found. Post keys: {sorted(post.keys())}")
            elif state in ("error", "failed"):
                err = post.get("error") or "unknown error"
                piece.publer_job_ids[f"{platform}_url"] = f"FAILED: {err}"
                log.error(f"  ✗ {platform} failed: {err}")
                pending.pop(platform)
        first_poll = False
    if pending:
        log.warning(f"  posts still pending at deadline: {list(pending.keys())}")
        for platform, post_id in pending.items():
            piece.publer_job_ids[f"{platform}_url"] = f"PENDING: post_id={post_id}"


def publish(piece: ContentPiece, *, wait_for_live: bool = False) -> None:
    """
    Push piece to Publer for every target platform that has a configured account id.

    Default mode (`wait_for_live=False`): non-blocking — schedule all posts in
    parallel, set status="scheduled", enqueue for the watcher
    (scripts/publish_watcher.py) to confirm permalinks asynchronously, return.
    Cuts ~5 min off every run because we no longer poll Publer for live URLs
    inside this process.

    Legacy mode (`wait_for_live=True`): keep the old behavior — schedule, then
    poll up to 6 min for live permalinks, set status from actual outcomes.
    Useful for manual debugging; default behavior is non-blocking.
    """
    if not PUBLER_KEY:
        raise RuntimeError("PUBLER_API_KEY not set; cannot --publish")

    # 1) Upload media once. Carousels = N parallel image uploads (1-3s each
    # sequential = 4-12s of dead time on a 4-slide run). ThreadPoolExecutor.map
    # preserves input order so slide ordering is not disturbed.
    from concurrent.futures import ThreadPoolExecutor

    media_ids: list[str] = []
    if piece.video and piece.video.local_path:
        media_ids = [_publer_upload(piece.video.local_path, "video/mp4")]
    else:
        images_with_path = [img for img in piece.images if img.local_path]
        if images_with_path:
            with ThreadPoolExecutor(max_workers=min(8, len(images_with_path))) as pool:
                media_ids = list(
                    pool.map(lambda img: _publer_upload(img.local_path, "image/png"), images_with_path)
                )

    # 2) Schedule per platform — in parallel. Each platform is an independent
    # Publer API call; sequential adds ~5-10s, parallel collapses to ~one round-trip.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _schedule_one(platform: str) -> tuple[str, str]:
        """Returns (platform, job_id_or_FAILED_marker). Never raises."""
        account = PUBLER_ACCOUNTS.get(platform)
        if not account:
            log.warning(f"no PUBLER_{platform.upper()}_ID configured -- skipping {platform}")
            return platform, ""
        # Twitter caps a single tweet at 4 media. For carousels with >4 slides
        # take the first 4 — the rest would be silently dropped by Twitter.
        if platform == "twitter" and len(media_ids) > 4:
            platform_media = media_ids[:4]
            log.info(f"  twitter: trimming {len(media_ids)} slides to 4 (Twitter cap)")
        else:
            platform_media = media_ids
        # Twitter only: bodies >480 chars become a text thread (mechanical
        # chunking of the same body, not a rewrite). Multi-image carousels
        # always ship as a single native gallery tweet — never thread.
        if (
            platform == "twitter"
            and needs_thread(piece.body)
            and len(platform_media) <= 1
        ):
            tweets = split_thread(piece.body)
            try:
                jid = _publer_schedule_thread(account, tweets, platform_media)
                log.info(f"  -> twitter thread ({len(tweets)} tweets, {len(platform_media)} media), job_id={jid}")
                return platform, jid
            except Exception as e:
                log.error(f"  !! twitter thread failed: {e}")
                return platform, f"FAILED: {e}"
        text = caption_for(piece, platform)
        if not text:
            log.warning(f"empty body for {platform} -- skipping")
            return platform, ""
        ptype = ("reel" if platform == "instagram" else "video") if piece.video else "photo"
        try:
            jid = _publer_schedule(platform, account, ptype, text, platform_media)
            log.info(f"  -> {platform} scheduled ({len(platform_media)} media), job_id={jid}")
            return platform, jid
        except Exception as e:
            log.error(f"  !! {platform} failed: {e}")
            return platform, f"FAILED: {e}"

    with ThreadPoolExecutor(max_workers=max(1, len(piece.target_platforms))) as pool:
        for fut in as_completed([pool.submit(_schedule_one, p) for p in piece.target_platforms]):
            platform, jid = fut.result()
            if jid:
                piece.publer_job_ids[platform] = jid

    piece.posted_at = datetime.now(timezone.utc)

    attempted = [p for p in piece.target_platforms if p in piece.publer_job_ids]
    if not attempted:
        piece.status = "failed"
        log.info(f"  publish outcome: status=failed (no platforms accepted)")
        return

    # Default: hand the run off to the watcher. We've already done the only
    # latency-bound work (schedule API calls); polling for permalinks is what
    # used to take 2-6 min and now happens out-of-process.
    if not wait_for_live:
        piece.status = "scheduled"
        # 24h queue lifetime: Publer often delays publishing by minutes-to-
        # hours due to feed-spacing rules + per-platform throttles. The
        # watcher's _final_status now only finalises when nothing's pending,
        # so this is a hard cutoff, not a "give up early" knob.
        publish_enqueue(piece, max_wait_s=86400)
        log.info(
            f"  publish outcome: status=scheduled, {len(attempted)} platforms enqueued for watcher "
            f"(run scripts/publish_watcher.py to resolve permalinks)"
        )
        return

    # Legacy / debugging path: keep the run alive until permalinks resolve.
    _wait_for_posts_live(piece, max_wait_s=360, poll_interval_s=15)
    confirmed: list[str] = []
    failed: list[str] = []
    for platform in attempted:
        if str(piece.publer_job_ids.get(platform, "")).startswith("FAILED"):
            failed.append(platform)
            continue
        url = str(piece.publer_job_ids.get(f"{platform}_url", ""))
        if url and not url.startswith("FAILED") and not url.startswith("pending"):
            confirmed.append(platform)
        else:
            failed.append(platform)
    if confirmed and not failed:
        piece.status = "posted"
    elif confirmed:
        piece.status = "partial"
    else:
        piece.status = "failed"
    log.info(
        f"  publish outcome: status={piece.status} "
        f"confirmed={confirmed or '[]'} failed={failed or '[]'}"
    )




# ---------------------------------------------------------------------------
# Orchestrator entry point
# ---------------------------------------------------------------------------
def run(
    content_type: ContentType,
    topic: str,
    *,
    slides: int,
    duration: int,
    do_publish: bool,
    audio_mode: AudioMode | None = None,
    wait_for_live: bool = False,
    quality: Optional[str] = None,
    picker_cost: Optional[tuple[float, str]] = None,
) -> ContentPiece:
    log.info("=" * 60)
    log.info(f"  Zeus pipeline -- {content_type.value}: {topic}")
    if audio_mode:
        log.info(f"  audio mode: {audio_mode.value}")
    log.info("=" * 60)

    # Build a stub piece up front so we can time text-gen onto it.
    piece = ContentPiece(content_type=content_type, title="", body="", topic=topic, audio_mode=audio_mode)

    # Picker spend (perplexity/sonar-pro web search) happens before the piece
    # exists, so main() captures it and hands it in here. Without this, every
    # --auto run silently underreports its real spend by the picker's cost.
    if picker_cost is not None:
        pcost, psource = picker_cost
        piece.add_cost(PICKER_MODEL, pcost, kind="text", source=psource)

    with _Phase(piece, "text_gen"):
        title, body, text_cost, text_source = generate_article_text(topic, content_type)
    piece.title = title
    piece.body = body
    piece.add_cost(ORCHESTRATOR_MODEL, text_cost, kind="text", source=text_source)
    log.info(
        f"  text -> title='{title}' body={len(body)}c run_id={piece.run_id} "
        f"cost=${text_cost:.6f} ({text_source}) "
        f"took={piece.phase_durations_ms.get('text_gen', 0)}ms"
    )

    # Stable artifact dir — set BEFORE any paid call so a crash leaves recoverable
    # bytes on disk (NOT /tmp). orphan_sweep.py keys off this path.
    artifact_dir = ARTIFACT_ROOT / f"{piece.run_id}_{_safe_topic(piece.topic)}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    piece.local_artifact_dir = str(artifact_dir)
    log.info(f"  artifacts -> {piece.local_artifact_dir}")

    archive = NotionArchive()

    # Archive EARLY (text + run_id + artifact dir). If the pipeline later crashes
    # mid-media, the Notion row already exists pointing at the artifact dir, and
    # update_assets() below will patch in whatever bytes we did capture.
    with _Phase(piece, "notion_archive_early"):
        try:
            archive.archive(piece)
            log.info(f"  archived (pre-media) -> {piece.notion_page_id}")
        except Exception as e:
            log.error(f"  early Notion archive failed (proceeding so spend lands in ledger): {e}")

    media_error: Exception | None = None
    with _Phase(piece, "media_gen"):
        try:
            generate_media_for(piece, slides=slides, video_seconds=duration, quality_override=quality)
            log.info(
                f"  media -> images={len(piece.images)} video={'yes' if piece.video else 'no'} cost=${piece.total_cost:.3f} "
                f"took={piece.phase_durations_ms.get('media_gen', 0)}ms"
            )
            errors = piece.validate()
            if errors:
                # Carousels with the wrong slide count post as broken UX (single
                # image / album-of-2). Hard-fail so publish is skipped — the run
                # still archives, ledgers, and emails via the partial-recovery
                # path so spend isn't lost.
                if piece.content_type == ContentType.CAROUSEL:
                    log.error(f"  carousel validation failed: {errors}")
                    raise RuntimeError(f"carousel validation: {errors}")
                log.warning(f"validation issues: {errors}")
        except Exception as e:
            media_error = e
            piece.status = "media_partial" if (piece.images or piece.video) else "failed"
            log.error(f"  media generation crashed (status={piece.status}): {e}")

    # Patch Notion with whatever assets we captured — success OR partial. If the
    # early archive failed, retry it now so the row at least exists.
    with _Phase(piece, "notion_assets"):
        try:
            if not piece.notion_page_id:
                archive.archive(piece)
                log.info(f"  archived (recovery) -> {piece.notion_page_id}")
            archive.update_assets(piece)
            log.info(f"  notion assets patched ({len(piece.images)} images, video={'yes' if piece.video else 'no'})")
        except Exception as e:
            log.error(f"  Notion update_assets failed: {e}")

    publish_error: Exception | None = None
    if do_publish and media_error is None:
        with _Phase(piece, "publish"):
            try:
                publish(piece, wait_for_live=wait_for_live)
                archive.update_status(piece)
                # Per-publish row in the Content Pipeline DB — one row per run
                # with multi-select Platforms, Run ID, and (eventually) live
                # Post URLs. Skipped silently if the user hasn't set up a
                # 'Content Pipeline' DB or NOTION_PIPELINE_DB_ID env.
                try:
                    archive.write_pipeline_row(piece)
                except Exception as e:
                    log.warning(f"  pipeline-row write failed (non-fatal): {e}")
                log.info(f"  published -> jobs={piece.publer_job_ids} took={piece.phase_durations_ms.get('publish', 0)}ms")
            except Exception as e:
                publish_error = e
                log.error(f"  publish failed: {e}")
    elif do_publish:
        log.warning("  skipping publish — media did not complete cleanly")
    else:
        log.info("  skip publish (use --publish to post)")

    # Always finalize: ledger row regardless. Email is conditional —
    # `scheduled` runs defer to publish_watcher so the email arrives WITH real
    # post URLs, not job-id placeholders. Failures still email immediately so
    # we don't silently lose runs that need attention.
    try:
        ledger_append(piece)
    except Exception as e:
        log.error(f"  ledger_append failed: {e}")
    defer_email = (
        do_publish
        and piece.status == "scheduled"
        and publish_error is None
        and media_error is None
    )
    if defer_email:
        log.info(
            "  email deferred to publish_watcher — will land when post URLs "
            "resolve (cron: zeus-content-publish-watcher every 10 min)"
        )
    else:
        try:
            backend = send_pipeline_summary(piece)
            log.info(f"  notified -> backend={backend}")
        except Exception as e:
            log.error(f"  email failed: {e}")

    log.info(
        f"DONE — total cost ${piece.total_cost:.4f}, models {piece.models_used}, status={piece.status}"
    )

    if media_error is not None:
        raise media_error
    if publish_error is not None:
        raise publish_error
    return piece


def _emit_picker_failure(content_type: ContentType, error: Exception) -> None:
    """Best-effort: write a failed-run ledger row + email when auto_pick
    burns money but finds no qualifying story. Keeps the failure visible
    instead of dying silently in a cron error file (added 2026-05-07).
    """
    msg = str(error)
    cost = 0.0
    # auto_pick_topic includes "(cost spent: $X)" in its RuntimeError; pull
    # it out so the ledger row reflects what the picker actually billed us.
    import re
    m = re.search(r"cost spent:\s*\$([0-9.]+)", msg)
    if m:
        try:
            cost = float(m.group(1))
        except ValueError:
            pass

    piece = ContentPiece(
        content_type=content_type,
        title="(picker rejected — no current story)",
        body=msg[:1500],
        topic="auto-pick failure",
    )
    piece.status = "failed"
    if cost > 0:
        piece.add_cost(PICKER_MODEL, cost, kind="text", source="actual")

    try:
        ledger_append(piece)
    except Exception as e:
        log.warning(f"  ledger_append failed (picker-failure path): {e}")
    try:
        backend = send_pipeline_summary(piece)
        log.info(f"  picker-failure email sent -> backend={backend}")
    except Exception as e:
        log.warning(f"  picker-failure email failed: {e}")


def main() -> int:
    p = argparse.ArgumentParser(description="Zeus content pipeline test runner")
    p.add_argument("--type", required=True, choices=[t.value for t in ContentType])
    p.add_argument("--topic", required=False, default=None, help="Topic/headline for the content (required unless --auto)")
    p.add_argument(
        "--auto",
        action="store_true",
        help="Pick a topic from content_pipeline.niche via a cheap LLM call. "
             "Use for host-side cron / scheduled runs. Mutually exclusive with --topic.",
    )
    p.add_argument("--slides", type=int, default=4, help="Slides for carousels (3-5)")
    p.add_argument("--duration", type=int, default=5, help="Seconds for video (5-10 per call)")
    p.add_argument(
        "--quality",
        choices=["low", "medium", "high"],
        default=None,
        help="GPT Image 2 quality. Defaults from IMAGE_SPECS per type. "
             "low ~$0.005/img (carousel iteration), medium ~$0.04, high ~$0.16 (ship-grade).",
    )
    p.add_argument(
        "--audio-mode",
        choices=[m.value for m in AudioMode],
        default=None,
        help="Audio mode for videos: music_only, music_narration, narration_primary",
    )
    p.add_argument("--publish", action="store_true", help="Also post to Publer (default: archive only)")
    p.add_argument(
        "--wait-for-live", action="store_true",
        help="Block until Publer confirms permalinks (~6 min). Default is non-blocking — "
             "scripts/publish_watcher.py resolves them out-of-process.",
    )
    args = p.parse_args()

    if args.auto and args.topic:
        log.error("--auto and --topic are mutually exclusive")
        return 2
    if not args.auto and not args.topic:
        log.error("provide --topic, or pass --auto to pick from your niche")
        return 2

    required = ["OPENROUTER_API_KEY", "FAL_KEY", "NOTION_API_KEY"]
    if args.publish:
        required.append("PUBLER_API_KEY")
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing env: {', '.join(missing)}")
        return 2

    audio_mode = AudioMode(args.audio_mode) if args.audio_mode else None
    if audio_mode and args.type not in ("short_video", "long_video"):
        log.warning(f"--audio-mode only applies to video types, ignoring for {args.type}")
        audio_mode = None

    if args.type in ("short_video_avatar", "long_video_avatar"):
        log.error(
            f"--type {args.type} is scaffolded in the taxonomy but the generation "
            f"pipeline is not yet wired (avatar provider + presenter persona TBD). "
            f"Pick one of: article, long_article, carousel, short_video, long_video."
        )
        return 3

    try:
        if args.topic:
            topic = args.topic
            picker_cost = None
        else:
            try:
                topic, _pcost, _psource = auto_pick_topic(ContentType(args.type))
            except Exception as picker_error:
                # Picker failed (no qualifying story / API error). Emit a
                # failed-run ledger row + email so the user sees it instead
                # of finding a silent cron error file hours later.
                _emit_picker_failure(ContentType(args.type), picker_error)
                raise
            picker_cost = (_pcost, _psource)
        run(
            ContentType(args.type),
            topic,
            slides=args.slides,
            duration=args.duration,
            do_publish=args.publish,
            audio_mode=audio_mode,
            wait_for_live=args.wait_for_live,
            quality=args.quality,
            picker_cost=picker_cost,
        )
        return 0
    except Exception as e:  # surface clean failure rather than dumping a traceback into the user's terminal
        log.exception(f"pipeline failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
