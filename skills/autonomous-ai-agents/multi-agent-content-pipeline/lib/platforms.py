"""
Platform character limits and "read more" thresholds.

User mandate (2026-05-06): every platform receives the SAME description — the
piece body, truncated to that platform's hard cap via caption_for(). No
per-platform LLM rewrites and no Twitter threading; long bodies are truncated
at a word boundary with an ellipsis.
"""
from __future__ import annotations

LIMITS: dict[str, int] = {
    "twitter": 280,
    "instagram": 2200,
    "linkedin": 3000,
    "tiktok": 2200,
    "youtube": 5000,
    "facebook": 63206,
    "reddit": 40000,
}

READ_MORE_TRIGGER: dict[str, int] = {
    "instagram": 125,
    "linkedin": 210,
    "tiktok": 80,
    "facebook": 480,
    "youtube": 100,
    "reddit": 0,  # no truncation
}


def validate_lengths(variants: dict[str, str]) -> list[str]:
    """Return list of platforms whose variants exceed hard limits."""
    errors: list[str] = []
    for platform, text in variants.items():
        if not text:
            continue
        limit = LIMITS.get(platform)
        if limit and len(text) > limit:
            errors.append(f"{platform}: {len(text)} chars > limit {limit}")
    return errors


def meets_read_more(platform: str, text: str) -> bool:
    """True if the variant is long enough to trigger 'read more' on this platform."""
    if not text:
        return False
    trigger = READ_MORE_TRIGGER.get(platform, 0)
    if trigger == 0:
        return True
    return len(text) > trigger
