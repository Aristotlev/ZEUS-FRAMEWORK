"""C-SPAN.org source — homepage + program-page scraping via browser sidecar.

C-SPAN's video catalog spans congressional committee hearings, news
conferences, white house events, and committee-floor sessions — exactly
the content we lost when we dropped YouTube ingestion.

Why we can't use plain HTTP here:
  - c-span.org gates a fraction of cold-IP requests through a Cloudflare
    202 challenge interstitial that requests/curl can't solve.
  - Program pages are SPA-rendered; the JSON-LD <script> tags only
    populate after JS executes, so a static HTML pull returns the
    template placeholders, not the real VideoObject.

The deploy/browser-fetch sidecar (headless Chromium on zeus-net) renders
each page, then returns the post-hydration HTML. We then do the same
JSON-LD parse we did before.

Media URL: c-span's HLS manifest follows a deterministic pattern at
m3u8-0.c-spanvideo.org/program/program.<ID>.tsc.m3u8. CloudFront *may*
require a signed token; ffmpeg's plain GET will surface 403 if so, which
the watcher records as a `skipped:download_failed` ledger row. If that
becomes the steady-state outcome in prod, we route media through the
sidecar's /fetch-binary endpoint with the program page primed as referer.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import _browser
from .base import (
    SourceListingError,
    UploadCandidate,
    VideoSource,
    register,
)

log = logging.getLogger("zeus.event_clip.sources.cspan")

HOME_URL = "https://www.c-span.org/"

# Categories that produce newsworthy soundbites. Everything else (campaign
# rallies, book talks, history vignettes, washington-journal call-in
# segments) is excluded.
_CATEGORY_WHITELIST: set[str] = {
    "house-committee",
    "senate-committee",
    "joint-committee",
    "news-conference",
    "white-house-event",
    "public-affairs-event",
    "united-nations",
    "us-house-of-representatives",
    "us-senate",
}

_PROGRAM_LINK_RE = re.compile(
    r'/program/([a-z0-9\-]+)/[a-z0-9\-]+/(\d{5,8})\b', re.I,
)
_JSONLD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.I,
)


def _parse_iso8601(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_iso_duration(s: str) -> int:
    """Parse 'PT1H23M45S' to seconds. Returns 0 if unparseable."""
    if not s:
        return 0
    m = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", s.strip(), re.I,
    )
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mn = int(m.group(2) or 0)
    sec = float(m.group(3) or 0)
    return int(h * 3600 + mn * 60 + sec)


def _extract_video_meta(html: str) -> Optional[dict]:
    """Find the VideoObject JSON-LD block on a rendered program page."""
    for jm in _JSONLD_RE.finditer(html):
        blob = jm.group(1).strip()
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        candidates = [data] if isinstance(data, dict) else []
        graph = (data.get("@graph") if isinstance(data, dict) else None) or []
        candidates.extend(graph)
        video = next(
            (c for c in candidates if isinstance(c, dict)
             and c.get("@type") == "VideoObject"),
            None,
        )
        if not video:
            continue
        return {
            "title": (video.get("name") or "").strip(),
            "uploaded_at": _parse_iso8601(video.get("uploadDate") or ""),
            "duration_s": _parse_iso_duration(video.get("duration") or ""),
        }
    return None


class CSpanSource(VideoSource):
    source_id = "cspan"
    display_name = "C-SPAN"
    home_url = HOME_URL

    def list_recent(self, *, hours_back: int) -> list[UploadCandidate]:
        if not _browser.is_available():
            log.info("cspan: browser-fetch sidecar unavailable — returning []")
            return []

        home = _browser.fetch_page(HOME_URL, wait_seconds=2.0)
        if home is None or home.status >= 400 or not home.html:
            log.info("cspan: homepage fetch via sidecar failed")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        seen: set[str] = set()
        candidate_links: list[tuple[str, str]] = []  # (program_id, page_url)
        for m in _PROGRAM_LINK_RE.finditer(home.html):
            category = m.group(1).lower()
            program_id = m.group(2)
            if category not in _CATEGORY_WHITELIST:
                continue
            if program_id in seen:
                continue
            seen.add(program_id)

            link_match = re.search(
                rf'/program/{re.escape(category)}/[a-z0-9\-]+/{program_id}\b',
                home.html, re.I,
            )
            if not link_match:
                continue
            page_url = f"https://www.c-span.org{link_match.group(0)}"
            candidate_links.append((program_id, page_url))
            # Cap to avoid hammering the sidecar — most homepage scrapes
            # surface 20-30 program links; we only need the freshest few.
            if len(candidate_links) >= 12:
                break

        results: list[UploadCandidate] = []
        for program_id, page_url in candidate_links:
            try:
                meta = self._fetch_program_meta(page_url)
            except Exception as exc:
                log.warning(
                    "cspan program %s meta fetch failed: %s", program_id, exc,
                )
                continue
            if not meta:
                continue

            uploaded_at = meta.get("uploaded_at")
            if not uploaded_at or uploaded_at < cutoff:
                continue

            results.append(UploadCandidate(
                source_id=self.source_id,
                video_id=program_id,
                title=meta["title"],
                page_url=page_url,
                upload_date=uploaded_at.isoformat(),
                duration_s=meta["duration_s"],
                media_url=(
                    f"https://m3u8-0.c-spanvideo.org/program/"
                    f"program.{program_id}.tsc.m3u8"
                ),
                media_kind="hls",
                # CloudFront often 403s the Hetzner IP on the bare m3u8.
                # Send the program page as Referer on the direct try, and
                # retry through the sidecar (with the program page primed)
                # if that still fails.
                referer=page_url,
                use_browser_fallback=True,
            ))
            if len(results) >= 5:
                break
        return results

    def _fetch_program_meta(self, page_url: str) -> Optional[dict]:
        fr = _browser.fetch_page(
            page_url,
            # Wait for the player init to inject the VideoObject JSON-LD.
            wait_for_selector='script[type="application/ld+json"]',
            wait_seconds=1.0,
        )
        if fr is None:
            return None
        if fr.status >= 400 or not fr.html:
            raise SourceListingError(f"program page {fr.status}")
        meta = _extract_video_meta(fr.html)
        if not meta:
            raise SourceListingError("no VideoObject JSON-LD on page")
        return meta


CSPAN_SOURCE = register(CSpanSource())
