"""
Platform character limits, "read more" thresholds, and Twitter thread splitting.

User mandate: every platform receives the SAME source body, no per-platform LLM
rewrites. On Twitter, bodies longer than the per-tweet limit are mechanically
chunked into a text thread via split_thread; multi-image carousel posts on
Twitter never thread — they ship as a single native gallery.

Twitter tier knobs (env-overridable so we can flip without a code change):
  ZEUS_TWITTER_TIER=premium   (preset: limit=25000, trigger=25000, budget=24990
                               — uses Premium long-post cap so LONG_ARTICLE
                               ships as a single long-form tweet, not a thread)
  ZEUS_TWITTER_TIER=free      (preset, default: limit=280, trigger=280, budget=270)
  ZEUS_TWITTER_LIMIT / ZEUS_TWITTER_THREAD_TRIGGER / ZEUS_TWITTER_TWEET_BUDGET
    override individual values; useful for ad-hoc tuning.

Budget reserves room for the " i/N" suffix (≤6 chars for N<100, ≤8 for N<1000),
so every chunk + suffix lands safely under the per-tweet limit.
"""
from __future__ import annotations

import os
import re

_TIER = os.getenv("ZEUS_TWITTER_TIER", "free").strip().lower()
_TIER_PRESETS: dict[str, tuple[int, int, int]] = {
    # tier: (per-tweet limit, thread trigger, per-chunk budget)
    "free": (280, 280, 270),
    # Premium long-post cap (25k chars). Trigger == limit so LONG_ARTICLE
    # (~550-900c), CAROUSEL captions, SHORT_VIDEO copy etc. all ship as a
    # single tweet — no threading on Premium.
    "premium": (25000, 25000, 24990),
}
_preset_limit, _preset_trigger, _preset_budget = _TIER_PRESETS.get(
    _TIER, _TIER_PRESETS["free"],
)

TWITTER_LIMIT = int(os.getenv("ZEUS_TWITTER_LIMIT", str(_preset_limit)))
TWITTER_THREAD_TRIGGER = int(os.getenv("ZEUS_TWITTER_THREAD_TRIGGER", str(_preset_trigger)))
TWITTER_TWEET_BUDGET = int(os.getenv("ZEUS_TWITTER_TWEET_BUDGET", str(_preset_budget)))

LIMITS: dict[str, int] = {
    "twitter": TWITTER_LIMIT,
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


def needs_thread(text: str) -> bool:
    return len(text) > TWITTER_THREAD_TRIGGER


def split_thread(text: str, per_tweet_limit: int = TWITTER_TWEET_BUDGET) -> list[str]:
    """
    Split text into a Twitter thread at the most natural boundary that fits.

    Boundary preference: paragraph (\\n\\n) > sentence (.!?…) > word > hard
    char-split (last-resort, only for tokens longer than the limit, e.g. URLs).
    Each chunk is ≤per_tweet_limit; " i/N" is suffixed when N>1, and the
    caller-side budget reserves room for that suffix.
    """
    text = text.strip()
    if len(text) <= per_tweet_limit:
        return [text]

    # 1. Build atomic units that each fit in one tweet. Try paragraph-sized
    #    chunks first (preserves logical structure on long_articles); if a
    #    paragraph is too big, drop to sentences; if a sentence is too big,
    #    word-wrap as a last resort.
    units: list[str] = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= per_tweet_limit:
            units.append(para)
            continue
        for sent in re.split(r"(?<=[.!?…])\s+", para):
            sent = sent.strip()
            if not sent:
                continue
            if len(sent) <= per_tweet_limit:
                units.append(sent)
            else:
                units.extend(_word_wrap(sent, per_tweet_limit))

    # 2. Greedy-pack units into tweets, joining with a single space. We never
    #    split a unit across two tweets here — that already happened in step 1
    #    when needed.
    tweets: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
            continue
        candidate = f"{current} {unit}"
        if len(candidate) <= per_tweet_limit:
            current = candidate
        else:
            tweets.append(current)
            current = unit
    if current:
        tweets.append(current)

    n = len(tweets)
    if n > 1:
        tweets = [f"{t} {i + 1}/{n}" for i, t in enumerate(tweets)]
    return tweets


def _word_wrap(text: str, limit: int) -> list[str]:
    out: list[str] = []
    current = ""
    for word in text.split():
        # A single token longer than the tweet limit (long URL, run-on
        # hashtag, no-space pasted block) used to be appended whole, then
        # Twitter would truncate it mid-word on display. Hard-split such
        # tokens at limit boundaries before they hit the chunker.
        if len(word) > limit:
            if current:
                out.append(current)
                current = ""
            for i in range(0, len(word), limit):
                piece = word[i : i + limit]
                if len(piece) == limit:
                    out.append(piece)
                else:
                    current = piece
            continue
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                out.append(current)
            current = word
    if current:
        out.append(current)
    return out


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
