"""Event-clip pipeline: pull real-event video from rights-safe first-party
sources, let Gemini pick the most newsworthy ≤90s soundbite, cut dual-AR
clips for short-form (9:16) and long-form (16:9) social distribution.

Sources are GOVERNMENT / OFFICIAL ONLY and hit the publisher's own site
directly — see lib/event_clip_sources/ for the per-source extractors.

Pipeline shape:
    source.list_recent() → UploadCandidate (Brightcove MP4 or HLS m3u8)
        -> download_media → 480p mp4 on disk
        -> ffmpeg extract 32kbps mono mp3 audio
        -> base64 -> OpenRouter Gemini 2.5 Flash multimodal call
        -> JSON {start_seconds, end_seconds, hook}
        -> ffmpeg cut at timestamps, encode 1080x1920 + 1920x1080 outputs

Why not yt-dlp + YouTube anymore: YouTube's 2026 stack stacks three walls
(IP reputation + Google session + PO Token + nsig + SABR). Even with a
residential proxy, a burner-account cookies.txt, and the bgutil-pot sidecar,
the stream URLs still resolve to "Only images are available for download"
about as often as not. Each layer is in an active arms race with YouTube,
which breaks the cron silently. First-party gov sites have zero bot defense
and have served the same publishers for years.

Gemini is the ONE LLM call: it transcribes + picks + summarises in a single
multimodal request. No Whisper, no separate transcribe step. The 90-second
hard cap is enforced in code after the call so a chatty model can't drift.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

import requests

from .event_clip_sources import (
    DEFAULT_SOURCES,
    SOURCE_REGISTRY,
    UploadCandidate,
    download_media,
    resolve_source,
)

log = logging.getLogger("zeus.event_clip")

# ---------------------------------------------------------------------------
# Default source list. Each entry is a short source_id resolved against the
# event_clip_sources registry. EVENT_CLIP_CHANNELS in env overrides; the var
# name is kept for backward compat with the existing cron config.
# ---------------------------------------------------------------------------
DEFAULT_CHANNELS: list[str] = list(DEFAULT_SOURCES)

# Hard caps (defensive — Gemini can drift past these without the clamp).
MIN_CLIP_SECONDS = 15
MAX_CLIP_SECONDS = 90
# Source-video duration cap. Longer videos (4-hour committee marathons)
# would balloon the Gemini token bill and don't fit in context anyway.
# Skipped with ledger row `skipped:too_long`.
MAX_SOURCE_MINUTES = 30
# yt-dlp lookback window for the cron poll.
DEFAULT_LOOKBACK_HOURS = 24

# OpenRouter Gemini model. Multimodal (audio input) supported.
GEMINI_MODEL = "google/gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
@dataclass
class ChannelUpload:
    """One fresh upload from a watched source.

    Field meanings (post-source-layer-refactor):
      video_id    — source-internal ID (Brightcove video id, CSPAN program id…)
      title       — display title from the source's metadata
      url         — human-facing page URL (used for caption attribution + dedup)
      upload_date — ISO 8601 UTC publication time
      duration_s  — source duration in seconds (0 if unknown ahead of download)
      channel_url — kept name for backward compat with callers; now stores
                    the SOURCE ID (e.g. "cspan"), not a YouTube channel URL.
      media_url   — direct MP4 or HLS .m3u8 to fetch (set by source layer)
      media_kind  — "mp4" or "hls" — selects download path in fetch_and_cut
      referer     — Referer to send on media fetch (cspan/senate_banking
                    CDNs key on it). When None, no Referer is sent.
      use_browser_fallback — if True, a 403/connection failure on the
                    direct media fetch triggers a retry through the
                    deploy/browser-fetch sidecar. Set by sources whose
                    CDN rejects the Hetzner datacenter IP.
    """
    video_id: str
    title: str
    url: str
    upload_date: str  # ISO 8601
    duration_s: int
    channel_url: str
    media_url: str = ""
    media_kind: str = "mp4"
    referer: Optional[str] = None
    use_browser_fallback: bool = False


@dataclass
class SoundbitePick:
    """Gemini's chosen ≤90s window inside the source video."""
    start_seconds: float
    end_seconds: float
    hook: str
    transcript: str  # the chosen window's quote, for caption context
    raw_cost_usd: float
    cost_source: str  # "actual" or "estimate"


