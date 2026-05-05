"""
Content type taxonomy for Zeus content pipeline.

Five types:
  Article      — 1 image + short description (<480 chars), single tweet, 4 platforms
  LongArticle  — 1 image + long description (550-900+ chars), Twitter thread, 4 platforms
  Carousel     — 3-5 slide images + long description, 4 platforms (Twitter/IG/LI/TT)
  ShortVideo   — 1080x1920, <90s, 5 platforms (+YouTube Shorts)
  LongVideo    — 1920x1080, 4 platforms (YouTube/Twitter/LinkedIn/Reddit)

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


class AudioMode(str, Enum):
    MUSIC_ONLY = "music_only"
    MUSIC_AND_NARRATION = "music_narration"
    NARRATION_PRIMARY = "narration_primary"


PLATFORMS_BY_TYPE: dict[ContentType, list[str]] = {
    ContentType.ARTICLE: ["twitter", "instagram", "linkedin", "tiktok"],
    ContentType.LONG_ARTICLE: ["twitter", "instagram", "linkedin", "tiktok"],
    ContentType.CAROUSEL: ["twitter", "instagram", "linkedin", "tiktok"],
    ContentType.SHORT_VIDEO: ["twitter", "instagram", "linkedin", "tiktok", "youtube"],
    ContentType.LONG_VIDEO: ["youtube", "twitter", "linkedin", "reddit"],
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
        return errors
