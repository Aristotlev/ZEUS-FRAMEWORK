"""Senate Banking Committee source — banking.senate.gov/hearings.

Audited 2026-05-15 as fully Akamai-walled (403 to plain HTTP regardless
of header tweaks). With the deploy/browser-fetch sidecar, the listing
page renders in a real Chromium and the per-hearing pages reveal their
HLS stream URLs via the player's network requests.

Pipeline:
  1. Render https://www.banking.senate.gov/hearings via sidecar.
  2. Pull links to /hearings/<slug> from the listing.
  3. For each hearing page, render with capture_responses_regex set to
     match the m3u8 manifest the player loads. Take the first match.
  4. Pull date from the hearing page's `<time>` tag, title from `<h1>`.

Media downloads happen via ffmpeg from the agent container's IP. Akamai
sometimes 403s those (the audit's original failure mode). If that becomes
the steady-state outcome in prod we route HLS segments through the
sidecar's /fetch-binary endpoint with the hearing page primed as referer.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import _browser
from .base import (
    UploadCandidate,
    VideoSource,
    register,
)

log = logging.getLogger("zeus.event_clip.sources.senate_banking")

HOME_URL = "https://www.banking.senate.gov"
LISTING_URL = f"{HOME_URL}/hearings"

# Hearing slug links: /hearings/<slug-with-or-without-date-prefix>. Avoid
# the year-archive view (/hearings/year/2026) and pagination links.
_HEARING_LINK_RE = re.compile(
    r'href="(/hearings/[a-z0-9][a-z0-9\-]{6,})"', re.I,
)

# Akamai media URLs the senate streamer uses. Both common shapes:
#   https://...akamaihd.net/.../manifest.m3u8
#   https://...akamaized.net/.../master.m3u8
_M3U8_NETWORK_RE = r"(akamaihd|akamaized|edgesuite)\.net.*\.m3u8"

_TITLE_RE = re.compile(
    r'<h1[^>]*>(.*?)</h1>', re.I | re.DOTALL,
)
# <time datetime="2026-05-14T10:00:00-04:00"> pattern is consistent on
# committee.senate.gov pages.
_DATETIME_ATTR_RE = re.compile(
    r'<time[^>]+datetime="([^"]+)"', re.I,
)


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class SenateBankingSource(VideoSource):
    source_id = "senate_banking"
    display_name = "U.S. Senate Committee on Banking, Housing, and Urban Affairs"
    home_url = LISTING_URL

    def list_recent(self, *, hours_back: int) -> list[UploadCandidate]:
        if not _browser.is_available():
            log.info("senate_banking: sidecar unavailable — returning []")
            return []

        listing = _browser.fetch_page(LISTING_URL, wait_seconds=2.0)
        if listing is None or listing.status >= 400 or not listing.html:
            log.info("senate_banking: listing fetch failed")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        seen: set[str] = set()
        hearing_paths: list[str] = []
        for m in _HEARING_LINK_RE.finditer(listing.html):
            path = m.group(1)
            # Skip archive/year/index pages.
            if re.search(r"/hearings/(year|month|index|archive)\b", path, re.I):
                continue
            if path in seen:
                continue
            seen.add(path)
            hearing_paths.append(path)
            if len(hearing_paths) >= 8:
                break

        results: list[UploadCandidate] = []
        for path in hearing_paths:
            page_url = f"{HOME_URL}{path}"
            fr = _browser.fetch_page(
                page_url,
                wait_seconds=3.5,  # let the player init + request its manifest
                capture_responses_regex=_M3U8_NETWORK_RE,
                referer=LISTING_URL,
            )
            if fr is None or fr.status >= 400:
                log.info("senate_banking: hearing %s fetch failed", path)
                continue

            uploaded_at: Optional[datetime] = None
            m = _DATETIME_ATTR_RE.search(fr.html)
            if m:
                uploaded_at = _parse_iso(m.group(1))
            if not uploaded_at or uploaded_at < cutoff:
                continue

            m3u8_url = fr.captured_urls[0] if fr.captured_urls else ""
            if not m3u8_url:
                # Some hearings post the page before the stream is up. Skip
                # silently — we'll catch it on the next cron tick.
                log.info("senate_banking: %s — no m3u8 captured yet", path)
                continue

            title = ""
            tm = _TITLE_RE.search(fr.html)
            if tm:
                title = _strip_tags(tm.group(1))
            if not title:
                # Fall back to slug as a last resort.
                title = path.rsplit("/", 1)[-1].replace("-", " ").title()

            results.append(UploadCandidate(
                source_id=self.source_id,
                video_id=path.rsplit("/", 1)[-1],
                title=title[:200],
                page_url=page_url,
                upload_date=uploaded_at.isoformat(),
                duration_s=0,  # probed at download time
                media_url=m3u8_url,
                media_kind="hls",
                # Akamai keys on Referer for the signed manifest and gates
                # the agent container IP entirely. Direct try first with
                # the hearing page as Referer; on 403, retry through the
                # sidecar with the hearing page primed.
                referer=page_url,
                use_browser_fallback=True,
            ))
            if len(results) >= 4:
                break
        return results


SENATE_BANKING_SOURCE = register(SenateBankingSource())
