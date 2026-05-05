"""Music Generation Tool — fal.ai music models.

Generates instrumental music or songs from text prompts via the fal queue API
with full reliability (idempotency, retry, timeout, immediate download).

The active model is configurable via ``music_gen.model`` in config.yaml.
Default: ``fal-ai/cassetteai/music-gen`` (CassetteAI).
"""

import datetime
import json
import logging
import os
from typing import Any, Dict, Optional

from tools.debug_helpers import DebugSession
from tools.fal_reliability import (
    check_fal_available,
    download_result,
    submit_fal_job,
    wait_for_result,
)

logger = logging.getLogger(__name__)


FAL_MUSIC_MODELS: Dict[str, Dict[str, Any]] = {
    "fal-ai/cassetteai/music-gen": {
        "display": "CassetteAI Music Gen",
        "strengths": "General-purpose music generation",
        "defaults": {},
        "prompt_key": "prompt",
        "duration_key": "duration_seconds",
        "audio_response_key": "audio",
    },
    "fal-ai/stable-audio": {
        "display": "Stable Audio",
        "strengths": "High-quality instrumentals, sound effects",
        "defaults": {},
        "prompt_key": "prompt",
        "duration_key": "seconds_total",
        "audio_response_key": "audio_file",
    },
}

DEFAULT_MODEL = "fal-ai/cassetteai/music-gen"
DEFAULT_DURATION = 30
MAX_DURATION = 300

QUEUE_TIMEOUT_SECONDS = 300

_debug = DebugSession("music_tools", env_var="MUSIC_TOOLS_DEBUG")


def _get_output_dir():
    from hermes_constants import get_hermes_home
    d = get_hermes_home() / "generated" / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_music_model() -> tuple:
    """Resolve the active music model from config.yaml or default."""
    model_id = ""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        music_cfg = cfg.get("music_gen") if isinstance(cfg, dict) else None
        if isinstance(music_cfg, dict):
            raw = music_cfg.get("model")
            if isinstance(raw, str):
                model_id = raw.strip()
    except Exception as exc:
        logger.debug("Could not load music_gen.model from config: %s", exc)

    if not model_id:
        return DEFAULT_MODEL, FAL_MUSIC_MODELS[DEFAULT_MODEL]

    if model_id not in FAL_MUSIC_MODELS:
        logger.warning(
            "Unknown music model '%s' in config; falling back to %s",
            model_id, DEFAULT_MODEL,
        )
        return DEFAULT_MODEL, FAL_MUSIC_MODELS[DEFAULT_MODEL]

    return model_id, FAL_MUSIC_MODELS[model_id]


def music_generate_tool(
    prompt: str,
    duration_seconds: int = DEFAULT_DURATION,
) -> str:
    """Generate music from a text prompt.

    Returns JSON: {"success": bool, "audio_url": str|null, "local_path": str|null, ...}
    """
    debug_data: Dict[str, Any] = {
        "prompt": prompt,
        "duration_seconds": duration_seconds,
        "success": False,
        "error": None,
        "generation_time": 0,
    }
    start = datetime.datetime.now()

    try:
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt is required and must be a non-empty string")

        if not os.getenv("FAL_KEY"):
            raise ValueError("FAL_KEY environment variable not set")

        dur = max(1, min(int(duration_seconds), MAX_DURATION))
        model_id, meta = _resolve_music_model()

        arguments: Dict[str, Any] = dict(meta.get("defaults", {}))
        arguments[meta["prompt_key"]] = prompt.strip()
        arguments[meta["duration_key"]] = dur

        logger.info(
            "Generating music via %s — prompt: %s, %ds",
            meta.get("display", model_id), prompt[:60], dur,
        )

        handle = submit_fal_job(
            model_id, arguments, timeout_seconds=QUEUE_TIMEOUT_SECONDS,
        )
        result = wait_for_result(handle)

        gen_time = (datetime.datetime.now() - start).total_seconds()

        audio_key = meta["audio_response_key"]
        audio = result.get(audio_key)
        if isinstance(audio, dict):
            audio_url = audio.get("url", "")
        elif isinstance(audio, str):
            audio_url = audio
        else:
            raise ValueError(f"fal returned no audio in response (key: {audio_key})")

        if not audio_url:
            raise ValueError("fal returned empty audio URL")

        local_path = str(download_result(audio_url, _get_output_dir()))

        logger.info("Music generated in %.1fs → %s", gen_time, local_path)

        debug_data.update(success=True, generation_time=gen_time)
        _debug.log_call("music_generate_tool", debug_data)
        _debug.save()

        return json.dumps({
            "success": True,
            "audio_url": audio_url,
            "local_path": local_path,
            "duration_seconds": dur,
            "model": model_id,
            "generation_time_seconds": round(gen_time, 1),
        }, ensure_ascii=False)

    except Exception as e:
        gen_time = (datetime.datetime.now() - start).total_seconds()
        logger.error("Music generation failed: %s", e, exc_info=True)

        debug_data.update(error=str(e), generation_time=gen_time)
        _debug.log_call("music_generate_tool", debug_data)
        _debug.save()

        return json.dumps({
            "success": False,
            "audio_url": None,
            "local_path": None,
            "error": str(e),
            "error_type": type(e).__name__,
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

MUSIC_GENERATE_SCHEMA = {
    "name": "music_generate",
    "description": (
        "Generate music or instrumentals from a text prompt using fal.ai. "
        "Returns an audio file URL and local path. Describe genre, mood, "
        "tempo, and instruments. Default duration: 30s, max: 300s."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Text prompt describing the desired music. Include genre, "
                    "mood, tempo, instruments. Example: 'upbeat electronic "
                    "dance track with synthesizers, 128 BPM'."
                ),
            },
            "duration_seconds": {
                "type": "integer",
                "description": "Duration of the generated music in seconds (1-300).",
                "default": DEFAULT_DURATION,
            },
        },
        "required": ["prompt"],
    },
}


def _handle_music_generate(args, **kw):
    prompt = args.get("prompt", "")
    if not prompt:
        return tool_error("prompt is required for music generation")
    return music_generate_tool(
        prompt=prompt,
        duration_seconds=args.get("duration_seconds", DEFAULT_DURATION),
    )


registry.register(
    name="music_generate",
    toolset="music_gen",
    schema=MUSIC_GENERATE_SCHEMA,
    handler=_handle_music_generate,
    check_fn=check_fal_available,
    requires_env=["FAL_KEY"],
    is_async=False,
    emoji="🎵",
)
