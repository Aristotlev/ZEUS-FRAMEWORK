"""
fal.ai client wrapper for Zeus content pipeline.

Replaces the old Replicate stack. All media generation funnels through here so cost
tracking and model choice are centralized. Set FAL_KEY in ~/.hermes/.env.

Models (May 2026):
  Image: fal-ai/openai/gpt-image-2
  Video: fal-ai/kling-video/v2.5-turbo/pro/text-to-video
  Music: fal-ai/cassetteai/music-generator (swappable via model_slug arg)

Install: pip install fal-client
"""
from __future__ import annotations

import logging
import os
import urllib.request
from typing import Literal, Optional

log = logging.getLogger("zeus.fal")

try:
    import fal_client  # type: ignore
except ImportError:
    fal_client = None


GPT_IMAGE_2_PRICE: dict[tuple[tuple[int, int], str], float] = {
    ((1024, 1024), "low"): 0.006,
    ((1024, 1024), "medium"): 0.053,
    ((1024, 1024), "high"): 0.211,
    ((1024, 768), "low"): 0.005,
    ((1024, 768), "medium"): 0.037,
    ((1024, 768), "high"): 0.145,
    ((1024, 1536), "low"): 0.005,
    ((1024, 1536), "medium"): 0.042,
    ((1024, 1536), "high"): 0.165,
    ((1920, 1080), "low"): 0.005,
    ((1920, 1080), "medium"): 0.040,
    ((1920, 1080), "high"): 0.158,
}

KLING_BASE_PRICE = 0.35
KLING_BASE_SECONDS = 5
KLING_PER_SECOND_AFTER = 0.07


def kling_cost(duration_s: float) -> float:
    if duration_s <= KLING_BASE_SECONDS:
        return KLING_BASE_PRICE
    return KLING_BASE_PRICE + (duration_s - KLING_BASE_SECONDS) * KLING_PER_SECOND_AFTER


class FalError(RuntimeError):
    pass


def _client():
    if fal_client is None:
        raise FalError("fal-client not installed. Run: pip install fal-client")
    if not os.getenv("FAL_KEY"):
        raise FalError("FAL_KEY env var not set. Add to ~/.hermes/.env")
    return fal_client


def generate_image(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    quality: Literal["low", "medium", "high"] = "medium",
    output_format: Literal["png", "jpeg", "webp"] = "png",
) -> tuple[str, float]:
    """Returns (image_url, cost_usd). Uses REST API directly (fal SDK has a polling bug with openai/ models)."""
    import time as _time
    import requests as _req

    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        raise FalError("FAL_KEY env var not set. Add to ~/.hermes/.env")
    headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
    payload = {
        "prompt": prompt,
        "image_size": {"width": width, "height": height},
        "quality": quality,
        "num_images": 1,
        "output_format": output_format,
    }
    log.info(f"fal image: {width}x{height} {quality} -- {prompt[:60]}")
    # fal's OpenAI-namespaced models have intermittent "Exhausted balance" lock states
    # immediately after a previous job finishes billing. Retry with backoff.
    r = None
    for attempt in range(6):
        r = _req.post("https://queue.fal.run/openai/gpt-image-2", headers=headers, json=payload, timeout=30)
        if r.status_code == 200:
            break
        if r.status_code == 403 and "locked" in r.text.lower():
            wait = 5 * (attempt + 1)
            log.warning(f"  fal lock (attempt {attempt + 1}/6) — retrying in {wait}s")
            _time.sleep(wait)
            continue
        break
    if r is None or r.status_code != 200:
        raise FalError(f"fal submit failed {r.status_code if r else '???'}: {(r.text[:200] if r else 'no response')}")
    job = r.json()
    request_id = job["request_id"]
    status_url = job["status_url"]
    response_url = job["response_url"]
    log.info(f"  queued {request_id}, polling...")

    for _ in range(120):
        _time.sleep(3)
        sr = _req.get(status_url, headers=headers, timeout=15)
        status = sr.json().get("status")
        if status == "COMPLETED":
            break
        if status == "FAILED":
            raise FalError(f"GPT Image 2 generation failed: {sr.json()}")
    else:
        raise FalError(f"GPT Image 2 timed out after 360s (request {request_id})")

    rr = _req.get(response_url, headers=headers, timeout=30)
    rr.raise_for_status()
    result = rr.json()
    images = result.get("images", [])
    if not images:
        raise FalError(f"GPT Image 2 returned no images: {result}")
    cost = GPT_IMAGE_2_PRICE.get(((width, height), quality), 0.0)
    return images[0]["url"], cost


def generate_video_kling(
    prompt: str,
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16",
    duration_s: int = 5,
    negative_prompt: Optional[str] = None,
) -> tuple[str, float]:
    """
    Single-call Kling 2.5 Turbo Pro generation. Returns (video_url, cost_usd).

    Kling caps a single call at ~10s. For longer outputs, call multiple times and stitch
    with ffmpeg (see scripts/pipeline_test.py for the chaining pattern).
    """
    client = _client()
    arguments: dict = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "duration": str(duration_s),
    }
    if negative_prompt:
        arguments["negative_prompt"] = negative_prompt
    log.info(f"fal video (Kling Turbo): {aspect_ratio} {duration_s}s -- {prompt[:60]}")
    result = client.subscribe(
        "fal-ai/kling-video/v2.5-turbo/pro/text-to-video", arguments=arguments, client_timeout=600
    )
    video = result.get("video") if isinstance(result, dict) else None
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        raise FalError(f"Kling returned no video: {result}")
    return url, kling_cost(duration_s)


def generate_music(
    prompt: str,
    duration_s: int = 30,
    model_slug: str = "fal-ai/cassetteai/music-generator",
) -> tuple[str, float]:
    """
    Background music generation. Default model is cassetteai/music-generator.
    Swap by passing a different model_slug. Returns (audio_url, cost_usd).
    """
    client = _client()
    log.info(f"fal music ({model_slug}): {duration_s}s -- {prompt[:60]}")
    result = client.subscribe(model_slug, arguments={"prompt": prompt, "duration": duration_s})
    audio = (
        result.get("audio_file")
        or result.get("audio")
        or result.get("output")
        if isinstance(result, dict)
        else None
    )
    url = audio.get("url") if isinstance(audio, dict) else audio
    if not url:
        raise FalError(f"Music model returned no audio: {result}")
    return url, 0.05  # conservative; refine once real billing observed


def download(url: str, dest_path: str) -> str:
    """Stream a fal output URL to disk. Returns dest_path. Use to protect against fal URL expiry."""
    log.info(f"download {url[:60]}... -> {dest_path}")
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    urllib.request.urlretrieve(url, dest_path)
    return dest_path
