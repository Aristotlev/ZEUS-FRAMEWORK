"""Shared abstractions + helpers for first-party video sources."""
from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("zeus.event_clip.sources")

# Realistic browser UA. Most gov CDNs 403 the bare requests/curl default.
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT_S = 30


@dataclass
class UploadCandidate:
    """A discovered video from a source, ready for fetch_and_cut().

    Mirrors ChannelUpload's surface so callers in event_clip.py don't need a
    second dataclass — same field meanings, just generalized across sources.

    Browser-fallback fields (referer, use_browser_fallback) let a source
    declare that direct media fetches from the agent container may 403 at
    the CDN, and that the central download_media should retry through the
    deploy/browser-fetch sidecar with the page primed as referer. Used by
    cspan (CloudFront) and senate_banking (Akamai).
    """
    source_id: str          # e.g. "cspan", "federalreserve"
    video_id: str           # source-internal ID (CSPAN program ID, Brightcove ID, etc.)
    title: str
    page_url: str           # human-facing page (used for attribution + dedup)
    upload_date: str        # ISO 8601 UTC
    duration_s: int         # 0 if not known until download time
    media_url: str          # direct MP4 or HLS .m3u8
    media_kind: str         # "mp4" or "hls"
    referer: Optional[str] = None
    use_browser_fallback: bool = False


class SourceListingError(RuntimeError):
    """Source's listing endpoint failed or returned nothing usable."""


class VideoSource(ABC):
    """Each first-party source implements list_recent() + (optionally) custom download.

    The default download_media() handles mp4 and hls. Sources only override
    if they need custom headers/proxy or expire-then-refresh semantics.
    """

    source_id: str = ""
    display_name: str = ""
    home_url: str = ""

    @abstractmethod
    def list_recent(self, *, hours_back: int) -> list[UploadCandidate]:
        """Return uploads from the source within the lookback window.

        Implementations should return [] on transient failures and only raise
        SourceListingError when something is structurally wrong (CDN gone,
        HTML shape changed). The cron tolerates [] silently.
        """


# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------
def picker_proxy_url() -> Optional[str]:
    """Returns the residential proxy URL when available.

    Same env var as the picker uses (ZEUS_PICKER_PROXY_URL — see [[project_picker_proxy]]).
    Sources that 403 the Hetzner datacenter IP set need_proxy=True on the
    fetch helper; sources that work fine direct pass through.
    """
    url = (os.getenv("ZEUS_PICKER_PROXY_URL") or "").strip()
    return url or None


