"""First-party gov/official video sources for the EVENT_CLIP pipeline.

Replaces yt-dlp + YouTube. YouTube's 2026 stack (residential IP block +
session auth + PO Token + nsig + SABR) is no longer reliably bypassable; see
the inline note in lib/event_clip.py for the full history. Every source here
hits the original publisher (federalreserve.gov, c-span.org, imf.org) and
downloads via plain HTTP/HLS — no cookies, no PO tokens, no arms race.

A source is identified by a short `source_id` ("cspan", "federalreserve",
"imf"). The cron poller uses EVENT_CLIP_CHANNELS as a comma-separated list
of these IDs — historically it held YouTube channel URLs.

V1 sources actually enabled by default:
  - federalreserve — Brightcove direct MP4/HLS (account 66043936001).
    Tested end-to-end from prod IP on 2026-05-15: returns April FOMC
    Powell presser with a signed Brightcove CDN MP4. No proxy needed.

Sources registered but NOT in DEFAULT_SOURCES (kept in-tree so V2 can
re-enable once we add a Cloudflare/Akamai bypass — Playwright sidecar or
Bright Data Web Unlocker):
  - cspan — c-span.org sits behind Cloudflare 202 challenge from Hetzner;
            program pages are also SPA-rendered with :::title::: placeholders.
            Deterministic m3u8 URLs are CloudFront-403'd without signed tokens.
  - imf   — /en/News/SearchNews 403s even through Webshare residential
            proxy. Listing is unreachable; Brightcove Playback API itself
            does work direct, so once listing is solved this lights up.

Tier 2 sources fully dropped from the original 11-channel allowlist
(audited 2026-05-15 — see audit task transcripts):
  - ecb            — fully YouTube-backed as of 2026; no first-party VOD
  - senate_banking — Akamai 403 regardless of headers/proxy
  - treasury       — YorkCast geo-blocks Hetzner; Vbrick API needs auth
  - bls/sec/hfsc/boe/boj — YouTube-only or no video infrastructure
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

# Default ordered source list. The cron polls them in this order. Order
# affects only tie-breaking when two sources surface the same event — we
# prefer the publisher's own site over C-SPAN's secondary capture.
# Only sources that produce candidates from prod's Hetzner IP without
# additional infrastructure. cspan + imf are registered but stay off the
# default list until they can actually return media.
DEFAULT_SOURCES: list[str] = [
    "federalreserve",
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
]