@dataclass
class ClipAssets:
    """Two ffmpeg outputs ready for Publer fan-out."""
    vertical_path: str  # 1080x1920, captions burned
    landscape_path: str  # 1920x1080, no overlay
    duration_s: float


class EventClipError(RuntimeError):
    """Generic event-clip pipeline failure."""


class NoFreshUploads(EventClipError):
    """Channel has no uploads within the lookback window. Silent skip."""


class SourceTooLong(EventClipError):
    """Source video exceeds MAX_SOURCE_MINUTES. Ledger-and-skip."""


class GeminiPickerError(EventClipError):
    """Gemini multimodal call failed or returned unparseable JSON."""


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------
def channels_from_env() -> list[str]:
    raw = os.getenv("EVENT_CLIP_CHANNELS", "").strip()
    if not raw:
        return list(DEFAULT_CHANNELS)
    return [c.strip() for c in raw.split(",") if c.strip()]


def lookback_hours_from_env() -> int:
    try:
        return int(os.getenv("EVENT_CLIP_LOOKBACK_HOURS", str(DEFAULT_LOOKBACK_HOURS)))
    except ValueError:
        return DEFAULT_LOOKBACK_HOURS


# ---------------------------------------------------------------------------
# Source-layer dispatch — discovery + download
# ---------------------------------------------------------------------------
def _ffmpeg_bin() -> str:
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    raise EventClipError("ffmpeg not installed — add to Dockerfile apt layer")


def _to_channel_upload(c: UploadCandidate) -> ChannelUpload:
    """Bridge dataclass: source layer's UploadCandidate → public ChannelUpload."""
    return ChannelUpload(
        video_id=c.video_id,
        title=c.title,
        url=c.page_url,
        upload_date=c.upload_date,
        duration_s=c.duration_s,
        channel_url=c.source_id,
        media_url=c.media_url,
        media_kind=c.media_kind,
        referer=c.referer,
        use_browser_fallback=c.use_browser_fallback,
    )


def list_fresh_uploads(source_id: str, *, hours_back: int) -> list[ChannelUpload]:
    """Return uploads from `source_id` posted within `hours_back` hours.

    `source_id` is one of the registered first-party source IDs
    ("federalreserve", "cspan", "imf", …). For backward compat during the
    migration off YouTube, a legacy YouTube channel URL passed in here
    silently yields [] — the cron's env var may still hold YT URLs for a
    tick or two after deploy, and we don't want it to throw.
    """
    source = resolve_source(source_id)
    if source is None:
        log.info(
            "event_clip: source %r not in registry — skipping "
            "(legacy YouTube URL? Update EVENT_CLIP_CHANNELS to source IDs.)",
            source_id,
        )
        return []
    try:
        candidates = source.list_recent(hours_back=hours_back)
    except Exception as exc:  # broad: source code can fail in many shapes
        log.warning(
            "event_clip: source %s list_recent failed: %s", source_id, exc,
        )
        return []
    return [_to_channel_upload(c) for c in candidates]


