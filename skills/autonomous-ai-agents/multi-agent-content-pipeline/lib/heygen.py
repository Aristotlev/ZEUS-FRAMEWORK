"""
HeyGen v2 API client for Zeus avatar pipeline.

Alternative path to the FLUX-LoRA + Hedra stack: HeyGen does the full
"text-script -> talking avatar video with voice" in one call. Use when you
want a stock or pre-trained HeyGen avatar instead of a custom-trained
FLUX-LoRA character.

Env:
  HEYGEN_API_KEY      required
  HEYGEN_AVATAR_ID    required at call-time (pick from HeyGen dashboard)
  HEYGEN_VOICE_ID     required at call-time (pick from HeyGen dashboard)
  HEYGEN_AVATAR_STYLE optional, default "normal"

Pricing (2026-05, from heygen.com/api-pricing):
  Avatar III @ 1080p   $1.00/min  (default)
  Avatar IV  @ 1080p   $4.00/min
  Avatar IV  @ 4K      $5.00/min

Endpoints:
  POST /v2/video/generate          -> {data: {video_id}}
  GET  /v1/video_status.get?video_id=...
       -> {data: {status, video_url, duration, ...}}
"""
from __future__ import annotations

import json as _json
import logging
import os
import time
import urllib.request
from datetime import datetime
from typing import Literal, Optional

import requests

from .paths import zeus_data_path

log = logging.getLogger("zeus.heygen")

HEYGEN_CALL_LOG = zeus_data_path("zeus_heygen_calls.jsonl")

HEYGEN_API_BASE = "https://api.heygen.com"

PRICE_PER_MIN = {
    ("avatar_iii", "1080p"): 1.00,
    ("avatar_iv",  "1080p"): 4.00,
    ("avatar_iv",  "4k"):    5.00,
}


class HeyGenError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.getenv("HEYGEN_API_KEY")
    if not key:
        raise HeyGenError("HEYGEN_API_KEY env var not set")
    return key


def _headers() -> dict:
    return {
        "X-Api-Key": _api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def heygen_cost(duration_s: float, tier: str = "avatar_iii", resolution: str = "1080p") -> float:
    """Return estimated $ for a HeyGen render. Per-minute billing, prorated."""
    rate = PRICE_PER_MIN.get((tier, resolution))
    if rate is None:
        raise HeyGenError(f"unknown HeyGen tier/resolution: {tier}/{resolution}")
    return round((duration_s / 60.0) * rate, 4)


def _log_call(
    *,
    run_id: Optional[str],
    video_id: Optional[str],
    declared_cost_usd: float,
    cost_source: str,
    inputs: dict,
    response_excerpt: dict,
) -> None:
    try:
        HEYGEN_CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.utcnow().isoformat(),
            "run_id": run_id,
            "model": "heygen-" + inputs.get("tier", "avatar_iii"),
            "video_id": video_id,
            "declared_cost_usd": round(float(declared_cost_usd or 0), 6),
            "cost_source": cost_source,
            "inputs": inputs,
            "response_excerpt": response_excerpt,
        }
        with HEYGEN_CALL_LOG.open("a") as fh:
            fh.write(_json.dumps(row, default=str) + "\n")
    except Exception as e:  # pragma: no cover
        log.warning(f"heygen call-log write failed: {e}")


