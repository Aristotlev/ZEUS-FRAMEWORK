"""Smoke tests for the content-pipeline content_types module."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = ROOT / "skills" / "autonomous-ai-agents" / "multi-agent-content-pipeline" / "lib"
sys.path.insert(0, str(LIB.parent))

from lib.content_types import ContentType, PLATFORMS_BY_TYPE  # noqa: E402


def test_content_type_enum_values():
    assert ContentType.ARTICLE.value == "article"
    assert ContentType.LONG_ARTICLE.value == "long_article"
    assert ContentType.CAROUSEL.value == "carousel"
    assert ContentType.SHORT_VIDEO.value == "short_video"
    assert ContentType.LONG_VIDEO.value == "long_video"


def test_every_content_type_has_platforms():
    for ct in ContentType:
        platforms = PLATFORMS_BY_TYPE[ct]
        assert isinstance(platforms, list)
        assert platforms, f"{ct} has no platforms"
        assert all(isinstance(p, str) and p for p in platforms)


def test_long_video_targets_youtube_first():
    assert PLATFORMS_BY_TYPE[ContentType.LONG_VIDEO][0] == "youtube"


def test_short_video_targets_mobile_native_platforms():
    short = set(PLATFORMS_BY_TYPE[ContentType.SHORT_VIDEO])
    assert {"twitter", "instagram", "tiktok", "youtube"} <= short
