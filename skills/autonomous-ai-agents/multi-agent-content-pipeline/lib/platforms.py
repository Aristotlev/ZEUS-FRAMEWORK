"""
Platform character limits, "read more" thresholds, and Twitter thread splitting.

User mandate (2026-05-06): every platform receives the SAME source body. On
Twitter, single tweets cap at 480 chars (X Premium tier); bodies longer than
480 are mechanically chunked into a text thread via split_thread (each tweet
≤470 chars, room for " n/N" suffix). No per-platform LLM rewrites. Multi-image
carousel posts on Twitter never thread — they ship as a single native gallery.
"""
from __future__ import annotations

import re

LIMITS: dict[str, int] = {
    "twitter": 480,
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

TWITTER_THREAD_TRIGGER = 480
TWITTER_TWEET_BUDGET = 470  # leave room for ' n/N' suffix on multi-tweet threads


def needs_thread(text: str) -> bool:
    return len(text) > TWITTER_THREAD_TRIGGER


def split_thread(text: str, per_tweet_limit: int = TWITTER_TWEET_BUDGET) -> list[str]:
    """
    Split text into a Twitter thread. Tries sentence boundaries first, falls
    back to word wrap for over-long sentences. Suffixes ' i/N' to each tweet
    when N > 1.
    """
    text = text.strip()
    if len(text) <= per_tweet_limit:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    tweets: list[str] = []
    current = ""
    for sent in sentences:
        if len(sent) > per_tweet_limit:
            if current:
                tweets.append(current.strip())
                current = ""
            tweets.extend(_word_wrap(sent, per_tweet_limit))
            continue
        candidate = f"{current} {sent}".strip() if current else sent
        if len(candidate) <= per_tweet_limit:
            current = candidate
        else:
            tweets.append(current.strip())
            current = sent
    if current:
        tweets.append(current.strip())
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
