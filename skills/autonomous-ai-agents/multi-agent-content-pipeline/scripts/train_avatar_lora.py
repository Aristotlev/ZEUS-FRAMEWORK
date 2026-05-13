#!/usr/bin/env python3
"""
One-shot FLUX-LoRA trainer for the Zeus avatar persona.

This is the "one-time configuration" step. You run it ONCE per character (and
optionally once more for an environment/style LoRA). After that, every
short_video_avatar / long_video_avatar run reuses the saved persona file —
no provider call needed for prompt or style tweaks.

Usage:
    # Train a character LoRA from a directory of reference images
    python -m scripts.train_avatar_lora \
        --images ~/avatar-refs/character/ \
        --trigger zeusavatar \
        --style-prefix "stylized 3D cartoon character, clean linework, vibrant palette" \
        --voice-id <fish-audio-voice-id>

    # Train an environment/style LoRA AFTER the character (additive)
    python -m scripts.train_avatar_lora \
        --images ~/avatar-refs/world/ \
        --trigger zeusworld \
        --is-style \
        --update-existing

Inputs:
    15-30 reference images (JPG/PNG/WEBP) of the SAME subject in varied
    poses, angles, lighting. Crop tightly to the subject. More images is
    NOT always better — quality and consistency beat quantity.

Output:
    Writes/updates $HERMES_HOME/.hermes/avatar_persona.json with the
    resulting LoRA URL, trigger word, and prompt config. The pipeline
    reads this file on every avatar run.

Cost: ~$2 per training run (charged by fal-ai/flux-lora-fast-training).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `lib` importable when invoked as `python -m scripts.train_avatar_lora`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import (  # noqa: E402
    AVATAR_PERSONA_FILE,
    AvatarPersona,
    AvatarPersonaError,
    fal_upload_local_file,
    load_avatar_persona,
    save_avatar_persona,
    train_flux_lora,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("zeus.train_avatar_lora")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _collect_images(images_arg: str) -> list[Path]:
    p = Path(images_arg).expanduser()
    if p.is_file():
        return [p]
    if not p.is_dir():
        raise SystemExit(f"--images path not found: {p}")
    files = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS)
    if not files:
        raise SystemExit(f"no JPG/PNG/WEBP images in {p}")
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True, help="Directory of reference images, or a single file")
    ap.add_argument("--trigger", required=True, help="Unique trigger word (token included in every prompt)")
    ap.add_argument("--steps", type=int, default=1000, help="Training steps (default 1000)")
    ap.add_argument("--is-style", action="store_true", help="Train an environment/aesthetic LoRA, not a character LoRA")
    ap.add_argument("--style-prefix", default="", help="Visual brief prepended to every prompt")
    ap.add_argument("--negative-prompt", default=None, help="Override default negative prompt")
    ap.add_argument("--voice-id", default=None, help="fish.audio voice id for talking-head TTS")
    ap.add_argument(
        "--update-existing",
        action="store_true",
        help="Merge into existing persona instead of overwriting (use after a 2nd training, e.g. environment LoRA)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Validate args + count images without training")
    args = ap.parse_args()

    if not os.getenv("FAL_KEY"):
        log.error("FAL_KEY not set. Add to ~/.hermes/.env")
        return 2

    image_files = _collect_images(args.images)
    log.info(f"found {len(image_files)} image(s) under {args.images}")
    if len(image_files) < 4:
        log.error("need at least 4 reference images for a usable LoRA")
        return 2
    if len(image_files) < 12:
        log.warning("fewer than 12 images — identity may drift; aim for 15-30")

    if args.dry_run:
        log.info("dry-run OK — re-run without --dry-run to train")
        return 0

    # Upload each local image to fal so the trainer can fetch them.
    log.info("uploading reference images to fal CDN...")
    image_urls: list[str] = []
    for f in image_files:
        url = fal_upload_local_file(str(f))
        image_urls.append(url)
    log.info(f"uploaded {len(image_urls)} image(s)")

    log.info(
        f"starting LoRA training: trigger={args.trigger!r} steps={args.steps} "
        f"is_style={args.is_style}"
    )
    lora_url, cost = train_flux_lora(
        image_urls=image_urls,
        trigger_word=args.trigger,
        steps=args.steps,
        create_masks=not args.is_style,
        is_style=args.is_style,
    )
    log.info(f"training complete — LoRA URL: {lora_url}")
    log.info(f"reported cost: ${cost:.2f}")

    now = datetime.now(timezone.utc).isoformat()

    if args.update_existing:
        try:
            persona = load_avatar_persona()
        except AvatarPersonaError:
            log.error(
                "--update-existing was passed but no persona exists yet. "
                "Train the character LoRA first (without --is-style and without --update-existing)."
            )
            return 2
        if args.is_style:
            persona.environment_lora_url = lora_url
            log.info("updated environment_lora_url on existing persona")
        else:
            persona.character_lora_url = lora_url
            persona.trigger_word = args.trigger
            log.info("updated character_lora_url + trigger_word on existing persona")
        if args.style_prefix:
            persona.style_prefix = args.style_prefix
        if args.negative_prompt:
            persona.negative_prompt = args.negative_prompt
        if args.voice_id:
            persona.voice_id = args.voice_id
        persona.trained_at = now
    else:
        if args.is_style:
            log.error(
                "--is-style alone creates an orphan environment LoRA. "
                "Either train a character LoRA first, or pass --update-existing."
            )
            return 2
        persona = AvatarPersona(
            character_lora_url=lora_url,
            trigger_word=args.trigger,
            style_prefix=args.style_prefix,
            voice_id=args.voice_id,
            trained_at=now,
        )
        if args.negative_prompt:
            persona.negative_prompt = args.negative_prompt

    save_avatar_persona(persona)
    log.info(f"avatar persona written to {AVATAR_PERSONA_FILE}")
    log.info("you can now run: pipeline_test.py --type short_video_avatar --topic <topic>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
