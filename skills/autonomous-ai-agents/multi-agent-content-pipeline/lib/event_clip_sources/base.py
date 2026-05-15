"""Shared abstractions + helpers for first-party video sources."""
from __future__ import annotations

import logging
import os
import pathlib
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
    """
    source_id: str          # e.g. "cspan", "federalreserve"
    video_id: str           # source-internal ID (CSPAN program ID, Brightcove ID, etc.)
    title: str
    page_url: str           # human-facing page (used for attribution + dedup)
    upload_date: str        # ISO 8601 UTC
    duration_s: int         # 0 if not known until download time
    media_url: str          # direct MP4 or HLS .m3u8
    media_kind: str         # "mp4" or "hls"


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

    if media_kind == "mp4":
        proxies = None
        if use_proxy and (proxy := picker_proxy_url()):
            proxies = {"http": proxy, "https": proxy}
        headers = {"User-Agent": DEFAULT_UA}
        if extra_headers:
            headers.update(extra_headers)
        with requests.get(
            media_url, headers=headers, proxies=proxies, stream=True, timeout=timeout_s,
        ) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
        return out_path

    if media_kind == "hls":
        env = os.environ.copy()
        if use_proxy and (proxy := picker_proxy_url()):
            env["http_proxy"] = proxy
            env["https_proxy"] = proxy
        # -user_agent on ffmpeg's HTTP demuxer keeps Akamai/Mediasite happy.
        # Some CDNs require a Referer too — passed via -headers when present.
        cmd = [
            _ffmpeg_bin(), "-y",
            "-user_agent", DEFAULT_UA,
        ]
        if extra_headers:
            # ffmpeg wants \r\n-separated header block via -headers.
            hdr_block = "".join(f"{k}: {v}\r\n" for k, v in extra_headers.items())
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

    raise SourceListingError(f"unknown media_kind: {media_kind!r}")


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
