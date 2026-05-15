"""C-SPAN.org source — scrapes homepage + parses JSON-LD VideoObject per page.

C-SPAN's video catalog spans the original allowlist's house-committee /
senate-committee / news-conference / white-house-event content and more.
Their HLS manifest URLs are deterministic by program ID, which makes the
extractor very stable.

Pattern:
  1. Fetch https://www.c-span.org/ (~30 recent tiles).
  2. Match /program/<category>/<slug>/<6+digit ID> links.
  3. Filter to the category whitelist — drops rallies, book talks, vignettes.
  4. For each program page, parse <script type="application/ld+json"> for
     name / uploadDate / duration / description.
  5. Build the HLS URL deterministically: m3u8-0.c-spanvideo.org/program/program.<ID>.tsc.m3u8
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .base import (
    SourceListingError,
    UploadCandidate,
    VideoSource,
    http_get,
    register,
)

log = logging.getLogger("zeus.event_clip.sources.cspan")

# Categories that produce newsworthy soundbites. Everything else (campaign
# rallies, book talks, history vignettes) is excluded.
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


class CSpanSource(VideoSource):
    source_id = "cspan"
    display_name = "C-SPAN"
    home_url = "https://www.c-span.org/"

    def list_recent(self, *, hours_back: int) -> list[UploadCandidate]:
        try:
            r = http_get(self.home_url, timeout=20)
            if r.status_code == 403:
                # CSPAN sometimes hits Cloudflare friction on first req from
                # a cold IP. One retry with picker proxy clears it.
                r = http_get(self.home_url, use_proxy=True, timeout=20)
            r.raise_for_status()
            html = r.text
        except Exception as exc:
            log.warning("c-span homepage fetch failed: %s", exc)
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        seen: set[str] = set()
        results: list[UploadCandidate] = []
        for m in _PROGRAM_LINK_RE.finditer(html):
            category = m.group(1).lower()
            program_id = m.group(2)
            if category not in _CATEGORY_WHITELIST:
                continue
            if program_id in seen:
                continue
            seen.add(program_id)

            # We need the page's exact slug to construct the canonical URL,
            # so capture the full match path from the source HTML.
            link_match = re.search(
                rf'/program/{re.escape(category)}/[a-z0-9\-]+/{program_id}\b',
                html, re.I,
            )
            if not link_match:
                continue
            page_url = f"https://www.c-span.org{link_match.group(0)}"

            try:
                meta = self._fetch_program_meta(page_url, program_id)
            except Exception as exc:
                log.warning(
                    "c-span program %s meta fetch failed: %s", program_id, exc,
                )
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
            ))
            if len(results) >= 10:
                break
        return results

    def _fetch_program_meta(self, page_url: str, program_id: str) -> dict:
        r = http_get(page_url, timeout=20)
        if r.status_code != 200:
            raise SourceListingError(f"program page {r.status_code}")
        html = r.text

        # Parse every embedded JSON-LD block; the VideoObject is one of them.
        for jm in _JSONLD_RE.finditer(html):
            blob = jm.group(1).strip()
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            # Some pages emit a graph with @type: VideoObject inside; handle
            # both bare-VideoObject and @graph variants.
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

        raise SourceListingError(
            f"no VideoObject JSON-LD on program/{program_id}"
        )


CSPAN_SOURCE = register(CSpanSource())
