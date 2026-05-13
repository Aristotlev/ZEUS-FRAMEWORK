"""
Content type taxonomy for Zeus content pipeline.

Seven types:
  Article          — 1 image + short description (<480 chars), single tweet, 4 platforms
  LongArticle      — 1 image + long description (550-900+ chars), Twitter thread, 4 platforms
  Carousel         — 3-5 slide images + long description, 4 platforms (Twitter/IG/LI/TT)
  ShortVideo       — 1080x1920, <90s, 5 platforms (+YouTube Shorts)
  LongVideo        — 1920x1080, 4 platforms (YouTube/Twitter/LinkedIn/Reddit)
  ShortVideoAvatar — 1080x1920, <90s, FLUX-LoRA character + Hedra (talking) or Kling i2v (scene)
  LongVideoAvatar  — 1920x1080, FLUX-LoRA character + Hedra (talking) or Kling i2v (scene)

Avatar pipeline (added 2026-05-09):
  Character + environment consistency comes from a one-time-config persona at
  $HERMES_HOME/.hermes/avatar_persona.json (FLUX-LoRA URL + trigger word +
  style prefix). Run scripts/train_avatar_lora.py once per character to
  populate it. After that, every avatar run anchors its first frame on the
  trained LoRA — Hedra lip-syncs talking-head shots, Kling i2v animates
  in-scene b-roll. Pass --avatar-mode talking|scene to pick the path.

ContentPiece is the single dataclass that flows through fal -> Publer -> Notion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

# Unified-caption mandate: every platform receives the same caption (article
# body), truncated to its own char limit. No per-platform LLM rewrites.


class ContentType(str, Enum):
    ARTICLE = "article"
    LONG_ARTICLE = "long_article"
    CAROUSEL = "carousel"
    SHORT_VIDEO = "short_video"
    LONG_VIDEO = "long_video"
    # Scaffold-only — orchestrator NotImplementedError until provider picked.
    SHORT_VIDEO_AVATAR = "short_video_avatar"
    LONG_VIDEO_AVATAR = "long_video_avatar"


class AudioMode(str, Enum):
    MUSIC_ONLY = "music_only"
    MUSIC_AND_NARRATION = "music_narration"
    NARRATION_PRIMARY = "narration_primary"


PLATFORMS_BY_TYPE: dict[ContentType, list[str]] = {
    # tiktok is video-only: dropped from articles 2026-05-10 (OpenAPI ~10–12
    # posts/24h cap), then removed from carousels 2026-05-11. TikTok stays on
    # SHORT_VIDEO + SHORT_VIDEO_AVATAR only — image content goes elsewhere.
    # "substack" is a virtual platform — it doesn't fan out through Publer.
    # publish() handles it inline (ARTICLE → Substack Note, LONG_ARTICLE →
    # Substack Post) via lib/substack.py. If SUBSTACK_CONNECT_SID is unset
    # the inline handler skips with the same "not configured" semantics
    # Publer platforms use, so the run still finalises cleanly.
    # ARTICLE (short-form) drops instagram: it's text-only, IG needs a media
    # attachment, so the slot always fails. LONG_ARTICLE keeps IG because it
    # ships with a cover image. Removed from ARTICLE only on 2026-05-13.
    ContentType.ARTICLE: ["twitter", "linkedin", "facebook", "substack"],
    ContentType.LONG_ARTICLE: ["twitter", "instagram", "linkedin", "facebook", "substack"],
    # reddit is wired here for FUTURE Publer integration. If PUBLER_REDDIT_ID
    # is unset, `_schedule_one` skips it with a "no PUBLER_*_ID configured"
    # warning and never sends a request. When the account gets connected
    # later, no code change is needed — set the env var and it starts posting.
    ContentType.CAROUSEL: ["twitter", "instagram", "linkedin", "facebook", "reddit"],
    ContentType.SHORT_VIDEO: ["twitter", "instagram", "linkedin", "tiktok", "youtube", "facebook"],
    ContentType.LONG_VIDEO: ["youtube", "twitter", "linkedin", "reddit"],
    # Avatar types share platform targets with their non-avatar siblings.
    # Same dims, same publish flow once the generator lands.
    ContentType.SHORT_VIDEO_AVATAR: ["twitter", "instagram", "linkedin", "tiktok", "youtube", "facebook"],
    ContentType.LONG_VIDEO_AVATAR: ["youtube", "twitter", "linkedin", "reddit"],
}


@dataclass
class GeneratedAsset:
    url: str
    kind: Literal["image", "video", "audio"]
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None
    model: str = ""
    cost_usd: float = 0.0
    local_path: Optional[str] = None  # set after download() — protects against fal URL expiry


@dataclass
class ContentPiece:
    content_type: ContentType
    title: str
    body: str
    topic: str

    audio_mode: Optional[AudioMode] = None

    images: list[GeneratedAsset] = field(default_factory=list)
    video: Optional[GeneratedAsset] = None
    audio: Optional[GeneratedAsset] = None

    created_at: datetime = field(default_factory=datetime.utcnow)
    posted_at: Optional[datetime] = None
    publer_job_ids: dict[str, str] = field(default_factory=dict)
    notion_page_id: Optional[str] = None
    # Separate from notion_page_id (the archive row): this is the page id of
    # the per-publish row in the "Content Pipeline" DB — one row per run with
    # multi-select Platforms, Post URLs, etc. publish_watcher patches it as
    # permalinks resolve.
    notion_pipeline_page_id: Optional[str] = None
    # Stable id for the lifetime of this run. Lets the cost ledger correlate
    # checkpoint rows (written after each fal generation) with the final row,
    # so a run that crashed mid-pipeline still has its leaked spend on disk.
    run_id: str = field(default_factory=lambda: uuid4().hex[:12])
    # Stable on-disk dir holding every downloaded asset for this run. Set by
    # the orchestrator before any paid generation so a crashed run still has
    # recoverable bytes (NOT /tmp — survives OS reaping and process death).
    local_artifact_dir: Optional[str] = None
    # Per-phase wall-clock duration in milliseconds, populated by the orchestrator's
    # phase context manager. Lets ledger_summary() compute p50/p90 latency per
    # content type so optimization work is data-driven instead of guessed.
    phase_durations_ms: dict[str, int] = field(default_factory=dict)
    status: str = "draft"  # draft | scheduled | media_partial | media_generated | posted | partial | failed | checkpoint:<phase>

    cost_breakdown: dict[str, float] = field(default_factory=dict)
    # Tags every cost_breakdown key with provenance: "actual" if the provider
    # returned the dollar amount (OpenRouter usage.cost, fish per-char which IS
    # the billing primitive), "estimate" if pulled from a local price table
    # (fal image/video — fal's standard response has no cost). Lets the email
    # show accuracy %, and the reconciliation scripts know which rows still
    # need a provider-side cross-check.
    cost_sources: dict[str, str] = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        return round(sum(self.cost_breakdown.values()), 4)

    @property
    def actual_cost(self) -> float:
        return round(
            sum(v for k, v in self.cost_breakdown.items() if self.cost_sources.get(k) == "actual"),
            4,
        )

    @property
    def estimated_cost(self) -> float:
        return round(
            sum(v for k, v in self.cost_breakdown.items() if self.cost_sources.get(k) != "actual"),
            4,
        )

    @property
    def models_used(self) -> list[str]:
        return [k.split(":", 1)[1] for k in self.cost_breakdown if ":" in k]

    @property
    def target_platforms(self) -> list[str]:
        return PLATFORMS_BY_TYPE[self.content_type]

    def add_cost(self, model: str, usd: float, kind: str = "media", source: str = "estimate") -> None:
        """
        Add `usd` to the cost ledger under key `<kind>:<model>`. `source` must be
        either "actual" (provider returned this dollar amount) or "estimate"
        (computed from a local price table). If a key already has source="actual"
        and a later add tries to demote it to "estimate", the actual wins —
        accuracy never regresses within a run.
        """
        key = f"{kind}:{model}"
        self.cost_breakdown[key] = self.cost_breakdown.get(key, 0.0) + usd
        existing = self.cost_sources.get(key)
        if existing == "actual" and source != "actual":
            return
        self.cost_sources[key] = source

    def validate(self) -> list[str]:
        errors: list[str] = []
        ct = self.content_type
        if ct in (ContentType.ARTICLE, ContentType.LONG_ARTICLE):
            if len(self.images) != 1:
                errors.append(f"{ct.value} requires exactly 1 image, got {len(self.images)}")
            if self.video:
                errors.append(f"{ct.value} must not have a video asset")
        elif ct == ContentType.CAROUSEL:
            if not (3 <= len(self.images) <= 5):
                errors.append(f"Carousel requires 3-5 images, got {len(self.images)}")
            if self.video:
                errors.append("Carousel must not have a video asset")
        elif ct == ContentType.SHORT_VIDEO:
            if not self.video:
                errors.append("Short video requires a video asset")
            else:
                if self.video.duration_s is not None and self.video.duration_s >= 90:
                    errors.append(f"Short video must be <90s, got {self.video.duration_s}s")
                if self.video.width and self.video.height:
                    if (self.video.width, self.video.height) != (1080, 1920):
                        errors.append(
                            f"Short video must be 1080x1920, got {self.video.width}x{self.video.height}"
                        )
        elif ct == ContentType.LONG_VIDEO:
            if not self.video:
                errors.append("Long video requires a video asset")
            elif self.video.width and self.video.height:
                if (self.video.width, self.video.height) != (1920, 1080):
                    errors.append(
                        f"Long video must be 1920x1080, got {self.video.width}x{self.video.height}"
                    )
        elif ct == ContentType.SHORT_VIDEO_AVATAR:
            if not self.video:
                errors.append("Short video avatar requires a video asset")
            else:
                if self.video.duration_s is not None and self.video.duration_s >= 90:
                    errors.append(f"Short video avatar must be <90s, got {self.video.duration_s}s")
                if self.video.width and self.video.height:
                    if (self.video.width, self.video.height) != (1080, 1920):
                        errors.append(
                            f"Short video avatar must be 1080x1920, got {self.video.width}x{self.video.height}"
                        )
        elif ct == ContentType.LONG_VIDEO_AVATAR:
            if not self.video:
                errors.append("Long video avatar requires a video asset")
            elif self.video.width and self.video.height:
                if (self.video.width, self.video.height) != (1920, 1080):
                    errors.append(
                        f"Long video avatar must be 1920x1080, got {self.video.width}x{self.video.height}"
                    )
        return errors
