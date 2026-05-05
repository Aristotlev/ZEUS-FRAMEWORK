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

import json as _json
import logging
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

log = logging.getLogger("zeus.fal")

FAL_CALL_LOG = Path(os.path.expanduser("~/.hermes/zeus_fal_calls.jsonl"))


def _log_fal_call(
    *,
    run_id: Optional[str],
    model: str,
    request_id: Optional[str],
    declared_cost_usd: float,
    cost_source: str,
    inputs: dict,
    response_excerpt: dict,
) -> None:
    """
    Append one row per paid fal call. This is the reconciliation audit trail —
    `scripts/fal_reconcile.py` reads it to cross-check against fal billing.
    Never raise from here; logging failures must not kill a paid generation.
    """
    try:
        FAL_CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.utcnow().isoformat(),
            "run_id": run_id,
            "model": model,
            "request_id": request_id,
            "declared_cost_usd": round(float(declared_cost_usd or 0), 6),
            "cost_source": cost_source,
            "inputs": inputs,
            "response_excerpt": response_excerpt,
        }
        with FAL_CALL_LOG.open("a") as fh:
            fh.write(_json.dumps(row, default=str) + "\n")
    except Exception as e:  # pragma: no cover
        log.warning(f"fal call-log write failed: {e}")


def _extract_fal_cost(payload: Any) -> tuple[Optional[float], str]:
    """
    Try to find a billed cost in a fal response. Some fal models include
    `metrics.cost`, `cost`, or `pricing.charge` — prefer any of those over
    the local price table. Returns (cost, source) where source is
    "actual" if found, "" if not.
    """
    if not isinstance(payload, dict):
        return None, ""
    for k in ("cost", "billed_cost", "charge"):
        v = payload.get(k)
        if isinstance(v, (int, float)):
            return float(v), "actual"
    metrics = payload.get("metrics") or payload.get("pricing") or {}
    if isinstance(metrics, dict):
        for k in ("cost", "billed_cost", "charge", "amount_usd"):
            v = metrics.get(k)
            if isinstance(v, (int, float)):
                return float(v), "actual"
    return None, ""

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
    run_id: Optional[str] = None,
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

    # Prefer fal-reported cost if present; fall back to the local price table.
    actual_cost, src = _extract_fal_cost(result)
    if actual_cost is not None:
        cost, cost_source = actual_cost, "actual"
    else:
        cost = GPT_IMAGE_2_PRICE.get(((width, height), quality), 0.0)
        cost_source = "estimate"
    _log_fal_call(
        run_id=run_id,
        model="gpt-image-2",
        request_id=request_id,
        declared_cost_usd=cost,
        cost_source=cost_source,
        inputs={"width": width, "height": height, "quality": quality, "output_format": output_format,
                "prompt_excerpt": prompt[:200]},
        response_excerpt={k: result.get(k) for k in ("seed", "timings", "metrics", "pricing", "cost") if k in result},
    )
    return images[0]["url"], cost


def generate_video_kling(
    prompt: str,
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16",
    duration_s: int = 5,
    negative_prompt: Optional[str] = None,
    run_id: Optional[str] = None,
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
    actual_cost, _ = _extract_fal_cost(result if isinstance(result, dict) else {})
    if actual_cost is not None:
        cost, cost_source = actual_cost, "actual"
    else:
        cost = kling_cost(duration_s)
        cost_source = "estimate"
    _log_fal_call(
        run_id=run_id,
        model="kling-v2.5-turbo-pro",
        request_id=(result.get("request_id") if isinstance(result, dict) else None),
        declared_cost_usd=cost,
        cost_source=cost_source,
        inputs={"aspect_ratio": aspect_ratio, "duration_s": duration_s, "prompt_excerpt": prompt[:200]},
        response_excerpt={k: result.get(k) for k in ("metrics", "pricing", "cost") if isinstance(result, dict) and k in result},
    )
    return url, cost


def generate_music(
    prompt: str,
    duration_s: int = 30,
    model_slug: str = "fal-ai/cassetteai/music-generator",
    run_id: Optional[str] = None,
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
    actual_cost, _ = _extract_fal_cost(result if isinstance(result, dict) else {})
    if actual_cost is not None:
        cost, cost_source = actual_cost, "actual"
    else:
        cost, cost_source = 0.05, "estimate"  # conservative; refine via fal_reconcile
    _log_fal_call(
        run_id=run_id,
        model=model_slug,
        request_id=(result.get("request_id") if isinstance(result, dict) else None),
        declared_cost_usd=cost,
        cost_source=cost_source,
        inputs={"duration_s": duration_s, "prompt_excerpt": prompt[:200]},
        response_excerpt={k: result.get(k) for k in ("metrics", "pricing", "cost") if isinstance(result, dict) and k in result},
    )
    return url, cost


def download(url: str, dest_path: str) -> str:
    """Stream a fal output URL to disk. Returns dest_path. Use to protect against fal URL expiry."""
    log.info(f"download {url[:60]}... -> {dest_path}")
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    urllib.request.urlretrieve(url, dest_path)
    return dest_path
