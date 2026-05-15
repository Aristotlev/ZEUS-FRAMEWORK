"""Manual-drop source — scans an inbox dir on the host for video files.

Workflow:
    scp ~/Downloads/some_hearing.mp4 \
        root@<prod-vm>:/opt/zeus/event_clip_inbox/
    # next EVENT_CLIP cron tick (≤1h) picks it up, Gemini chooses the
    # ≤90s soundbite, ships to Publer + Substack Note.

The inbox dir is bind-mounted into the container at the same path
(/opt/zeus/event_clip_inbox), declared in deploy/docker-compose.prod.yml.

Dedup is handled by the watcher's existing seen-DB on (source_id,
video_id); we derive video_id from the file's SHA-1 (first 16 hex chars)
so the same content dropped twice is naturally suppressed.

Files stay in the inbox after processing — the user owns cleanup. The
seen-DB has a 14-day retention window, so re-dropping after that does
re-process. Override the inbox path via EVENT_CLIP_INBOX_DIR.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pathlib
from datetime import datetime, timedelta, timezone

from .base import (
    UploadCandidate,
    VideoSource,
    register,
)

log = logging.getLogger("zeus.event_clip.sources.manual")

DEFAULT_INBOX = "/opt/zeus/event_clip_inbox"

# Containers ffmpeg can demux without re-encode on the read side. mkv/webm
# work too via libmkv/libwebm; .ts is HLS-segment leftovers users sometimes
# concat by hand.
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts"}


def inbox_path() -> pathlib.Path:
    """Resolve the inbox dir from env, defaulting to the compose mount."""
    return pathlib.Path(os.getenv("EVENT_CLIP_INBOX_DIR", DEFAULT_INBOX))


def _hash_file(path: pathlib.Path) -> str:
    """SHA-1 first 16 hex chars — same content -> same video_id, so the
    cron's dedup catches re-drops naturally. We hash up to the first 8 MB
    only; movie files of 1 GB would otherwise burn CPU every cron pass.
    The header bytes are stable enough to uniquely identify a file in
    practice — and identity here is "did we already process THIS specific
    drop", not cryptographic.
    """
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read(8 * 1024 * 1024))
    return h.hexdigest()[:16]


def _title_from_filename(stem: str) -> str:
    """Clean a filename stem into a human-readable title.

    Replaces underscores/hyphens with spaces and title-cases short words.
    Long descriptive names ("2026-05-15 SEC Atkins crypto remarks") pass
    through fine; junky ones ("DCIM_00043") still produce something.
    """
    clean = stem.replace("_", " ").replace("-", " ").strip()
    while "  " in clean:
        clean = clean.replace("  ", " ")
    if not clean:
        return "Untitled clip"
    # Don't auto-title-case if the user already capitalized substantively
    # (avoids "Sec Atkins Crypto Remarks" mangling proper acronyms).
    if any(c.isupper() for c in clean):
        return clean[:200]
    return clean.title()[:200]


class ManualInboxSource(VideoSource):
    """Source backed by files dropped into the inbox dir.

    list_recent() walks the dir (non-recursive — keep it simple), filters
    to video extensions, skips zero-byte files, and yields one
    UploadCandidate per file. The cron's dedup DB handles already-seen.
    """

    source_id = "manual"
    display_name = "Manual upload"
    home_url = "file:///opt/zeus/event_clip_inbox"

    def list_recent(self, *, hours_back: int) -> list[UploadCandidate]:
        # hours_back is honoured loosely: files older than the window are
        # skipped to match the rest of the pipeline's "fresh content"
        # contract. The seen-DB takes care of preventing re-ships within
        # the window if a file is left in the dir.
        root = inbox_path()
        if not root.is_dir():
            log.info("manual inbox %s missing — source returning []", root)
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        out: list[UploadCandidate] = []
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in _VIDEO_EXTENSIONS:
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue
            mtime = datetime.fromtimestamp(
                entry.stat().st_mtime, tz=timezone.utc,
            )
            if mtime < cutoff:
                continue
            try:
                video_id = _hash_file(entry)
            except OSError as exc:
                log.warning("manual: failed to hash %s: %s", entry, exc)
                continue
            out.append(UploadCandidate(
                source_id=self.source_id,
                video_id=video_id,
                title=_title_from_filename(entry.stem),
                # page_url uses file:// scheme so the caption builder and
                # archive layer have a stable handle. Not shown to viewers.
                page_url=f"file://{entry}",
                upload_date=mtime.isoformat(),
                duration_s=0,  # probed downstream by ffprobe
                # media_url carries the absolute path; media_kind="local"
                # tells base.download_media to copy instead of HTTP-GET.
                media_url=str(entry.resolve()),
                media_kind="local",
            ))
        return out


MANUAL_SOURCE = register(ManualInboxSource())