def download_video(
    url_or_upload, out_dir: pathlib.Path,
) -> pathlib.Path:
    """Download the source video to `out_dir/source.mp4`.

    Accepts either:
      - A ChannelUpload — uses its media_url + media_kind (preferred path).
      - A bare URL string — only when it ends with `.mp4` or `.m3u8`. This
        narrow shape exists so the breaking-news watcher can pass a known
        media URL through without re-running discovery. Legacy YouTube URLs
        raise EventClipError; the source layer is the supported entry point.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(url_or_upload, ChannelUpload):
        if not url_or_upload.media_url:
            raise EventClipError(
                f"ChannelUpload from source {url_or_upload.channel_url!r} "
                f"has no media_url — source extractor needs to populate it"
            )
        return download_media(
            url_or_upload.media_url,
            url_or_upload.media_kind,
            out_dir,
            referer=url_or_upload.referer,
            use_browser_fallback=url_or_upload.use_browser_fallback,
        )

    url = str(url_or_upload).strip()
    if url.endswith(".m3u8"):
        return download_media(url, "hls", out_dir)
    if url.endswith(".mp4"):
        return download_media(url, "mp4", out_dir)
    raise EventClipError(
        f"download_video got bare URL {url!r} that isn't a direct mp4/m3u8; "
        f"pass a ChannelUpload instead. (YouTube ingestion is no longer "
        f"supported — see lib/event_clip_sources for first-party extractors.)"
    )


def probe_duration_s(path: pathlib.Path) -> float:
    """Return the duration of a media file in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    try:
        return float((out.stdout or "0").strip())
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Gemini multimodal soundbite picker (via OpenRouter)
# ---------------------------------------------------------------------------
_PICKER_PROMPT = """\
You are picking ONE clip for social media distribution from this audio recording.

The audio is from a {kind} event titled: "{title}"

Listen carefully. Pick the SINGLE most newsworthy ≤90s window — a moment that:
  - Contains a self-contained quote or statement that makes sense without prior context
  - Would be the soundbite a major financial news outlet would lead with
  - Has clear, intelligible speech (no fumbling, no long silence)

Return ONE JSON object, NO prose, NO markdown fences:
{{
  "start_seconds": <number>,
  "end_seconds": <number>,
  "hook": "<a single-line newsroom-style headline, max 100 chars, present tense>",
  "transcript": "<verbatim transcript of the chosen window, ≤500 chars>"
}}

Rules:
  - end_seconds - start_seconds MUST be between 15 and 90 seconds
  - start_seconds MUST be ≥ 0
  - The clip MUST end on a complete sentence — never mid-word
  - If NO part of this audio is newsworthy, return {{"start_seconds": -1, "end_seconds": -1, "hook": "SKIP", "transcript": "no newsworthy moment"}}
"""


def extract_audio(video_path: pathlib.Path, out_dir: pathlib.Path) -> pathlib.Path:
    """Extract 32kbps mono mp3 from `video_path` for Gemini upload.

    32kbps mono is plenty for speech understanding and keeps the base64
    payload under ~10 MB even for 30-min source videos.
    """
    audio_path = out_dir / "audio.mp3"
    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "32k",
        str(audio_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if res.returncode != 0 or not audio_path.is_file():
        raise EventClipError(f"ffmpeg audio extract failed: {(res.stderr or '')[:300]}")
    return audio_path


def pick_soundbite(
    audio_path: pathlib.Path,
    *,
    title: str,
    kind: str = "speech or hearing",
    openrouter_key: Optional[str] = None,
) -> SoundbitePick:
    """One OpenRouter call to Gemini 2.5 Flash with the audio attached.

    Returns SoundbitePick with start/end clamped to [0, source_duration] and
    end-start clamped to [MIN_CLIP_SECONDS, MAX_CLIP_SECONDS]. Raises
    GeminiPickerError on transport failure or unparseable response. Caller
    handles `hook == "SKIP"` as a "no newsworthy moment" no-op.
    """
    key = openrouter_key or os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise GeminiPickerError("OPENROUTER_API_KEY not set")

    audio_bytes = audio_path.read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    prompt = _PICKER_PROMPT.format(kind=kind, title=title)

    body = {
        "model": GEMINI_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                # OpenAI-style audio input. OpenRouter routes this to Gemini's
                # native inline_data format under the hood.
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "mp3"}},
            ],
        }],
        "max_tokens": 600,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=300,
        )
    except requests.RequestException as exc:
        raise GeminiPickerError(f"OpenRouter transport failure: {exc}") from exc
    if r.status_code != 200:
        raise GeminiPickerError(f"OpenRouter {r.status_code}: {r.text[:300]}")
    payload = r.json()

    text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not text:
        raise GeminiPickerError("OpenRouter returned empty content")
    # Strip any accidental code fences the model added.
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiPickerError(f"Gemini returned non-JSON: {text[:200]}") from exc

    usage = payload.get("usage") or {}
    raw_cost = usage.get("cost")
    cost_source = "estimate"
    cost = 0.0
    if raw_cost is not None:
        try:
            cost = float(raw_cost)
            cost_source = "actual"
        except (TypeError, ValueError):
            cost = 0.0
    if cost == 0.0:
        # Rough fallback: gemini-2.5-flash input audio ≈ $0.00125/min, output
        # tokens ≈ $0.30/M. We don't know exact audio-minute billing here,
        # so estimate from completion tokens only.
        comp_tok = float(usage.get("completion_tokens") or 0)
        cost = round(comp_tok * 0.30 / 1_000_000.0, 6)

    return SoundbitePick(
        start_seconds=float(obj.get("start_seconds", -1)),
        end_seconds=float(obj.get("end_seconds", -1)),
        hook=str(obj.get("hook", "")).strip(),
        transcript=str(obj.get("transcript", "")).strip(),
        raw_cost_usd=cost,
        cost_source=cost_source,
    )


