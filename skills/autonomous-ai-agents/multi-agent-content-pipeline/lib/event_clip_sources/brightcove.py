"""Generic Brightcove Playback API source. Used by Fed (federalreserve.gov)
and IMF (imf.org) — both host on Brightcove with public policy keys.

Pattern:
  1. Scrape publisher's HTML for `data-video-id="..." data-account="..."` or
     the equivalent video-list URL pattern.
  2. For each ID, hit edge.api.brightcove.com Playback API with
     Accept: application/json;pk={policy_key}.
  3. Extract title/published_at/duration/sources[] (MP4 + HLS).
  4. Prefer progressive MP4 when available (Fed does, IMF doesn't); fall
     back to HLS master manifest.

The policy key is the public default-player key from the Brightcove player
JS bundle — it rotates very rarely. If it 401s in the wild, re-extract from
players.brightcove.net/{account}/default_default/index.min.js.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .base import (
    SourceListingError,
    UploadCandidate,
    VideoSource,
    http_get,
    register,
)

log = logging.getLogger("zeus.event_clip.sources.brightcove")


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Brightcove emits "2026-04-29T18:31:45.000Z"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class BrightcoveSource(VideoSource):
    def __init__(
        self,
        *,
        source_id: str,
        display_name: str,
        home_url: str,
        account_id: str,
        policy_key: str,
        list_video_ids: Callable[[int], list[tuple[str, str]]],
        listing_needs_proxy: bool = False,
    ):
        """
        Args:
            list_video_ids: callable(hours_back) -> [(video_id, page_url), ...]
                Per-publisher HTML scraper that surfaces candidate Brightcove
                IDs from listing pages. Doesn't need to filter by date — the
                Playback API gives us the real published_at and we filter here.
            listing_needs_proxy: True for publishers whose listing HTML 403s
                the Hetzner IP (IMF). The Playback API itself never needs
                a proxy — it's CDN-cached and accepts anywhere.
        """
        self.source_id = source_id
        self.display_name = display_name
        self.home_url = home_url
        self.account_id = account_id
        self.policy_key = policy_key
        self.list_video_ids = list_video_ids
        self.listing_needs_proxy = listing_needs_proxy

    def list_recent(self, *, hours_back: int) -> list[UploadCandidate]:
        try:
            id_pairs = self.list_video_ids(hours_back)
        except Exception as exc:  # broad: scraper can fail in many shapes
            log.warning("%s listing scraper failed: %s", self.source_id, exc)
            return []
        if not id_pairs:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        results: list[UploadCandidate] = []
        # Cap to 12 ids — most listings surface dozens. Playback API is a
        # round-trip each, so we bound the work.
        for video_id, page_url in id_pairs[:12]:
            try:
                meta = self._playback_meta(video_id)
            except Exception as exc:
                log.warning(
                    "%s Playback API failed for %s: %s",
                    self.source_id, video_id, exc,
                )
                continue
            uploaded_at = meta["uploaded_at"]
            if not uploaded_at or uploaded_at < cutoff:
                continue
            results.append(UploadCandidate(
                source_id=self.source_id,
                video_id=video_id,
                title=meta["title"],
                page_url=page_url,
                upload_date=uploaded_at.isoformat(),
                duration_s=meta["duration_s"],
                media_url=meta["media_url"],
                media_kind=meta["media_kind"],
            ))
        return results

    def _playback_meta(self, video_id: str) -> dict:
        url = (
            f"https://edge.api.brightcove.com/playback/v1/accounts/"
            f"{self.account_id}/videos/{video_id}"
        )
        headers = {"Accept": f"application/json;pk={self.policy_key}"}
        r = http_get(url, headers=headers, timeout=15)
        if r.status_code == 401:
            raise SourceListingError(
                f"Brightcove 401 — policy key for {self.source_id} may have "
                f"rotated; re-extract from players.brightcove.net/"
                f"{self.account_id}/default_default/index.min.js"
            )
        r.raise_for_status()
        data = r.json()

        mp4_best: tuple[str, int] = ("", 0)  # (url, size_bytes)
        hls_url: str = ""
        for src in data.get("sources", []) or []:
            container = (src.get("container") or "").upper()
            stype = (src.get("type") or "").lower()
            href = src.get("src") or ""
            if not href.startswith("https://"):
                continue
            if container == "MP4":
                size = int(src.get("size") or 0)
                if size > mp4_best[1]:
                    mp4_best = (href, size)
            elif "mpegurl" in stype or href.endswith(".m3u8"):
                # First HTTPS HLS we see is fine — Brightcove returns
                # multivariant masters, ffmpeg picks the rendition.
                if not hls_url:
                    hls_url = href

        if mp4_best[0]:
            media_url, media_kind = mp4_best[0], "mp4"
        elif hls_url:
            media_url, media_kind = hls_url, "hls"
        else:
            raise SourceListingError(
                f"no usable sources[] entry in Brightcove metadata for {video_id}"
            )

        return {
            "title": (data.get("name") or "").strip(),
            "uploaded_at": _parse_iso(data.get("published_at") or ""),
            "duration_s": int((data.get("duration") or 0) // 1000),
            "media_url": media_url,
            "media_kind": media_kind,
        }


# ---------------------------------------------------------------------------
# Fed listing scraper — federalreserve.gov
# ---------------------------------------------------------------------------
# Pattern: every video page on federalreserve.gov has a <video> tag with
# data-video-id="<numeric brightcove id>" data-account="66043936001".
# /videos.htm is the consolidated video index. Press conf pages also link
# from /newsevents/pressconferences.htm.
_FED_VIDEO_TAG_RE = re.compile(
    r'data-video-id="(\d+)"\s+data-account="66043936001"', re.I,
)
# Listing-page link to individual video pages: relative paths starting with
# /monetarypolicy/, /newsevents/speech/, /newsevents/pressconf-, etc.
_FED_LINK_RE = re.compile(
    r'href="(/(?:monetarypolicy|newsevents|videos)/[^"#?]+\.htm)"', re.I,
)


def _fed_list_video_ids(hours_back: int) -> list[tuple[str, str]]:
    """Scrape federalreserve.gov for recent Brightcove video IDs.

    Strategy:
      1. Fetch /videos.htm (the consolidated index).
      2. Pull every relative .htm link.
      3. For the most recent ~10 of those, fetch each page and extract its
         data-video-id. Yield (video_id, page_url) pairs.

    We over-fetch a bit and let BrightcoveSource.list_recent's date filter
    do the lookback enforcement (the Playback API gives accurate published_at).
    """
    del hours_back  # date filtering happens in BrightcoveSource via API
    home = "https://www.federalreserve.gov"
    try:
        index_html = http_get(f"{home}/videos.htm", timeout=20).text
    except Exception as exc:
        log.warning("fed videos.htm fetch failed: %s", exc)
        return []

    seen: set[str] = set()
    page_urls: list[str] = []
    for m in _FED_LINK_RE.finditer(index_html):
        path = m.group(1)
        if path in seen:
            continue
        seen.add(path)
        page_urls.append(f"{home}{path}")
        if len(page_urls) >= 15:
            break

    out: list[tuple[str, str]] = []
    for page_url in page_urls:
        try:
            html = http_get(page_url, timeout=20).text
        except Exception:
            continue
        m = _FED_VIDEO_TAG_RE.search(html)
        if not m:
            continue  # speech text-only pages with no video — fine
        out.append((m.group(1), page_url))
    return out


FED_SOURCE = register(BrightcoveSource(
    source_id="federalreserve",
    display_name="Federal Reserve",
    home_url="https://www.federalreserve.gov/videos.htm",
    account_id="66043936001",
    policy_key=(
        "BCpkADawqM0J7PEJrUnIrcu81WeBp_NXD6LD3ARLY1mZ_zQnSh5VCgsH5jYbY5IO"
        "hf3ssgVDk8jLwURDX1jP_lRtkBs3i9p7W__JzAQ-imDGPS6iLIZ4e5ZY1QA"
    ),
    list_video_ids=_fed_list_video_ids,
))


# ---------------------------------------------------------------------------
# IMF listing scraper — imf.org
# ---------------------------------------------------------------------------
# IMF's video page URL pattern: /en/videos/view/{13-digit Brightcove id}.
# Listing surfaces on /en/News/SearchNews?f:type=[Videos] but that page
# 403s the Hetzner datacenter IP — must go through ZEUS_PICKER_PROXY_URL.
# Fallback: imf.org RSS at /en/News/SearchNews?as_rss=true&...
_IMF_VIDEO_LINK_RE = re.compile(
    r'/en/videos/view/(\d{10,16})', re.I,
)


def _imf_list_video_ids(hours_back: int) -> list[tuple[str, str]]:
    """Scrape imf.org SearchNews (video filter) via proxy for fresh ids.

    The listing endpoint 403s our datacenter IP at the CDN; the Playback API
    itself is reachable direct. So we proxy ONLY the discovery hop.
    """
    del hours_back
    home = "https://www.imf.org"
    listing_url = f"{home}/en/News/SearchNews?f:type=[Videos]&page=1"
    try:
        r = http_get(listing_url, use_proxy=True, timeout=30)
        if r.status_code != 200:
            log.warning("imf listing %d: %s", r.status_code, r.text[:200])
            return []
        html = r.text
    except Exception as exc:
        log.warning("imf listing fetch failed: %s", exc)
        return []

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for m in _IMF_VIDEO_LINK_RE.finditer(html):
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        out.append((vid, f"{home}/en/videos/view/{vid}"))
        if len(out) >= 12:
            break
    return out


IMF_SOURCE = register(BrightcoveSource(
    source_id="imf",
    display_name="IMF",
    home_url="https://www.imf.org/en/News/SearchNews?f:type=[Videos]",
    account_id="45228659001",
    policy_key=(
        "BCpkADawqM3uKJkGFZivLv2KSM0eEM0YGCYCB0RB7Bxh0Jmu6gffclzfkZCpty"
        "UWCUgYJf60YjKpQHo80g66fVlwkU5jc-XVGL7s4VKtK3rkNdhlGCcROQ8HE08"
    ),
    list_video_ids=_imf_list_video_ids,
    listing_needs_proxy=True,
))
