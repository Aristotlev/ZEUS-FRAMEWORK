"""Client for the deploy/browser-fetch sidecar.

The sidecar runs a headless Chromium on the same docker-compose network
(service name `browser-fetch`, port 8000) and exposes POST /fetch and
POST /fetch-binary. Sources that 403 against curl/requests from the
Hetzner IP — c-span (Cloudflare), imf (listing CDN), senate banking
(Akamai) — call into here to get a real-browser fetch.

If ZEUS_BROWSER_FETCH_URL is unset OR the sidecar is unreachable, the
client returns None so callers degrade silently to an empty source.
That keeps laptop/dev runs from crashing when the sidecar isn't
deployed locally.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("zeus.event_clip.sources.browser")

DEFAULT_URL = "http://browser-fetch:8000"


def base_url() -> Optional[str]:
    """Resolve the sidecar URL from env, falling back to the compose name.

    In prod the compose stack wires `browser-fetch` on the zeus-net bridge,
    so the default resolves cleanly. On a laptop without the sidecar, the
    env var is unset AND the hostname doesn't resolve, so the client treats
    "sidecar unavailable" the same as "no source data" and returns [].
    """
    url = (os.getenv("ZEUS_BROWSER_FETCH_URL") or DEFAULT_URL).strip().rstrip("/")
    return url or None


@dataclass
class FetchResult:
    status: int
    final_url: str
    html: str
    captured_urls: list[str]


def fetch_page(
    url: str,
    *,
    wait_for_selector: Optional[str] = None,
    wait_seconds: float = 2.0,
    capture_responses_regex: Optional[str] = None,
    wait_until: str = "networkidle",
    referer: Optional[str] = None,
    timeout_s: int = 45,
) -> Optional[FetchResult]:
    """Render `url` through the sidecar Chromium. Returns None on failure."""
    base = base_url()
    if not base:
        return None
    payload = {
        "url": url,
        "wait_for_selector": wait_for_selector,
        "wait_seconds": wait_seconds,
        "capture_responses_regex": capture_responses_regex,
        "wait_until": wait_until,
        "referer": referer,
    }
    try:
        r = requests.post(f"{base}/fetch", json=payload, timeout=timeout_s)
    except requests.RequestException as exc:
        log.info("browser-fetch unreachable (%s) — degrading source to empty", exc)
        return None
    if r.status_code != 200:
        log.warning("browser-fetch /fetch %d for %s: %s",
                    r.status_code, url, r.text[:200])
        return None
    try:
        data = r.json()
    except ValueError:
        log.warning("browser-fetch /fetch returned non-JSON for %s", url)
        return None
    return FetchResult(
        status=int(data.get("status") or 0),
        final_url=str(data.get("final_url") or ""),
        html=str(data.get("html") or ""),
        captured_urls=list(data.get("captured_urls") or []),
    )


def fetch_binary(
    url: str,
    *,
    referer: Optional[str] = None,
    prime_with_page: Optional[str] = None,
    timeout_s: int = 120,
) -> Optional[bytes]:
    """Download `url` bytes through the sidecar's browser context.

    Returns None if the sidecar is unavailable, the upstream returned
    a non-2xx, or the request timed out.
    """
    base = base_url()
    if not base:
        return None
    payload = {
        "url": url,
        "referer": referer,
        "prime_with_page": prime_with_page,
    }
    try:
        r = requests.post(
            f"{base}/fetch-binary", json=payload, timeout=timeout_s,
        )
    except requests.RequestException as exc:
        log.info("browser-fetch /fetch-binary unreachable: %s", exc)
        return None
    if r.status_code != 200:
        log.warning("browser-fetch /fetch-binary %d for %s",
                    r.status_code, url)
        return None
    return r.content


def is_available() -> bool:
    """Quick health probe — used by source modules to skip cleanly when
    the sidecar isn't running (laptop / dev / first-boot before the
    browser-fetch service is up).
    """
    base = base_url()
    if not base:
        return False
    try:
        r = requests.get(f"{base}/healthz", timeout=3)
    except requests.RequestException:
        return False
    return r.status_code == 200
