"""First-party gov/official video sources for the EVENT_CLIP pipeline.

Replaces yt-dlp + YouTube. YouTube's 2026 stack (residential IP block +
session auth + PO Token + nsig + SABR) is no longer reliably bypassable;
see the inline note in lib/event_clip.py for the full history. Every
source here hits the original publisher (federalreserve.gov, c-span.org,
imf.org, banking.senate.gov) directly — no PO tokens, no arms race.

A source is identified by a short `source_id` ("manual", "federalreserve",
"cspan", "imf", "senate_banking"). The cron poller uses EVENT_CLIP_CHANNELS
as a comma-separated list of these IDs.

Source unlocks:
  - federalreserve (V1, 2026-05-15) — Brightcove direct, plain HTTP.
  - manual         (V1.1, 2026-05-15) — scp drop into
                       /opt/zeus/event_clip_inbox. Pipeline picks the file
                       up on the next hourly cron, Gemini chooses the ≤90s
                       window, Publer + Substack fan-out. Use for
                       YouTube-only orgs (BLS, SEC, BoE, BoJ, ECB, HFSC)
                       we can't reach automatically — grab the clip on a
                       real browser and drop the mp4.
  - cspan          (V1.1, 2026-05-15) — listing + program-page metadata
                       via the deploy/browser-fetch headless Chromium
                       sidecar. Media download tries the deterministic
                       m3u8; if CloudFront 403s it the watcher logs
                       skipped:download_failed and we route through
                       /fetch-binary in V2.
  - imf            (V1.1, 2026-05-15) — listing via browser sidecar
                       (replaces the dead residential-proxy path);
                       Brightcove Playback API reachable direct so media
                       downloads as usual.
  - senate_banking (V1.1, 2026-05-15) — listing + hearing-page render via
                       sidecar with capture_responses_regex set to grab
                       the Akamai HLS manifest. Media download may 403 at
                       Akamai's edge; same V2 fallback as cspan if it does.

YouTube-only orgs with no first-party VOD as of 2026 (ECB, BLS, SEC, HFSC,
BOE, BOJ): no auto path. Use the `manual` drop source.
"""
from __future__ import annotations

from .base import (
    SOURCE_REGISTRY,
    SourceListingError,
    UploadCandidate,
    VideoSource,
    download_media,
    register,
    resolve_source,
)
from .brightcove import FED_SOURCE, IMF_SOURCE
from .cspan import CSPAN_SOURCE
from .manual import MANUAL_SOURCE
from .senate_banking import SENATE_BANKING_SOURCE

# Default ordered source list. The cron polls them in this order. Order
# only matters for tie-breaking when two sources surface the same event;
# we prefer the publisher's own site over C-SPAN's secondary capture.
#
# Manual goes first — if the user explicitly dropped a file, that should
# win against any auto-discovered candidate that might overlap.
DEFAULT_SOURCES: list[str] = [
    "manual",
    "federalreserve",
    "imf",
    "senate_banking",
    "cspan",
]

__all__ = [
    "SOURCE_REGISTRY",
    "SourceListingError",
    "UploadCandidate",
    "VideoSource",
    "download_media",
    "register",
    "resolve_source",
    "DEFAULT_SOURCES",
    "FED_SOURCE",
    "IMF_SOURCE",
    "CSPAN_SOURCE",
    "MANUAL_SOURCE",
    "SENATE_BANKING_SOURCE",
]