def generate_avatar_video(
    script: str,
    avatar_id: Optional[str] = None,
    voice_id: Optional[str] = None,
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16",
    avatar_style: str = "normal",
    background_color: str = "#FAFAFA",
    tier: Literal["avatar_iii", "avatar_iv"] = "avatar_iii",
    resolution: Literal["1080p", "4k"] = "1080p",
    character_type: Optional[Literal["avatar", "talking_photo"]] = None,
    poll_interval_s: int = 5,
    timeout_s: int = 900,
    run_id: Optional[str] = None,
) -> tuple[str, float, float]:
    """
    Submit a HeyGen v2 render and block until completed.

    Returns (video_url, cost_usd, duration_s).

    avatar_id / voice_id default to HEYGEN_AVATAR_ID / HEYGEN_VOICE_ID env vars.
    character_type defaults to HEYGEN_AVATAR_TYPE env var (else "avatar").
    Use "talking_photo" when the id refers to an uploaded photo / Photo Avatar
    rather than a stock or Instant Avatar.
    """
    avatar_id = avatar_id or os.getenv("HEYGEN_AVATAR_ID")
    voice_id = voice_id or os.getenv("HEYGEN_VOICE_ID")
    character_type = character_type or os.getenv("HEYGEN_AVATAR_TYPE") or "avatar"
    if character_type not in ("avatar", "talking_photo"):
        raise HeyGenError(f"unknown HEYGEN_AVATAR_TYPE={character_type!r} (expected 'avatar' or 'talking_photo')")
    if not avatar_id:
        raise HeyGenError("HEYGEN_AVATAR_ID env var not set (pick one from the HeyGen dashboard)")
    if not voice_id:
        raise HeyGenError("HEYGEN_VOICE_ID env var not set (pick one from the HeyGen dashboard)")

    if aspect_ratio == "9:16":
        width, height = 1080, 1920
    elif aspect_ratio == "16:9":
        width, height = 1920, 1080
    else:
        width, height = 1080, 1080

    if character_type == "talking_photo":
        character = {
            "type": "talking_photo",
            "talking_photo_id": avatar_id,
        }
    else:
        character = {
            "type": "avatar",
            "avatar_id": avatar_id,
            "avatar_style": avatar_style,
        }

    payload = {
        "video_inputs": [
            {
                "character": character,
                "voice": {
                    "type": "text",
                    "input_text": script,
                    "voice_id": voice_id,
                },
                "background": {
                    "type": "color",
                    "value": background_color,
                },
            }
        ],
        "dimension": {"width": width, "height": height},
    }

    log.info(f"heygen submit: {width}x{height} avatar={avatar_id} voice={voice_id} -- {script[:60]}")
    r = requests.post(
        f"{HEYGEN_API_BASE}/v2/video/generate",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    if r.status_code != 200:
        raise HeyGenError(f"heygen submit {r.status_code}: {r.text[:300]}")
    body = r.json()
    if body.get("error"):
        raise HeyGenError(f"heygen submit error: {body['error']}")
    video_id = (body.get("data") or {}).get("video_id")
    if not video_id:
        raise HeyGenError(f"heygen submit returned no video_id: {body}")

    log.info(f"  heygen queued {video_id}, polling...")
    deadline = time.monotonic() + timeout_s
    last_status: dict = {}
    while time.monotonic() < deadline:
        time.sleep(poll_interval_s)
        sr = requests.get(
            f"{HEYGEN_API_BASE}/v1/video_status.get",
            headers=_headers(),
            params={"video_id": video_id},
            timeout=15,
        )
        if sr.status_code != 200:
            log.warning(f"  heygen status {sr.status_code}: {sr.text[:200]}")
            continue
        sb = sr.json()
        last_status = sb.get("data") or {}
        st = last_status.get("status")
        if st == "completed":
            break
        if st == "failed":
            err = last_status.get("error") or sb.get("error") or "unknown"
            raise HeyGenError(f"heygen render failed (video_id={video_id}): {err}")
    else:
        raise HeyGenError(f"heygen render timed out after {timeout_s}s (video_id={video_id})")

    video_url = last_status.get("video_url") or last_status.get("video_url_caption")
    if not video_url:
        raise HeyGenError(f"heygen completed but no video_url: {last_status}")
    duration_s = float(last_status.get("duration") or 0.0)
    if duration_s <= 0:
        # HeyGen occasionally omits duration on the first status read; fall
        # back to a script-length heuristic (~140 wpm).
        word_count = len(script.split())
        duration_s = max(1.0, word_count / 140.0 * 60.0)

    cost = heygen_cost(duration_s, tier=tier, resolution=resolution)
    _log_call(
        run_id=run_id,
        video_id=video_id,
        declared_cost_usd=cost,
        cost_source="estimate",
        inputs={
            "tier": tier,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "character_type": character_type,
            "avatar_id": avatar_id,
            "voice_id": voice_id,
            "script_excerpt": script[:200],
        },
        response_excerpt={k: last_status.get(k) for k in ("status", "duration", "video_id") if k in last_status},
    )
    return video_url, cost, duration_s


def download(url: str, dest_path: str) -> str:
    """Stream a HeyGen output URL to disk. Returns dest_path."""
    log.info(f"heygen download {url[:60]}... -> {dest_path}")
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    urllib.request.urlretrieve(url, dest_path)
    return dest_path
