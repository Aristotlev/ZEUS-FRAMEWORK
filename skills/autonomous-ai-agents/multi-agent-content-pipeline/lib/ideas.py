"""
Idea extraction — turn a raw user-supplied source (URL, YouTube link, plain
text) into a structured ExtractedIdea the pipeline can compile into content.

Zero new dependencies: HTML parsing uses only the stdlib's html.parser, and
YouTube metadata comes from the public oEmbed endpoint (no API key needed).
The LLM-driven distill step is wired in scripts/ingest_ideas.py via the
existing openrouter_chat() helper — this module is intentionally I/O-only so
it stays cheap to test.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal, Optional
from urllib.parse import urlparse, parse_qs

import requests

log = logging.getLogger("zeus.ideas")

SourceType = Literal["text", "url", "youtube"]

# Hard cap on the body excerpt we hand to the LLM. Keeps token spend bounded
# even if the user pastes a 50KB article — the distiller doesn't need the
# entire piece, just enough to grasp the angle and key beats.
EXCERPT_CHAR_LIMIT = 6000


@dataclass
class ExtractedIdea:
    source_type: SourceType
    source: str  # the original URL or text the user provided
    title: str = ""
    excerpt: str = ""  # readable body text, truncated to EXCERPT_CHAR_LIMIT
    metadata: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Source-type classification
# ---------------------------------------------------------------------------
_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "youtube-nocookie.com",
}


def classify(raw: str) -> SourceType:
    """Decide what kind of input the user dropped in.

    Heuristics, in order:
      1. Looks like a URL (http/https scheme) → URL or youtube
      2. YouTube host → youtube
      3. Anything else → text (treat as a topic/idea written in prose)
    """
    raw = (raw or "").strip()
    if not raw:
        return "text"
    parsed = urlparse(raw if "://" in raw else f"https://{raw}" if raw.startswith("www.") else raw)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        host = parsed.netloc.lower()
        if any(host.endswith(yt) for yt in _YT_HOSTS):
            return "youtube"
        return "url"
    return "text"


# ---------------------------------------------------------------------------
# HTML → readable text. Stdlib-only so we don't pull in beautifulsoup just for
# this. The strategy is not magical — we keep <article> / <main> contents if
# present, otherwise the longest contiguous run of <p> tags. Good enough for
# the average news / blog page; structurally weird sites degrade gracefully
# to "title + first 6KB of visible text".
# ---------------------------------------------------------------------------
_BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "blockquote", "pre"}
_DROP_TAGS = {"script", "style", "noscript", "svg", "header", "footer", "nav", "form", "aside"}


class _ReadableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._drop_depth = 0
        self._article_depth = 0  # >0 while inside <article>/<main>
        self._article_buf: list[str] = []
        self._all_buf: list[str] = []
        self._cur_block: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag in _DROP_TAGS:
            self._drop_depth += 1
        elif tag in ("article", "main"):
            self._article_depth += 1
        elif tag in _BLOCK_TAGS:
            self._cur_block = []

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag in _DROP_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
        elif tag in ("article", "main"):
            self._article_depth = max(0, self._article_depth - 1)
        elif tag in _BLOCK_TAGS:
            text = " ".join(t.strip() for t in self._cur_block if t.strip())
            self._cur_block = []
            if not text:
                return
            if self._article_depth > 0:
                self._article_buf.append(text)
            self._all_buf.append(text)

    def handle_data(self, data: str):
        if self._drop_depth > 0:
            return
        if self._in_title:
            self.title += data
            return
        self._cur_block.append(data)

    def best_text(self) -> str:
        """Prefer text inside <article>/<main>; fall back to all blocks."""
        if self._article_buf:
            return "\n\n".join(self._article_buf)
        return "\n\n".join(self._all_buf)


def _truncate(text: str, limit: int = EXCERPT_CHAR_LIMIT) -> str:
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > 0 else limit].rstrip() + "…"


def fetch_url(url: str, *, timeout: int = 20) -> ExtractedIdea:
    """
    Fetch a URL and extract title + readable body text. Never raises for the
    common failure modes (timeout, 4xx, malformed HTML) — returns an empty
    excerpt instead so the caller can degrade to "treat as plain-text idea"
    without losing the user's row.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; ZeusIdeasBot/1.0; +https://github.com/Aristotlev/ZEUS-FRAMEWORK)"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    out = ExtractedIdea(source_type="url", source=url)
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except Exception as e:
        log.warning(f"fetch_url: request failed for {url}: {e}")
        return out
    if r.status_code >= 400:
        log.warning(f"fetch_url: {url} returned {r.status_code}")
        return out
    if "html" not in (r.headers.get("Content-Type") or "").lower():
        log.warning(f"fetch_url: {url} is not HTML; cannot extract")
        return out

    parser = _ReadableParser()
    try:
        parser.feed(r.text)
    except Exception as e:
        log.warning(f"fetch_url: HTML parse failed for {url}: {e}")
        return out

    out.title = parser.title.strip() or _hostname(url)
    out.excerpt = _truncate(parser.best_text())
    out.metadata["final_url"] = r.url
    return out


def _hostname(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


# ---------------------------------------------------------------------------
# YouTube — oEmbed gives title + author + thumbnail without an API key. We
# don't transcribe (yt-dlp + Whisper would be a heavy dep stack); the title
# + author + URL is usually enough for the LLM distiller to produce a
# coherent piece, and the user can always enrich the row with their own
# Notes before flipping it from "New" to "Processing".
# ---------------------------------------------------------------------------
def _yt_video_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.lstrip("/").split("/")[0] or None
    if "youtube" in parsed.netloc:
        if parsed.path == "/watch":
            return (parse_qs(parsed.query).get("v") or [None])[0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2] or None
        if parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[2] or None
    return None


def fetch_youtube(url: str, *, timeout: int = 15) -> ExtractedIdea:
    out = ExtractedIdea(source_type="youtube", source=url)
    vid = _yt_video_id(url)
    if vid:
        out.metadata["video_id"] = vid
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=timeout,
        )
    except Exception as e:
        log.warning(f"fetch_youtube: oembed request failed for {url}: {e}")
        return out
    if r.status_code >= 400:
        log.warning(f"fetch_youtube: oembed returned {r.status_code} for {url}")
        return out
    try:
        data = r.json()
    except ValueError:
        log.warning(f"fetch_youtube: oembed for {url} returned non-JSON")
        return out
    out.title = data.get("title", "") or ""
    if author := data.get("author_name"):
        out.metadata["author"] = author
    if thumb := data.get("thumbnail_url"):
        out.metadata["thumbnail"] = thumb
    # We have no transcript — synthesize a tiny excerpt so the distiller has
    # something more concrete than just the title. Format chosen so the LLM
    # treats it as factual seed material.
    parts = [f"YouTube video: {out.title}"]
    if author := out.metadata.get("author"):
        parts.append(f"Channel: {author}")
    parts.append(f"URL: {url}")
    out.excerpt = "\n".join(parts)
    return out


def extract(raw: str) -> ExtractedIdea:
    """Single entry point — classify the input, then dispatch to the right fetcher."""
    raw = (raw or "").strip()
    kind = classify(raw)
    if kind == "youtube":
        return fetch_youtube(raw)
    if kind == "url":
        return fetch_url(raw)
    return ExtractedIdea(source_type="text", source=raw, title="", excerpt=raw[:EXCERPT_CHAR_LIMIT])