def clamp_window(start: float, end: float, source_duration_s: float) -> tuple[float, float]:
    """Defensive clamp — Gemini sometimes drifts past MAX_CLIP_SECONDS, picks
    a window narrower than MIN, or overshoots the source duration.

    Behaviour:
      - negative timestamps mean "SKIP" -> raise EventClipError
      - over-long windows are trimmed to MAX_CLIP_SECONDS
      - too-short windows are SYMMETRICALLY PADDED toward MIN_CLIP_SECONDS,
        bounded by [0, source_duration]. Many newsworthy quotes are genuinely
        12-13s long; we want them shipped with ~2-3s of breathing room, not
        rejected. Only when the source itself is shorter than MIN do we raise.
    """
    if start < 0 or end < 0:
        raise EventClipError("Gemini returned SKIP / negative timestamps")
    if source_duration_s < MIN_CLIP_SECONDS:
        raise EventClipError(
            f"source only {source_duration_s:.1f}s long (min clip {MIN_CLIP_SECONDS}s)"
        )

    # Trim overshoot first.
    end = min(end, source_duration_s)
    start = max(0.0, start)
    if end <= start:
        raise EventClipError(f"invalid window: start={start:.1f} end={end:.1f}")

    if end - start > MAX_CLIP_SECONDS:
        end = start + MAX_CLIP_SECONDS
        return start, end

    if end - start < MIN_CLIP_SECONDS:
        # Symmetric pad toward MIN_CLIP_SECONDS, clipped to source bounds.
        needed = MIN_CLIP_SECONDS - (end - start)
        pad_each_side = needed / 2.0
        new_start = max(0.0, start - pad_each_side)
        new_end = min(source_duration_s, end + pad_each_side)
        # If one side hit a bound, push the other to make up the deficit.
        if new_end - new_start < MIN_CLIP_SECONDS:
            deficit = MIN_CLIP_SECONDS - (new_end - new_start)
            if new_start > 0:
                new_start = max(0.0, new_start - deficit)
            else:
                new_end = min(source_duration_s, new_end + deficit)
        start, end = new_start, new_end

    return start, end