def http_get(
    url: str,
    *,
    use_proxy: bool = False,
    headers: Optional[dict] = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> requests.Response:
    """One-stop GET that handles browser UA + optional proxy.

    Raises requests.RequestException on transport failure. Caller decides
    whether to swallow vs. let it bubble (most list_recent() implementations
    swallow and return []).
    """
    h = {"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    proxies = None
    if use_proxy:
        proxy = picker_proxy_url()
        if proxy:
            proxies = {"http": proxy, "https": proxy}
    return requests.get(url, headers=h, proxies=proxies, timeout=timeout)


def _ffmpeg_bin() -> str:
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    raise SourceListingError("ffmpeg not in PATH — required for HLS download")


def download_media(
    media_url: str,
    media_kind: str,
    dest_dir: pathlib.Path,
    *,
    use_proxy: bool = False,
    extra_headers: Optional[dict] = None,
    timeout_s: int = 600,
    referer: Optional[str] = None,
    use_browser_fallback: bool = False,
) -> pathlib.Path:
    """Download media to dest_dir/source.mp4 regardless of upstream format.

    - media_kind=="mp4"   : streaming GET, write to disk
    - media_kind=="hls"   : ffmpeg -i master.m3u8 -c copy out.mp4 (no re-encode)
    - media_kind=="local" : shutil.copyfile from media_url (filesystem path)
                            into dest_dir/source.mp4. Used by the manual-drop
                            source — file is already on disk, no network IO.

    For HLS through a proxy, sets http_proxy/https_proxy in the subprocess
    env so ffmpeg's HTTP client picks it up. ffmpeg copies segments without
    re-encoding so this is fast even for hour-long hearings.

    If use_browser_fallback=True, a direct fetch that 403s (or otherwise
    fails) is retried through the deploy/browser-fetch sidecar with
    `referer` as the priming page. Used for cspan (CloudFront) and
    senate_banking (Akamai) where the Hetzner datacenter IP is rejected
    by the CDN even with a clean UA + Referer.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / "source.mp4"

    if media_kind == "local":
        src = pathlib.Path(media_url)
        if not src.is_file():
            raise SourceListingError(f"local media file not found: {src}")
        # Copy (not move) — manual source leaves the original in the inbox
        # so the user can re-trigger by clearing the seen-DB without losing
        # the file. ffmpeg downstream auto-detects the container by magic
        # bytes, so renaming non-mp4 to .mp4 is harmless.
        shutil.copyfile(src, out_path)
        return out_path

    merged_headers = {"User-Agent": DEFAULT_UA}
    if referer:
        merged_headers["Referer"] = referer
    if extra_headers:
        merged_headers.update(extra_headers)

    if media_kind == "mp4":
        try:
            return _download_mp4_direct(
                media_url, out_path, merged_headers, use_proxy, timeout_s,
            )
        except (requests.RequestException, SourceListingError) as exc:
            if not use_browser_fallback:
                raise
            log.warning(
                "mp4 direct fetch failed (%s) — falling back to browser sidecar",
                exc,
            )
            return _download_mp4_via_browser(
                media_url, out_path, referer=referer, prime_with_page=referer,
            )

    if media_kind == "hls":
        try:
            return _download_hls_via_ffmpeg(
                media_url, out_path, merged_headers, use_proxy, timeout_s,
            )
        except SourceListingError as exc:
            if not use_browser_fallback:
                raise
            log.warning(
                "hls ffmpeg fetch failed (%s) — falling back to browser sidecar",
                exc,
            )
            return _download_hls_via_browser(
                media_url, out_path, dest_dir,
                referer=referer, prime_with_page=referer, timeout_s=timeout_s,
            )

    raise SourceListingError(f"unknown media_kind: {media_kind!r}")


def _download_mp4_direct(
    media_url: str,
    out_path: pathlib.Path,
    headers: dict,
    use_proxy: bool,
    timeout_s: int,
) -> pathlib.Path:
    proxies = None
    if use_proxy and (proxy := picker_proxy_url()):
        proxies = {"http": proxy, "https": proxy}
    with requests.get(
        media_url, headers=headers, proxies=proxies, stream=True, timeout=timeout_s,
    ) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    return out_path


def _download_hls_via_ffmpeg(
    media_url: str,
    out_path: pathlib.Path,
    headers: dict,
    use_proxy: bool,
    timeout_s: int,
) -> pathlib.Path:
    env = os.environ.copy()
    if use_proxy and (proxy := picker_proxy_url()):
        env["http_proxy"] = proxy
        env["https_proxy"] = proxy
    # -user_agent on ffmpeg's HTTP demuxer keeps Akamai/Mediasite happy.
    # Other headers (Referer, custom) ride through -headers.
    other = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
    cmd = [
        _ffmpeg_bin(), "-y",
        "-user_agent", headers.get("User-Agent", DEFAULT_UA),
    ]
    if other:
        hdr_block = "".join(f"{k}: {v}\r\n" for k, v in other.items())
        cmd.extend(["-headers", hdr_block])
    cmd.extend([
        "-i", media_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        str(out_path),
    ])
    res = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_s, check=False, env=env,
    )
    if res.returncode != 0 or not out_path.is_file():
        raise SourceListingError(
            f"ffmpeg HLS download failed: {(res.stderr or '')[-400:]}"
        )
    return out_path


def _download_mp4_via_browser(
    media_url: str,
    out_path: pathlib.Path,
    *,
    referer: Optional[str],
    prime_with_page: Optional[str],
) -> pathlib.Path:
    """Pull a single mp4 through the deploy/browser-fetch sidecar."""
    # Local import — keeps base.py free of an _browser import cycle for the
    # vast majority of source modules that never need a fallback.
    from . import _browser

    body = _browser.fetch_binary(
        media_url, referer=referer, prime_with_page=prime_with_page,
    )
    if not body:
        raise SourceListingError(
            "browser sidecar mp4 fetch returned no bytes "
            "(sidecar unreachable, upstream non-2xx, or empty body)"
        )
    out_path.write_bytes(body)
    return out_path


# ---------------------------------------------------------------------------
# HLS-via-browser fallback
# ---------------------------------------------------------------------------
# Strategy: pull the master playlist via /fetch-binary, pick the highest
# bitrate variant, pull its media playlist, then pull each .ts segment
# through the sidecar. Concatenate segments byte-wise into a single MPEG-TS
# (legal for the TS container — each segment is a self-contained PES stream
# with PAT/PMT) and have ffmpeg remux to mp4 with -c copy. No re-encode.
#
# Trade-off: a 1h hearing at 6s segments = ~600 sequential round-trips
# through the sidecar. At ~250ms each that's ~2.5min — slower than ffmpeg's
# direct fetch, but acceptable on an hourly cron tick that already includes
# a Gemini multimodal call. The watcher's timeout_s (default 600s) covers
# it. The sidecar serializes contexts via asyncio.Lock so we don't try to
# parallelize; cspan + senate_banking ship ≤1 clip per cron pass.
_M3U8_STREAM_INF_RE = re.compile(
    r"#EXT-X-STREAM-INF:([^\n]+)\n([^\n#]+)", re.I,
)
_M3U8_BANDWIDTH_RE = re.compile(r"BANDWIDTH=(\d+)", re.I)


def _resolve_uri(base_url: str, uri: str) -> str:
    """Resolve `uri` against `base_url`. Absolute URIs pass through."""
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    if uri.startswith("//"):
        scheme = base_url.split(":", 1)[0]
        return f"{scheme}:{uri}"
    if uri.startswith("/"):
        # host root
        m = re.match(r"(https?://[^/]+)", base_url)
        return (m.group(1) if m else "") + uri
    # relative path — strip query/fragment then drop last path segment
    bare = base_url.split("?", 1)[0].split("#", 1)[0]
    parent = bare.rsplit("/", 1)[0]
    return f"{parent}/{uri}"


def _pick_best_variant(master_text: str, master_url: str) -> Optional[str]:
    """Return the highest-bandwidth variant playlist URL, or None if the
    text is already a media playlist (no #EXT-X-STREAM-INF lines)."""
    best: tuple[int, str] = (-1, "")
    for m in _M3U8_STREAM_INF_RE.finditer(master_text):
        attrs, uri = m.group(1), m.group(2).strip()
        bw_m = _M3U8_BANDWIDTH_RE.search(attrs)
        bw = int(bw_m.group(1)) if bw_m else 0
        if bw > best[0] and uri:
            best = (bw, uri)
    if best[1]:
        return _resolve_uri(master_url, best[1])
    return None


def _parse_media_playlist(playlist_text: str, playlist_url: str) -> list[str]:
    """Extract segment URIs from an HLS media playlist, resolved to
    absolute URLs. Skips comments, tags, and empty lines."""
    segs: list[str] = []
    for line in playlist_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        segs.append(_resolve_uri(playlist_url, line))
    return segs


def _download_hls_via_browser(
    media_url: str,
    out_path: pathlib.Path,
    dest_dir: pathlib.Path,
    *,
    referer: Optional[str],
    prime_with_page: Optional[str],
    timeout_s: int,
) -> pathlib.Path:
    from . import _browser

    master_bytes = _browser.fetch_binary(
        media_url, referer=referer, prime_with_page=prime_with_page,
    )
    if not master_bytes:
        raise SourceListingError(
            "browser sidecar HLS master fetch returned no bytes"
        )
    master_text = master_bytes.decode("utf-8", errors="replace")

    variant_url = _pick_best_variant(master_text, media_url)
    if variant_url:
        # Two-level playlist — pull the chosen variant.
        variant_bytes = _browser.fetch_binary(
            variant_url, referer=referer, prime_with_page=None,
        )
        if not variant_bytes:
            raise SourceListingError(
                "browser sidecar HLS variant fetch returned no bytes"
            )
        playlist_text = variant_bytes.decode("utf-8", errors="replace")
        playlist_url = variant_url
    else:
        playlist_text = master_text
        playlist_url = media_url

    segs = _parse_media_playlist(playlist_text, playlist_url)
    if not segs:
        raise SourceListingError("HLS playlist had no segments")

    # Stream segments straight into a single .ts on disk — avoids holding
    # an hour of video in memory.
    ts_path = dest_dir / "source.ts"
    written = 0
    with open(ts_path, "wb") as fh:
        for i, seg_url in enumerate(segs):
            seg = _browser.fetch_binary(
                seg_url, referer=referer, prime_with_page=None,
            )
            if not seg:
                raise SourceListingError(
                    f"HLS segment {i+1}/{len(segs)} returned no bytes via sidecar"
                )
            fh.write(seg)
            written += len(seg)
    log.info(
        "HLS-via-browser: wrote %d segments (%.1f MB) to %s",
        len(segs), written / 1_048_576, ts_path,
    )

    # Remux ts → mp4 with -c copy (no re-encode).
    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", str(ts_path),
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        str(out_path),
    ]
    res = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_s, check=False,
    )
    try:
        ts_path.unlink()
    except OSError:
        pass
    if res.returncode != 0 or not out_path.is_file():
        raise SourceListingError(
            f"ffmpeg ts→mp4 remux failed: {(res.stderr or '')[-400:]}"
        )
    return out_path


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
SOURCE_REGISTRY: dict[str, VideoSource] = {}


def register(source: VideoSource) -> VideoSource:
    """Register a source by its source_id. Module-level call at import."""
    if not source.source_id:
        raise ValueError(f"source {source!r} missing source_id")
    SOURCE_REGISTRY[source.source_id] = source
    return source


def resolve_source(source_id_or_url: str) -> Optional[VideoSource]:
    """Look up a source by ID. Accepts either bare ID ("cspan") or a legacy
    YouTube URL — the latter returns None so callers can warn-and-skip
    instead of crashing during the migration window.
    """
    key = (source_id_or_url or "").strip()
    if not key:
        return None
    # Tolerate the legacy URL format during transition; the cron's env var
    # might still hold "https://www.youtube.com/@..." for a tick or two.
    if key.startswith("http"):
        return None
    return SOURCE_REGISTRY.get(key)
