"""
Avatar persona config — the "one-time configuration" for animated character +
environment consistency across every short_video_avatar / long_video_avatar run.

A persona is a small JSON file with:
  - character_lora_url    .safetensors URL produced by train_flux_lora
  - trigger_word          unique token included in every inference prompt
  - style_prefix          consistent visual brief prepended to every prompt
  - negative_prompt       things to suppress (off-model artifacts, distortions)
  - environment_lora_url  optional second LoRA for world/scene consistency
  - environment_scale     blend strength when both LoRAs are stacked (0.4-0.7
                          usually balances character identity vs. environment)
  - face_pose_prompt      portrait/framing instructions for the talking-head
                          still that gets fed to Hedra (front-facing, neutral
                          expression, eyes on camera, etc.)
  - voice_id              fish.audio voice id used for TTS narration

Lives at $HERMES_HOME/.hermes/avatar_persona.json (alongside other Zeus state).
The trainer script writes it; the pipeline reads it. Edit by hand any time —
no provider call needed for prompt/style tweaks, only when you re-train the
character LoRA itself.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

from .paths import zeus_data_path

log = logging.getLogger("zeus.avatar_persona")

PERSONA_FILE = zeus_data_path("avatar_persona.json")


class AvatarPersonaError(RuntimeError):
    pass


@dataclass
class AvatarPersona:
    character_lora_url: str
    trigger_word: str
    style_prefix: str = ""
    negative_prompt: str = (
        "text, watermark, logo, signature, captions, distorted face, "
        "extra fingers, extra limbs, deformed, blurry, low quality"
    )
    environment_lora_url: Optional[str] = None
    environment_scale: float = 0.6
    face_pose_prompt: str = (
        "front-facing portrait, head and shoulders, looking directly at camera, "
        "neutral closed-mouth expression, soft studio lighting, sharp focus on face"
    )
    voice_id: Optional[str] = None
    notes: str = ""
    trained_at: Optional[str] = None  # ISO timestamp; set by trainer

    def build_prompt(self, scene_description: str, *, talking_head: bool = False) -> str:
        """Compose a final inference prompt: trigger + style + scene (+ pose)."""
        parts = [self.trigger_word]
        if self.style_prefix:
            parts.append(self.style_prefix)
        parts.append(scene_description)
        if talking_head:
            parts.append(self.face_pose_prompt)
        return ". ".join(p for p in parts if p)

    def loras(self) -> list[dict]:
        """LoRA stack for fal-ai/flux-lora `loras` arg. Character is primary;
        environment (if present) blends in at a lower scale to avoid identity drift."""
        stack = [{"path": self.character_lora_url, "scale": 1.0}]
        if self.environment_lora_url:
            stack.append({"path": self.environment_lora_url, "scale": self.environment_scale})
        return stack


def load_persona() -> AvatarPersona:
    """Read the persona file. Raises AvatarPersonaError with a setup hint if missing."""
    if not PERSONA_FILE.exists():
        raise AvatarPersonaError(
            f"avatar persona not configured at {PERSONA_FILE}. "
            f"Run: python -m scripts.train_avatar_lora --images <dir> --trigger <word> "
            f"to create one, or copy and edit avatar_persona.example.json."
        )
    try:
        data = json.loads(PERSONA_FILE.read_text())
    except json.JSONDecodeError as e:
        raise AvatarPersonaError(f"avatar persona file is invalid JSON: {e}") from e
    required = ("character_lora_url", "trigger_word")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise AvatarPersonaError(
            f"avatar persona at {PERSONA_FILE} is missing: {', '.join(missing)}"
        )
    known = {f for f in AvatarPersona.__dataclass_fields__}
    return AvatarPersona(**{k: v for k, v in data.items() if k in known})


def save_persona(persona: AvatarPersona) -> None:
    """Write the persona file. Used by the trainer after a successful train run."""
    PERSONA_FILE.parent.mkdir(parents=True, exist_ok=True)
    PERSONA_FILE.write_text(json.dumps(asdict(persona), indent=2, default=str))
    log.info(f"avatar persona saved to {PERSONA_FILE}")
