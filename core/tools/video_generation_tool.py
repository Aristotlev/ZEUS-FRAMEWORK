"""Video Generation Tool — fal.ai Kling 2.5 Turbo Pro.

Supports text-to-video and image-to-video via the fal queue API with full
reliability (idempotency, retry, timeout, immediate download).

Models:
- fal-ai/kling-video/v2.5-turbo/pro/text-to-video  (default)
- fal-ai/kling-video/v2.5-turbo/pro/image-to-video  (when image_url provided)

Pricing: $0.35 first 5s + $0.07/s after.
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

TEXT_TO_VIDEO_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"
IMAGE_TO_VIDEO_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"

ASPECT_RATIOS = {
    "landscape": "16:9",
    "portrait": "9:16",
    "square": "1:1",
}
DEFAULT_ASPECT_RATIO = "landscape"
VALID_DURATIONS = ("5", "10")
DEFAULT_DURATION = "5"

# Queue timeout: if no runner picks up within 5 minutes, fail fast.
QUEUE_TIMEOUT_SECONDS = 300

_debug = DebugSession("video_tools", env_var="VIDEO_TOOLS_DEBUG")


def _get_output_dir():
    from hermes_constants import get_hermes_home
    d = get_hermes_home() / "generated" / "video"
    d.mkdir(parents=True, exist_ok=True)
    return d


def video_generate_tool(
    prompt: str,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    duration: str = DEFAULT_DURATION,
    image_url: Optional[str] = None,
    negative_prompt: Optional[str] = None,
) -> str:
    """Generate a video clip from a text prompt (or image + prompt).

    Returns JSON: {"success": bool, "video_url": str|null, "local_path": str|null, ...}
    """
    debug_data: Dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "image_url": image_url,
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

        aspect_key = (aspect_ratio or DEFAULT_ASPECT_RATIO).lower().strip()
        if aspect_key not in ASPECT_RATIOS:
            logger.warning("Invalid aspect_ratio '%s', defaulting to '%s'", aspect_ratio, DEFAULT_ASPECT_RATIO)
            aspect_key = DEFAULT_ASPECT_RATIO

        dur = str(duration).strip()
        if dur not in VALID_DURATIONS:
            logger.warning("Invalid duration '%s', defaulting to '%s'", duration, DEFAULT_DURATION)
            dur = DEFAULT_DURATION

        model = IMAGE_TO_VIDEO_MODEL if image_url else TEXT_TO_VIDEO_MODEL
        arguments: Dict[str, Any] = {
            "prompt": prompt.strip(),
            "duration": dur,
            "aspect_ratio": ASPECT_RATIOS[aspect_key],
        }
        if negative_prompt:
            arguments["negative_prompt"] = negative_prompt.strip()
        if image_url:
            arguments["image_url"] = image_url.strip()

        logger.info(
            "Generating video via %s — prompt: %s, %s, %ss",
            model.split("/")[-1], prompt[:60], ASPECT_RATIOS[aspect_key], dur,
        )

        handle = submit_fal_job(
            model, arguments, timeout_seconds=QUEUE_TIMEOUT_SECONDS,
        )
        result = wait_for_result(handle)

        gen_time = (datetime.datetime.now() - start).total_seconds()

        video = result.get("video")
        if not video or not isinstance(video, dict) or "url" not in video:
            raise ValueError("fal returned no video in response")

        video_url = video["url"]
        local_path = str(download_result(video_url, _get_output_dir()))

        logger.info("Video generated in %.1fs → %s", gen_time, local_path)

        debug_data.update(success=True, generation_time=gen_time)
        _debug.log_call("video_generate_tool", debug_data)
        _debug.save()

        return json.dumps({
            "success": True,
            "video_url": video_url,
            "local_path": local_path,
            "duration_seconds": int(dur),
            "aspect_ratio": ASPECT_RATIOS[aspect_key],
            "generation_time_seconds": round(gen_time, 1),
        }, ensure_ascii=False)

    except Exception as e:
        gen_time = (datetime.datetime.now() - start).total_seconds()
        logger.error("Video generation failed: %s", e, exc_info=True)

        debug_data.update(error=str(e), generation_time=gen_time)
        _debug.log_call("video_generate_tool", debug_data)
        _debug.save()

        return json.dumps({
            "success": False,
            "video_url": None,
            "local_path": None,
            "error": str(e),
            "error_type": type(e).__name__,
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

VIDEO_GENERATE_SCHEMA = {
    "name": "video_generate",
    "description": (
        "Generate short video clips from text prompts using fal.ai Kling 2.5 "
        "Turbo Pro. Supports text-to-video (default) and image-to-video "
        "(provide image_url). Returns a video URL and local file path. "
        "Duration: 5s or 10s. Aspect ratios: landscape (16:9), portrait "
        "(9:16), square (1:1). Pricing: ~$0.35 for 5s, ~$0.70 for 10s."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Text prompt describing the desired video. Be descriptive "
                    "about motion, camera movement, and scene."
                ),
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(ASPECT_RATIOS.keys()),
                "description": (
                    "Video orientation. 'landscape' (16:9) for YouTube/desktop, "
                    "'portrait' (9:16) for TikTok/Reels/Shorts, 'square' (1:1) for Instagram."
                ),
                "default": DEFAULT_ASPECT_RATIO,
            },
            "duration": {
                "type": "string",
                "enum": list(VALID_DURATIONS),
                "description": "Video duration in seconds. '5' (~$0.35) or '10' (~$0.70).",
                "default": DEFAULT_DURATION,
            },
            "image_url": {
                "type": "string",
                "description": (
                    "Optional image URL to animate (image-to-video mode). "
                    "When provided, the video will be based on this image."
                ),
            },
        },
        "required": ["prompt"],
    },
}


def _handle_video_generate(args, **kw):
    prompt = args.get("prompt", "")
    if not prompt:
        return tool_error("prompt is required for video generation")
    return video_generate_tool(
        prompt=prompt,
        aspect_ratio=args.get("aspect_ratio", DEFAULT_ASPECT_RATIO),
        duration=args.get("duration", DEFAULT_DURATION),
        image_url=args.get("image_url"),
    )


registry.register(
    name="video_generate",
    toolset="video_gen",
    schema=VIDEO_GENERATE_SCHEMA,
    handler=_handle_video_generate,
    check_fn=check_fal_available,
    requires_env=["FAL_KEY"],
    is_async=False,
    emoji="🎬",
)
