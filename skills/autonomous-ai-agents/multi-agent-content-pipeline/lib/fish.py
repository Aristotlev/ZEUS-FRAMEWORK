"""
fish.audio TTS client for Zeus video pipelines.

User mandate (2026-05-04): "for TTS we use fish.audio cause everything else is
unacceptable shit." Replaces edge-tts and Kokoro as the default narrator.

API: POST https://api.fish.audio/v1/tts
Auth: Bearer FISH_AUDIO_API_KEY (in ~/.hermes/.env)
Returns: binary audio stream (mp3 by default)

Pricing model: fish.audio bills per character generated; ~$15/1M chars on S1 Pro.
We track an estimate per call so the cost ledger has something concrete.
"""
from __future__ import annotations

import logging
import os
from typing import Literal, Optional

import requests

log = logging.getLogger("zeus.fish")

FISH_API = "https://api.fish.audio/v1/tts"
DEFAULT_MODEL = "s1"  # s1 = $15/1M chars; s2-pro is the premium tier

# Voice presets — populate via reference_id from fish.audio voice library.
# Set ZEUS_FISH_VOICE_DEFAULT in .env to override.
DEFAULT_REFERENCE_ID = os.getenv("ZEUS_FISH_VOICE_DEFAULT", "")

PRICE_PER_MILLION_CHARS = {
    "s1": 15.0,
    "s2-pro": 30.0,
}


class FishAudioError(RuntimeError):
    pass


def synthesize(
    text: str,
    out_path: str,
    *,
    reference_id: Optional[str] = None,
    model: Literal["s1", "s2-pro"] = DEFAULT_MODEL,
    audio_format: Literal["mp3", "wav", "opus", "pcm"] = "mp3",
    mp3_bitrate: int = 128,
    speed: float = 1.0,
    temperature: float = 0.7,
) -> tuple[str, float]:
    """
    Generate narration with fish.audio. Writes binary audio to `out_path`.
    Returns (out_path, cost_usd_estimate).
    """
    api_key = os.getenv("FISH_AUDIO_API_KEY")
    if not api_key:
        raise FishAudioError("FISH_AUDIO_API_KEY not set in ~/.hermes/.env")
    if not text.strip():
        raise FishAudioError("synthesize: empty text")

    body: dict = {
        "text": text,
        "format": audio_format,
        "mp3_bitrate": mp3_bitrate,
        "temperature": temperature,
        "prosody": {"speed": speed},
    }
    if reference_id or DEFAULT_REFERENCE_ID:
        body["reference_id"] = reference_id or DEFAULT_REFERENCE_ID

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": model,
    }
    log.info(f"fish.audio TTS ({model}): {len(text)} chars -> {out_path}")
    r = requests.post(FISH_API, headers=headers, json=body, timeout=120)
    if r.status_code != 200:
        raise FishAudioError(f"fish.audio {r.status_code}: {r.text[:300]}")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(r.content)

    cost = (len(text) / 1_000_000.0) * PRICE_PER_MILLION_CHARS.get(model, 15.0)
    return out_path, round(cost, 6)