# ---------------------------------------------------------------------------
# ffmpeg dual-AR cutter
# ---------------------------------------------------------------------------
def cut_dual_ar(
    source_path: pathlib.Path,
    *,
    start_s: float,
    end_s: float,
    hook: str,
    out_dir: pathlib.Path,
) -> ClipAssets:
    """Produce two encoded clips from `source_path`:

      - clip_vertical.mp4   1080x1920, hook burned in at bottom, brand top-right
      - clip_landscape.mp4  1920x1080, no overlay (clean newsroom-style)

    Both are re-encoded (not stream-copied) so the timing is frame-accurate
    and platform uploaders don't reject misaligned keyframes.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    duration_s = end_s - start_s

    vertical = out_dir / "clip_vertical.mp4"
    landscape = out_dir / "clip_landscape.mp4"

    # Vertical 1080x1920 — TikTok-style blurred-background fill instead of
    # black bars. Source (typically 480p from yt-dlp) is too small for IG
    # Reels' implicit "must look fullscreen" check, and Publer's IG handler
    # rejected our letterboxed first attempt with "Post type is not valid"
    # (2026-05-15 Bowman run). The blurred-bg pattern:
    #   - duplicate the input stream
    #   - bg: scale-to-cover 1080x1920, crop to fit, heavy boxblur
    #   - fg: scale to width 1080 maintaining aspect
    #   - overlay fg centered on bg
    # Result is a fullscreen vertical Reel-spec frame with no flat-black
    # padding, which IG/TikTok/Shorts all accept cleanly.
    hook_escaped = (hook or "").replace("'", "").replace(":", " -")[:120]
    vf_vertical = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=24:5[bgblur];"
        "[fg]scale=1080:-2[fgs];"
        "[bgblur][fgs]overlay=(W-w)/2:(H-h)/2,"
        f"drawtext=text='{hook_escaped}':"
        "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        "fontcolor=white:fontsize=42:"
        "box=1:boxcolor=black@0.7:boxborderw=18:"
        "x=(w-text_w)/2:y=h-220"
    )
    cmd_v = [
        _ffmpeg_bin(), "-y",
        "-ss", f"{start_s:.3f}",
        "-i", str(source_path),
        "-t", f"{duration_s:.3f}",
        "-filter_complex", vf_vertical,
        # IG Reels prefers ~3-5 Mbps for 1080x1920 H.264. Our previous CRF=23
        # at low-res input was producing ~500 kbps which is technically valid
        # but reads as "low quality" to IG's transcoder.
        "-c:v", "libx264", "-preset", "fast", "-b:v", "4M", "-maxrate", "5M", "-bufsize", "8M",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30",
        "-movflags", "+faststart",
        str(vertical),
    ]
    res = subprocess.run(cmd_v, capture_output=True, text=True, timeout=300, check=False)
    if res.returncode != 0 or not vertical.is_file():
        raise EventClipError(f"ffmpeg vertical encode failed: {(res.stderr or '')[:300]}")

    # Landscape 1920x1080 — letterbox the source into landscape canvas. No
    # overlay; X/LinkedIn/FB previews look cleaner without burned text since
    # the caption renders alongside the video on those platforms.
    vf_landscape = "scale=1920:-2,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"
    cmd_l = [
        _ffmpeg_bin(), "-y",
        "-ss", f"{start_s:.3f}",
        "-i", str(source_path),
        "-t", f"{duration_s:.3f}",
        "-vf", vf_landscape,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(landscape),
    ]
    res = subprocess.run(cmd_l, capture_output=True, text=True, timeout=300, check=False)
    if res.returncode != 0 or not landscape.is_file():
        raise EventClipError(f"ffmpeg landscape encode failed: {(res.stderr or '')[:300]}")

    return ClipAssets(
        vertical_path=str(vertical),
        landscape_path=str(landscape),
        duration_s=duration_s,
    )


# ---------------------------------------------------------------------------
# End-to-end one-shot helper (used by both watcher path and cron path)
# ---------------------------------------------------------------------------
@dataclass
class FetchedClip:
    """Bundle returned by fetch_and_cut() for the orchestrator to consume."""
    upload: ChannelUpload
    source_path: str
    source_duration_s: float
    pick: SoundbitePick
    assets: ClipAssets
    cost_breakdown: dict[str, float]  # {"gemini-2.5-flash": 0.04, ...}
    cost_sources: dict[str, str]  # {"gemini-2.5-flash": "actual", ...}


def fetch_and_cut(
    upload: ChannelUpload,
    *,
    work_dir: pathlib.Path,
) -> FetchedClip:
    """Download → audio-extract → Gemini pick → ffmpeg cut.

    Raises SourceTooLong / GeminiPickerError / EventClipError. Caller maps
    those to ledger rows + (for non-skips) email alerts.
    """
    if upload.duration_s > MAX_SOURCE_MINUTES * 60:
        raise SourceTooLong(
            f"source is {upload.duration_s // 60} min, cap is {MAX_SOURCE_MINUTES} min"
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "downloading %s [%s] (%s, %ds)",
        upload.media_url or upload.url, upload.channel_url,
        upload.title[:60], upload.duration_s,
    )
    source_path = download_video(upload, work_dir)
    source_duration = probe_duration_s(source_path)
    if source_duration <= 0:
        source_duration = float(upload.duration_s)

    log.info("extracting audio for Gemini pick")
    audio_path = extract_audio(source_path, work_dir)

    log.info("calling Gemini 2.5 Flash to pick ≤90s window")
    pick = pick_soundbite(audio_path, title=upload.title)
    if pick.hook.upper() == "SKIP":
        raise EventClipError("Gemini returned SKIP — no newsworthy moment in this upload")
    start_s, end_s = clamp_window(pick.start_seconds, pick.end_seconds, source_duration)
    pick.start_seconds = start_s
    pick.end_seconds = end_s
    log.info(
        "Gemini pick: %.1fs-%.1fs (%.1fs window) cost=$%.4f hook=%r",
        start_s, end_s, end_s - start_s, pick.raw_cost_usd, pick.hook[:80],
    )

    log.info("cutting dual-AR clips with ffmpeg")
    assets = cut_dual_ar(
        source_path, start_s=start_s, end_s=end_s, hook=pick.hook, out_dir=work_dir,
    )

    return FetchedClip(
        upload=upload,
        source_path=str(source_path),
        source_duration_s=source_duration,
        pick=pick,
        assets=assets,
        cost_breakdown={GEMINI_MODEL: round(pick.raw_cost_usd, 6)},
        cost_sources={GEMINI_MODEL: pick.cost_source},
    )


# ---------------------------------------------------------------------------
# Story → source tagger (used by breaking-news watcher path)
# ---------------------------------------------------------------------------
# Cheap keyword routing: when a breaking-news headline matches an entity,
# try its first-party source for a fresh upload before falling back to
# ARTICLE. Order matters — first match wins. Keep tight per
# [[feedback_minimal_source_lists]].
#
# Values are SOURCE IDs registered in event_clip_sources, NOT URLs. Headlines
# about entities we no longer ingest (SEC, BLS, BoE, BoJ, House FinSvc) map
# to "cspan" because C-SPAN covers their newsworthy hearings; ECB has no
# first-party VOD as of 2026 so its headlines also fall to C-SPAN.
ENTITY_CHANNEL_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(powell|federal reserve|fed chair|fomc)\b", re.I),
     "federalreserve"),
    (re.compile(r"\b(imf|kristalina|georgieva)\b", re.I),
     "imf"),
    # Everything below routes to C-SPAN — it carries congressional
    # testimony + press conferences for these entities, and is the only
    # first-party path we have for them post-YouTube.
    (re.compile(r"\b(yellen|treasury|bessent)\b", re.I), "cspan"),
    (re.compile(r"\b(gensler|atkins|s\.?e\.?c\.?)\b", re.I), "cspan"),
    (re.compile(r"\b(nfp|cpi|jobs report|payrolls|bls)\b", re.I), "cspan"),
    (re.compile(r"\b(house financial|financial services committee)\b", re.I),
     "cspan"),
    (re.compile(r"\b(senate banking|banking committee)\b", re.I), "cspan"),
    (re.compile(r"\b(lagarde|ecb|european central)\b", re.I), "cspan"),
    (re.compile(r"\b(bailey|bank of england|boe)\b", re.I), "cspan"),
    (re.compile(r"\b(boj|bank of japan|ueda)\b", re.I), "cspan"),
]


def candidate_channel_for_headline(title: str) -> Optional[str]:
    """Return the most-likely source ID for `title`, or None.

    Caller passes the result to list_fresh_uploads(source_id, …) directly.
    """
    for pattern, source_id in ENTITY_CHANNEL_HINTS:
        if pattern.search(title or ""):
            return source_id
    return None
