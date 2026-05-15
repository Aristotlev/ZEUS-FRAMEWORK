"""Event-clip pipeline: pull real-event video from rights-safe channels, let
Gemini pick the most newsworthy ≤90s soundbite, cut dual-AR clips for
short-form (9:16) and long-form (16:9) social distribution.

Source allowlist is GOVERNMENT / OFFICIAL ONLY (Fed, SEC, Treasury, BLS,
House FinServ, Senate Banking, C-SPAN, IMF, ECB, BOE, BOJ, corporate IR).
Rights-safe to re-upload — no strike risk. CNBC/Bloomberg/Reuters/WSJ are
NEVER added to the default list; their clips will get the social account
struck within weeks. See feedback_no_unsolicited_advice in memory.

Pipeline shape:
    yt-dlp (480p mp4)
        -> ffmpeg extract 32kbps mono mp3 audio
        -> base64 -> OpenRouter Gemini 2.5 Flash multimodal call
        -> JSON {start_seconds, end_seconds, hook}
        -> ffmpeg cut at timestamps, encode 1080x1920 + 1920x1080 outputs

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
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

log = logging.getLogger("zeus.event_clip")

# ---------------------------------------------------------------------------
# Source channel allowlist. Gov/official only — see module docstring.
# Channel IDs are YouTube IDs (the @handle resolves to one of these via
# yt-dlp). Comma-separated list in EVENT_CLIP_CHANNELS env overrides default.
# ---------------------------------------------------------------------------
DEFAULT_CHANNELS: list[str] = [
    # Federal Reserve
    "https://www.youtube.com/@federalreserve",
    # SEC
    "https://www.youtube.com/@SECViews",
    # US Treasury
    "https://www.youtube.com/@USTreasury",
    # Bureau of Labor Statistics (NFP, CPI release days)
    "https://www.youtube.com/@BLSgov",
    # House Financial Services Committee
    "https://www.youtube.com/@HouseFinancialCmte",
    # Senate Banking Committee
    "https://www.youtube.com/@SenateBanking",
    # C-SPAN (committee hearings + speeches)
    "https://www.youtube.com/@cspan",
    # IMF
    "https://www.youtube.com/@imf",
    # European Central Bank
    "https://www.youtube.com/@europeancentralbank",
    # Bank of England
    "https://www.youtube.com/@bankofengland",
    # Bank of Japan
    "https://www.youtube.com/@BankofJapan",
]

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
    """One fresh upload from a watched channel."""
    video_id: str
    title: str
    url: str
    upload_date: str  # ISO 8601
    duration_s: int
    channel_url: str


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
# yt-dlp wrappers — discovery + download
# ---------------------------------------------------------------------------
def _ytdlp_bin() -> str:
    # Prefer the venv-installed binary (pip install yt-dlp). Fallback to PATH.
    venv = pathlib.Path("/opt/hermes/.venv/bin/yt-dlp")
    if venv.is_file():
        return str(venv)
    if shutil.which("yt-dlp"):
        return "yt-dlp"
    raise EventClipError("yt-dlp not installed — add to Dockerfile pip layer")


def _ytdlp_common_args() -> list[str]:
    """Returns the cookies + proxy args every yt-dlp invocation should carry.

    YouTube hard-blocks datacenter IPs (Hetzner, AWS, GCP) with "Sign in to
    confirm you're not a bot." The pipeline's cron-driven path requires one
    of two bypasses:

      EVENT_CLIP_YOUTUBE_COOKIES_PATH — points at a cookies.txt exported
        from a logged-in browser. yt-dlp uses these for every request.
        Cookies expire in ~weeks; the user re-exports when they do. THIS IS
        THE SUPPORTED PRODUCTION PATH.

      EVENT_CLIP_PROXY_URL (or fallback ZEUS_PICKER_PROXY_URL) — routes
        yt-dlp through a proxy. Webshare datacenter proxies DO NOT bypass
        YouTube; only residential proxies do. Kept as a fallback in case the
        user upgrades to a residential tier.
    """
    args: list[str] = []
    cookies_path = os.getenv("EVENT_CLIP_YOUTUBE_COOKIES_PATH", "").strip()
    if cookies_path and pathlib.Path(cookies_path).is_file():
        args.extend(["--cookies", cookies_path])
    proxy = (
        os.getenv("EVENT_CLIP_PROXY_URL", "").strip()
        or os.getenv("ZEUS_PICKER_PROXY_URL", "").strip()
    )
    # Only attach the proxy when the caller explicitly opted in via
    # EVENT_CLIP_PROXY_URL — the picker proxy is datacenter-tier and would
    # ALSO hit YouTube's bot wall, just from a different IP. Forwarding it
    # silently would mask cookie failures with a proxy-related error.
    if proxy and os.getenv("EVENT_CLIP_PROXY_URL", "").strip():
        args.extend(["--proxy", proxy])
    return args


def _ffmpeg_bin() -> str:
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    raise EventClipError("ffmpeg not installed — add to Dockerfile apt layer")


def list_fresh_uploads(channel_url: str, *, hours_back: int) -> list[ChannelUpload]:
    """Return uploads from `channel_url` posted within `hours_back` hours.

    Uses `yt-dlp --dump-json --playlist-end 10` to inspect only the channel's
    most recent 10 entries (avoids paging through years of backlog). Filters
    locally on upload_date.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    cmd = [
        _ytdlp_bin(),
        *_ytdlp_common_args(),
        "--dump-json",
        "--playlist-end", "10",
        "--no-warnings",
        "--ignore-errors",
        # Skip livestreams — we want recorded speeches/hearings, not WIP.
        "--match-filter", "!is_live & live_status != is_upcoming",
        channel_url,
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=90, check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("yt-dlp list timeout for %s", channel_url)
        return []
    if out.returncode != 0 and not out.stdout:
        log.warning("yt-dlp list failed for %s: %s", channel_url, (out.stderr or "")[:200])
        return []
    uploads: list[ChannelUpload] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        upload_date = obj.get("upload_date") or ""  # YYYYMMDD
        if not upload_date or len(upload_date) != 8:
            continue
        try:
            up_dt = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if up_dt < cutoff:
            continue
        duration_s = int(obj.get("duration") or 0)
        if duration_s <= 0:
            continue
        uploads.append(ChannelUpload(
            video_id=str(obj.get("id") or ""),
            title=str(obj.get("title") or "").strip(),
            url=str(obj.get("webpage_url") or obj.get("original_url") or ""),
            upload_date=up_dt.isoformat(),
            duration_s=duration_s,
            channel_url=channel_url,
        ))
    return uploads


def download_video(url: str, out_dir: pathlib.Path) -> pathlib.Path:
    """Download mp4 at ≤480p (good enough for Gemini + ffmpeg re-encode).

    Returns the local mp4 path. Raises EventClipError on failure.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "source.%(ext)s")
    cmd = [
        _ytdlp_bin(),
        *_ytdlp_common_args(),
        "-f", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best[height<=480]/worst[height>=240]",
        "--merge-output-format", "mp4",
        "-o", out_template,
        "--no-warnings",
        "--no-playlist",
        url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if res.returncode != 0:
        raise EventClipError(f"yt-dlp download failed: {(res.stderr or res.stdout)[:300]}")
    mp4 = out_dir / "source.mp4"
    if not mp4.is_file():
        # yt-dlp sometimes lands as .mkv when the merge couldn't keep mp4.
        for p in out_dir.glob("source.*"):
            if p.suffix.lower() in (".mp4", ".mkv", ".webm"):
                return p
        raise EventClipError("yt-dlp succeeded but no output file found")
    return mp4


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
    log.info("downloading %s (%s, %ds)", upload.url, upload.title[:60], upload.duration_s)
    source_path = download_video(upload.url, work_dir)
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
# Story → channel tagger (used by breaking-news watcher path)
# ---------------------------------------------------------------------------
# Cheap keyword routing: when a breaking-news headline matches an entity,
# try its official channel for a fresh upload before falling back to ARTICLE.
# Order matters — first match wins. Keep tight per minimal-source-lists rule.
ENTITY_CHANNEL_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(powell|federal reserve|fed chair|fomc)\b", re.I),
     "https://www.youtube.com/@federalreserve"),
    (re.compile(r"\b(yellen|treasury|bessent)\b", re.I),
     "https://www.youtube.com/@USTreasury"),
    (re.compile(r"\b(gensler|atkins|s\.?e\.?c\.?)\b", re.I),
     "https://www.youtube.com/@SECViews"),
    (re.compile(r"\b(nfp|cpi|jobs report|payrolls|bls)\b", re.I),
     "https://www.youtube.com/@BLSgov"),
    (re.compile(r"\b(house financial|financial services committee)\b", re.I),
     "https://www.youtube.com/@HouseFinancialCmte"),
    (re.compile(r"\b(senate banking|banking committee)\b", re.I),
     "https://www.youtube.com/@SenateBanking"),
    (re.compile(r"\b(lagarde|ecb|european central)\b", re.I),
     "https://www.youtube.com/@europeancentralbank"),
    (re.compile(r"\b(bailey|bank of england|boe)\b", re.I),
     "https://www.youtube.com/@bankofengland"),
    (re.compile(r"\b(boj|bank of japan|ueda)\b", re.I),
     "https://www.youtube.com/@BankofJapan"),
    (re.compile(r"\b(imf|kristalina|georgieva)\b", re.I),
     "https://www.youtube.com/@imf"),
]


def candidate_channel_for_headline(title: str) -> Optional[str]:
    """Return the most-likely official channel URL for `title`, or None."""
    for pattern, channel in ENTITY_CHANNEL_HINTS:
        if pattern.search(title or ""):
            return channel
    return None
