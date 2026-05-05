"""
Audio mixing for Zeus video pipeline.

Three modes (defined by user 2026-05-04):
  MUSIC_ONLY         — background music matching the video's vibe, no narration
  MUSIC_AND_NARRATION — narration layered on top of music + ambient sounds
  NARRATION_PRIMARY   — narration is the dominant audio; music very low or absent

Uses fish.audio for narration, fal cassetteai for music, ffmpeg for mixing.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .content_types import AudioMode, ContentPiece
from .fal import download, generate_music
from .fish import synthesize

log = logging.getLogger("zeus.audio_mix")

VOLUME_MAP: dict[AudioMode, dict[str, float]] = {
    AudioMode.MUSIC_ONLY: {"music": 1.0, "narration": 0.0},
    AudioMode.MUSIC_AND_NARRATION: {"music": 0.25, "narration": 1.0},
    AudioMode.NARRATION_PRIMARY: {"music": 0.08, "narration": 1.0},
}


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _probe_duration(path: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            text=True, timeout=10,
        )
        return float(out.strip())
    except Exception:
        return 0.0


def _generate_music_prompt(piece: ContentPiece) -> str:
    mood_hint = piece.body[:200] if piece.body else piece.topic
    return (
        f"Cinematic background music for a video about: {piece.topic}. "
        f"Mood matches this content: {mood_hint}. "
        f"No vocals, no lyrics, instrumental only."
    )


def mix_audio_for_video(
    piece: ContentPiece,
    video_path: str,
    out_dir: str,
    narration_text: Optional[str] = None,
) -> tuple[str, dict[str, float]]:
    """
    Produce a final video file with mixed audio based on piece.audio_mode.

    Returns (final_video_path, cost_breakdown) where cost_breakdown maps
    model names to USD spent.
    """
    if not piece.audio_mode:
        return video_path, {}

    if not _ffmpeg_available():
        log.error("ffmpeg not found — returning silent video")
        return video_path, {}

    mode = piece.audio_mode
    volumes = VOLUME_MAP[mode]
    costs: dict[str, float] = {}
    out = Path(out_dir)

    video_duration = _probe_duration(video_path) or (piece.video.duration_s if piece.video else 10)

    music_path: Optional[str] = None
    narration_path: Optional[str] = None

    if volumes["music"] > 0:
        music_prompt = _generate_music_prompt(piece)
        music_url, music_cost = generate_music(music_prompt, duration_s=int(video_duration) + 2)
        music_path = download(music_url, str(out / "music.mp3"))
        costs["cassetteai/music-generator"] = music_cost

    if volumes["narration"] > 0:
        text = narration_text or piece.body or piece.topic
        narration_path, narration_cost = synthesize(text, str(out / "narration.mp3"))
        costs["fish-audio/s1"] = narration_cost

    final_path = str(out / "video_final.mp4")

    if music_path and narration_path:
        _mix_two_tracks(video_path, narration_path, music_path, final_path,
                        narration_vol=volumes["narration"], music_vol=volumes["music"],
                        duration=video_duration)
    elif music_path:
        _mix_single_track(video_path, music_path, final_path,
                          vol=volumes["music"], duration=video_duration)
    elif narration_path:
        _mix_single_track(video_path, narration_path, final_path,
                          vol=volumes["narration"], duration=video_duration)
    else:
        return video_path, costs

    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
        return final_path, costs

    log.warning("ffmpeg mix produced empty file — returning silent video")
    return video_path, costs


def _mix_single_track(
    video: str, audio: str, output: str, vol: float, duration: float
) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", video,
        "-i", audio,
        "-filter_complex",
        f"[1:a]volume={vol},atrim=0:{duration},apad,atrim=0:{duration}[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output,
    ]
    log.info(f"ffmpeg single-track mix: vol={vol}")
    subprocess.run(cmd, capture_output=True, timeout=120, check=True)


def _mix_two_tracks(
    video: str, narration: str, music: str, output: str,
    narration_vol: float, music_vol: float, duration: float,
) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", video,
        "-i", narration,
        "-i", music,
        "-filter_complex",
        (
            f"[1:a]volume={narration_vol},atrim=0:{duration},apad,atrim=0:{duration}[narr];"
            f"[2:a]volume={music_vol},atrim=0:{duration},apad,atrim=0:{duration}[mus];"
            f"[narr][mus]amix=inputs=2:duration=first[a]"
        ),
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output,
    ]
    log.info(f"ffmpeg two-track mix: narration={narration_vol} music={music_vol}")
    subprocess.run(cmd, capture_output=True, timeout=120, check=True)
