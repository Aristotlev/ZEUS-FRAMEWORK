#!/usr/bin/env python3
"""
Zeus Content Pipeline — orchestrator + on-demand test runner.

Generates one of four content types and ALWAYS archives to Notion before any
publishing step, so generation spend can never be lost again.

Stack (May 2026):
    Text:  OpenRouter (gemini-2.5-flash)
    Media: fal.ai (GPT Image 2 for images, Kling 2.5 Turbo Pro for video)
    Archive: Notion (Omnifolio Content Hub -> Archive DB)
    Publish: Publer (optional, with --publish flag)

Usage:
    export $(grep -v '^#' ~/.hermes/.env | xargs)
    python3 pipeline_test.py --type article --topic "Bitcoin breaks 100K"
    python3 pipeline_test.py --type carousel --topic "..." --slides 4
    python3 pipeline_test.py --type short_video --topic "..." --duration 8
    python3 pipeline_test.py --type long_video --topic "..." --duration 10 --publish

Required env:
    OPENROUTER_API_KEY  — text generation
    FAL_KEY             — image/video generation
    NOTION_API_KEY      — archive
    PUBLER_API_KEY      — only if --publish
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

# Make sibling lib/ package importable when running this script directly.
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from lib import (  # noqa: E402
    AudioMode,
    ContentPiece,
    ContentType,
    GeneratedAsset,
    LIMITS,
    NotionArchive,
    download,
    generate_image,
    generate_video_kling,
    ledger_append,
    ledger_checkpoint,
    mix_audio_for_video,
    needs_thread,
    publish_enqueue,
    send_pipeline_summary,
    split_thread,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("zeus-pipeline")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
ORCHESTRATOR_MODEL = "google/gemini-2.5-flash"

# Publer (only used with --publish)
PUBLER_BASE = "https://app.publer.com/api/v1"
PUBLER_KEY = os.getenv("PUBLER_API_KEY", "")
PUBLER_AUTH = f"Bearer-API {PUBLER_KEY}"
PUBLER_WORKSPACE = os.getenv("PUBLER_WORKSPACE_ID", "your-workspace-id")
PUBLER_ACCOUNTS = {
    "twitter": os.getenv("PUBLER_TWITTER_ID", "69f783d1afc106b8869cf50b"),
    "instagram": os.getenv("PUBLER_INSTAGRAM_ID", "69f6511c5cf7421d7047fc4e"),
    "linkedin": os.getenv("PUBLER_LINKEDIN_ID", "69f783c63642e046435f7707"),
    "tiktok": os.getenv("PUBLER_TIKTOK_ID", "69f783de2c63a6ec70868731"),
    "youtube": os.getenv("PUBLER_YOUTUBE_ID", ""),
    "reddit": os.getenv("PUBLER_REDDIT_ID", ""),
    "facebook": os.getenv("PUBLER_FACEBOOK_ID", ""),
}

# Image dimensions per content type
IMAGE_SPECS = {
    ContentType.ARTICLE: (1024, 1024, "medium"),
    ContentType.CAROUSEL: (1024, 1024, "medium"),
    # videos don't use images directly, but a thumbnail is generated for the Notion record
}


# ---------------------------------------------------------------------------
# Niche loading — pulls content_pipeline.niche from ~/.hermes/config.yaml.
# Without this the LLM produces generic copy regardless of what the user's
# pipeline is actually about (finance/crypto/stocks/forex/geopolitics, etc.).
# ---------------------------------------------------------------------------
def _load_niche() -> list[str]:
    cfg_path = pathlib.Path(os.path.expanduser("~/.hermes/config.yaml"))
    if not cfg_path.exists():
        return []
    try:
        import yaml  # type: ignore

        with cfg_path.open() as fh:
            cfg = yaml.safe_load(fh) or {}
        niche = (cfg.get("content_pipeline") or {}).get("niche") or []
        if isinstance(niche, str):
            niche = [niche]
        return [str(n).strip() for n in niche if str(n).strip()]
    except Exception as e:  # pyyaml missing or malformed file — degrade to no-niche, log loudly
        log.warning(f"niche: could not read {cfg_path} ({e}); proceeding without niche context")
        return []


NICHE: list[str] = _load_niche()
if NICHE:
    log.info(f"niche: {', '.join(NICHE)}")
else:
    log.warning("niche: empty — content will be generic. Set content_pipeline.niche in ~/.hermes/config.yaml")


def _niche_clause() -> str:
    if not NICHE:
        return ""
    return (
        f"\nDOMAIN: this piece is for a {' / '.join(NICHE)} audience. "
        f"Use vocabulary, references, tickers, and framing native to those fields. "
        f"No generic platitudes — be specific to the domain.\n"
    )


# ---------------------------------------------------------------------------
# OpenRouter text generation
# ---------------------------------------------------------------------------
def openrouter_chat(
    prompt: str, *, max_tokens: int = 800, json_mode: bool = False
) -> tuple[str, float, str]:
    """
    Call OpenRouter chat. Returns (text, cost_usd, source) where source is
    "actual" if OpenRouter returned `usage.cost` in the response (the standard
    behavior — this is the dollar amount they billed), or "estimate" if not
    present (rare). Callers feed this into piece.add_cost(..., source=source).
    """
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    body = {
        "model": ORCHESTRATOR_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        # Force OpenRouter to include accounting fields. usage.cost is the
        # dollar amount billed for THIS call — we record it as the actual.
        "usage": {"include": True},
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    text = payload["choices"][0]["message"]["content"]
    if not text:
        raise RuntimeError("OpenRouter returned empty content")
    usage = payload.get("usage") or {}
    cost: Optional[float] = None
    source = "estimate"
    raw_cost = usage.get("cost")
    if raw_cost is not None:
        try:
            cost = float(raw_cost)
            source = "actual"
        except (TypeError, ValueError):
            cost = None
    if cost is None:
        # Cheap fallback: gemini-2.5-flash list price ~ $0.075/1M input, $0.30/1M output.
        # Rough enough that the email shows SOMETHING, but flagged as estimate so
        # the user knows to reconcile (or upgrade their OpenRouter response parsing).
        prompt_tok = float(usage.get("prompt_tokens") or 0)
        comp_tok = float(usage.get("completion_tokens") or 0)
        cost = round((prompt_tok * 0.075 + comp_tok * 0.30) / 1_000_000.0, 6)
    log.debug(f"openrouter cost={cost} source={source} usage={usage}")
    return text, cost, source


def generate_article_text(topic: str, content_type: ContentType) -> tuple[str, str, float, str]:
    """Generate (title, body, cost_usd, cost_source). Body length tuned for 'read more' on every visual platform."""
    target_chars = {
        ContentType.ARTICLE: "250-450",
        ContentType.LONG_ARTICLE: "550-900",
        ContentType.CAROUSEL: "550-900",
        ContentType.SHORT_VIDEO: "300-500",
        ContentType.LONG_VIDEO: "700-1200",
    }[content_type]
    prompt = (
        f"Write a sharp, data-driven post about: {topic}\n"
        f"{_niche_clause()}"
        f"Format:\n"
        f"- First line: a punchy 5-10 word title (no dates).\n"
        f"- Body: {target_chars} characters. The body must be long enough that Instagram, "
        f"LinkedIn, TikTok and Facebook all truncate it with a 'read more' affordance "
        f"(thresholds 125 / 210 / 80 / 480 chars respectively).\n"
        f"- Tone: Bloomberg Terminal condensed. Concrete numbers, sectors, take.\n"
        f"- No hashtags. No 'in conclusion'. No filler.\n"
    )
    raw, cost, source = openrouter_chat(prompt, max_tokens=800)
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    title = lines[0].lstrip("#").strip()
    body = "\n\n".join(lines[1:]) if len(lines) > 1 else raw
    return title, body, cost, source


def caption_for(piece: ContentPiece, platform: str) -> str:
    """Same article body for every platform, truncated to that platform's char limit.

    User mandate: no per-platform LLM rewrites. Twitter's thread case is handled
    separately in publish() via split_thread(piece.body).
    """
    limit = LIMITS.get(platform, len(piece.body))
    return piece.body[:limit]


# ---------------------------------------------------------------------------
# Media generation
# ---------------------------------------------------------------------------
def _safe_topic(topic: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in topic)[:40] or "untitled"


ARTIFACT_ROOT = pathlib.Path(os.path.expanduser("~/.hermes/zeus_artifacts"))


class _Phase:
    """
    Context manager that records wall-clock duration of a pipeline phase onto
    `piece.phase_durations_ms`. Multiple usages of the same phase name accumulate.
    Lets ledger_summary() report p50/p90 latency per phase + content type so
    optimization decisions are data-driven instead of guessed.
    """

    def __init__(self, piece: ContentPiece, name: str):
        self.piece = piece
        self.name = name

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc):
        ms = int((time.monotonic() - self._start) * 1000)
        d = self.piece.phase_durations_ms
        d[self.name] = d.get(self.name, 0) + ms
        return False


def _local_dir(piece: ContentPiece) -> pathlib.Path:
    """
    Return the stable on-disk artifact dir for `piece`. NEVER uses /tmp — bytes
    must survive process death and OS reaping so a crashed run can be recovered.
    `run()` sets piece.local_artifact_dir before any paid call; this function
    falls back to creating one lazily for direct callers.
    """
    if piece.local_artifact_dir:
        out = pathlib.Path(piece.local_artifact_dir)
        out.mkdir(parents=True, exist_ok=True)
        return out
    out = ARTIFACT_ROOT / f"{piece.run_id}_{_safe_topic(piece.topic)}"
    out.mkdir(parents=True, exist_ok=True)
    piece.local_artifact_dir = str(out)
    return out


def generate_media_for(piece: ContentPiece, slides: int = 4, video_seconds: int = 5) -> None:
    """Dispatch media generation by content type. Mutates `piece` in place."""
    out_dir = _local_dir(piece)
    if piece.content_type in (ContentType.ARTICLE, ContentType.LONG_ARTICLE):
        _gen_article_image(piece, out_dir)
    elif piece.content_type == ContentType.CAROUSEL:
        _gen_carousel_images(piece, out_dir, slides)
    elif piece.content_type == ContentType.SHORT_VIDEO:
        _gen_video(piece, out_dir, aspect="9:16", duration=min(video_seconds, 10))
    elif piece.content_type == ContentType.LONG_VIDEO:
        _gen_video(piece, out_dir, aspect="16:9", duration=min(video_seconds, 10))
    piece.status = "media_generated"


def _gen_article_image(piece: ContentPiece, out_dir: pathlib.Path) -> None:
    w, h, q = IMAGE_SPECS[ContentType.ARTICLE]
    prompt = piece.body[:1000]
    url, cost = generate_image(prompt, width=w, height=h, quality=q, run_id=piece.run_id)
    local = download(url, str(out_dir / "image_1.png"))
    piece.images.append(
        GeneratedAsset(url=url, kind="image", width=w, height=h, model="gpt-image-2", cost_usd=cost, local_path=local)
    )
    # fal's standard response has no cost field for openai/gpt-image-2 — flagged
    # as estimate so scripts/fal_reconcile.py can reconcile against fal billing.
    piece.add_cost("gpt-image-2", cost, kind="image", source="estimate")
    ledger_checkpoint(piece, "article_image_generated")


def _gen_carousel_images(piece: ContentPiece, out_dir: pathlib.Path, slides: int) -> None:
    """
    Generate the N carousel slides in parallel. fal's queue handles concurrent
    jobs fine — sequentially this loop was the dominant carousel-pipeline cost
    (~60s × N). With ThreadPoolExecutor it collapses to ~max-of-N (one slowest
    slide). On exception in one slide, surviving slides still land on `piece`
    and a checkpoint row is written before re-raising — so artifact-first
    recovery still works.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    slides = max(3, min(5, slides))
    slide_prompts = _carousel_slide_prompts(piece, slides)
    w, h, q = IMAGE_SPECS[ContentType.CAROUSEL]

    def _gen_one(idx: int, prompt: str) -> tuple[int, GeneratedAsset, float]:
        url, cost = generate_image(prompt, width=w, height=h, quality=q, run_id=piece.run_id)
        local = download(url, str(out_dir / f"slide_{idx + 1}.png"))
        asset = GeneratedAsset(
            url=url, kind="image", width=w, height=h,
            model="gpt-image-2", cost_usd=cost, local_path=local,
        )
        return idx, asset, cost

    # Slot the slides into a fixed-size list so insertion order matches slide
    # order regardless of completion order. Critical: slide 1 should be the
    # hook visual, slide N the closer.
    results: list[Optional[GeneratedAsset]] = [None] * slides
    first_error: Optional[BaseException] = None

    with ThreadPoolExecutor(max_workers=slides) as pool:
        futures = {pool.submit(_gen_one, i, sp): i for i, sp in enumerate(slide_prompts)}
        for fut in as_completed(futures):
            try:
                idx, asset, cost = fut.result()
            except BaseException as e:
                if first_error is None:
                    first_error = e
                log.error(f"  carousel slide {futures[fut] + 1} failed: {e}")
                continue
            results[idx] = asset
            piece.add_cost("gpt-image-2", cost, kind="image", source="estimate")
            ledger_checkpoint(piece, f"carousel_slide_{idx + 1}_generated")

    # Append in order; skip empty slots from failed slides.
    for asset in results:
        if asset is not None:
            piece.images.append(asset)

    if first_error is not None:
        raise first_error


def _carousel_slide_prompts(piece: ContentPiece, slides: int) -> list[str]:
    prompt = (
        f"You are designing a {slides}-slide social carousel based on this article. "
        f"Output ONLY a JSON object {{\"slides\": [\"prompt1\", \"prompt2\", ...]}} with "
        f"exactly {slides} image-generation prompts. Each prompt is a vivid visual description "
        f"(no text overlays, no captions) of one slide. Slide 1 is a strong hook visual; the rest "
        f"depict key data points or beats from the article in order.\n\n"
        f"ARTICLE TITLE: {piece.title}\n\nARTICLE BODY:\n{piece.body}"
    )
    raw, cost, source = openrouter_chat(prompt, max_tokens=900, json_mode=True)
    piece.add_cost(ORCHESTRATOR_MODEL, cost, kind="text", source=source)
    try:
        data = json.loads(raw)
        out = [str(s) for s in data.get("slides", [])]
        if len(out) >= slides:
            return out[:slides]
    except json.JSONDecodeError:
        pass
    log.warning("slide prompt JSON parse failed; falling back to article-body prompts")
    return [piece.body[:800] for _ in range(slides)]


def _gen_video(piece: ContentPiece, out_dir: pathlib.Path, aspect: str, duration: int) -> None:
    prompt = piece.body[:800]
    url, cost = generate_video_kling(prompt, aspect_ratio=aspect, duration_s=duration, run_id=piece.run_id)
    local = download(url, str(out_dir / "video.mp4"))
    width, height = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    piece.video = GeneratedAsset(
        url=url,
        kind="video",
        width=width,
        height=height,
        duration_s=duration,
        model="kling-v2.5-turbo-pro",
        cost_usd=cost,
        local_path=local,
    )
    piece.add_cost("kling-v2.5-turbo-pro", cost, kind="video", source="estimate")
    ledger_checkpoint(piece, "video_generated")

    if piece.audio_mode:
        final_path, audio_costs = mix_audio_for_video(
            piece, local, str(out_dir), narration_text=piece.body,
        )
        if final_path != local:
            piece.video.local_path = final_path
        for model, model_cost in audio_costs.items():
            # fish.audio bills per character — char count IS the actual billing
            # primitive, so it's "actual". Music is fal-side, still "estimate".
            src = "actual" if model.startswith("fish-audio") else "estimate"
            piece.add_cost(model, model_cost, kind="audio", source=src)
        ledger_checkpoint(piece, "audio_mixed")


# ---------------------------------------------------------------------------
# Publer (optional --publish)
# ---------------------------------------------------------------------------
def _publer_headers(json_body: bool = True) -> dict:
    h = {
        "Authorization": PUBLER_AUTH,
        "Publer-Workspace-Id": PUBLER_WORKSPACE,
        "Accept": "application/json",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _publer_upload(local_path: str, mime: str) -> str:
    with open(local_path, "rb") as fh:
        r = requests.post(
            f"{PUBLER_BASE}/media",
            headers=_publer_headers(json_body=False),
            files={"file": (os.path.basename(local_path), fh, mime)},
            timeout=120,
        )
    if r.status_code != 200:
        raise RuntimeError(f"Publer media upload failed {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def _publer_schedule(provider: str, account_id: str, post_type: str, text: str, media_ids: list[str]) -> str:
    # Publer interprets timezone-less ISO timestamps as UTC. Use UTC explicitly.
    when = (datetime.now(timezone.utc) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    payload = {
        "bulk": {
            "state": "scheduled",
            "posts": [
                {
                    "networks": {
                        provider: {
                            "type": post_type,
                            "text": text,
                            "media": [{"id": mid} for mid in media_ids],
                        }
                    },
                    "accounts": [{"id": account_id, "scheduled_at": when}],
                }
            ],
        }
    }
    r = requests.post(
        f"{PUBLER_BASE}/posts/schedule", headers=_publer_headers(), json=payload, timeout=20
    )
    if r.status_code != 200:
        raise RuntimeError(f"Publer schedule failed {r.status_code}: {r.text[:300]}")
    return r.json()["job_id"]


def _publer_schedule_thread(
    account_id: str, tweets: list[str], media_ids: list[str],
) -> str:
    """Post a Twitter thread via Publer. Media attaches to the first tweet only."""
    # Publer interprets timezone-less ISO timestamps as UTC. Use UTC explicitly.
    when = (datetime.now(timezone.utc) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    thread_posts = []
    for i, tweet_text in enumerate(tweets):
        post: dict = {
            "networks": {
                "twitter": {
                    "type": "photo" if (i == 0 and media_ids) else "status",
                    "text": tweet_text,
                }
            },
            "accounts": [{"id": account_id, "scheduled_at": when}],
        }
        if i == 0 and media_ids:
            post["networks"]["twitter"]["media"] = [{"id": mid} for mid in media_ids]
        thread_posts.append(post)
    payload = {
        "bulk": {
            "state": "scheduled",
            "posts": thread_posts,
            "thread": True,
        }
    }
    r = requests.post(
        f"{PUBLER_BASE}/posts/schedule", headers=_publer_headers(), json=payload, timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Publer thread schedule failed {r.status_code}: {r.text[:300]}")
    return r.json()["job_id"]


def _publer_find_post_id(account_id: str, text_snippet: str) -> str | None:
    """Find the most recent Publer post matching account_id + text snippet. Returns post id or None."""
    def _norm(s: str) -> str:
        # Normalize whitespace + case so Publer's text mangling (smart quotes,
        # tracking-param re-encoding, leading emoji) doesn't break the match.
        return " ".join((s or "").lower().split())
    try:
        r = requests.get(f"{PUBLER_BASE}/posts?limit=30", headers=_publer_headers(), timeout=15)
        if r.status_code != 200:
            log.warning(f"Publer GET /posts returned {r.status_code}: {r.text[:200]}")
            return None
        snippet_norm = _norm(text_snippet)[:40]
        posts = r.json().get("posts", [])
        # Pass 1: substring match on normalized text (handles Publer's text edits)
        for post in posts:
            if post.get("account_id") != account_id:
                continue
            if snippet_norm and snippet_norm in _norm(post.get("text") or ""):
                return post.get("id")
        # Pass 2: most recent post on this account (Publer returns newest-first)
        for post in posts:
            if post.get("account_id") == account_id:
                log.info(f"  Publer match fallback: most-recent post on account (snippet match failed)")
                return post.get("id")
    except Exception as e:
        log.warning(f"_publer_find_post_id error: {e}")
        return None
    return None


def _publer_get_post(post_id: str) -> dict | None:
    try:
        r = requests.get(f"{PUBLER_BASE}/posts/{post_id}", headers=_publer_headers(), timeout=15)
        if r.status_code == 200:
            return r.json().get("post") or r.json()
        # some Publer instances return the post directly without wrapping
    except Exception:
        return None
    return None


def _extract_post_url(post: dict) -> str | None:
    """Try every Publer URL field we've seen across accounts/platforms."""
    for k in ("post_link", "url", "permalink", "public_url", "external_url", "social_url", "live_url"):
        v = post.get(k)
        if v and isinstance(v, str) and v.startswith("http"):
            return v
    # Some Publer responses nest under 'platform_data' or similar
    nested = post.get("platform_data") or post.get("response") or {}
    if isinstance(nested, dict):
        for k in ("url", "permalink", "post_link"):
            v = nested.get(k)
            if v and isinstance(v, str) and v.startswith("http"):
                return v
    return None


def _wait_for_posts_live(piece: ContentPiece, *, max_wait_s: int = 720, poll_interval_s: int = 15) -> None:
    """
    Poll Publer until each scheduled post goes live (state='posted' with a public URL).
    Mutates piece.publer_job_ids in place: adds '<platform>_url' for each live post.
    Default 12-min window covers Publer's 2-min schedule offset + platform lag.
    """
    pending: dict[str, str] = {}  # platform -> publer_post_id
    for platform in piece.target_platforms:
        if platform not in piece.publer_job_ids:
            continue
        if str(piece.publer_job_ids[platform]).startswith("FAILED"):
            continue
        account = PUBLER_ACCOUNTS.get(platform)
        if not account:
            continue
        if platform == "twitter" and needs_thread(piece.body):
            snippet = split_thread(piece.body)[0]
        else:
            snippet = caption_for(piece, platform) or piece.body
        post_id = _publer_find_post_id(account, snippet)
        if post_id:
            pending[platform] = post_id
            log.info(f"  tracking {platform} -> publer_post_id={post_id}")
        else:
            log.warning(f"  could not resolve Publer post_id for {platform} (snippet match failed)")

    if not pending:
        log.warning("no Publer post IDs resolved; skipping live-wait")
        return

    deadline = time.time() + max_wait_s
    log.info(f"  waiting up to {max_wait_s}s for {len(pending)} posts to go live...")
    first_poll = True
    while pending and time.time() < deadline:
        time.sleep(poll_interval_s)
        for platform in list(pending.keys()):
            post = _publer_get_post(pending[platform])
            if not post:
                continue
            if first_poll:
                # One-time diagnostic so we can see what Publer actually returns
                log.info(f"  Publer post payload sample [{platform}]: keys={sorted(post.keys())[:20]}, state={post.get('state')!r}")
            state = post.get("state")
            link = _extract_post_url(post)
            if state == "posted" and link:
                piece.publer_job_ids[f"{platform}_url"] = link
                log.info(f"  ✓ {platform} live: {link}")
                pending.pop(platform)
            elif state == "posted" and not link:
                # Posted but no URL field — log keys so we can add the right field name
                log.warning(f"  {platform} state=posted but no URL field found. Post keys: {sorted(post.keys())}")
            elif state in ("error", "failed"):
                err = post.get("error") or "unknown error"
                piece.publer_job_ids[f"{platform}_url"] = f"FAILED: {err}"
                log.error(f"  ✗ {platform} failed: {err}")
                pending.pop(platform)
        first_poll = False
    if pending:
        log.warning(f"  posts still pending at deadline: {list(pending.keys())}")
        for platform, post_id in pending.items():
            piece.publer_job_ids[f"{platform}_url"] = f"PENDING: post_id={post_id}"


def publish(piece: ContentPiece, *, wait_for_live: bool = False) -> None:
    """
    Push piece to Publer for every target platform that has a configured account id.

    Default mode (`wait_for_live=False`): non-blocking — schedule all posts in
    parallel, set status="scheduled", enqueue for the watcher
    (scripts/publish_watcher.py) to confirm permalinks asynchronously, return.
    Cuts ~5 min off every run because we no longer poll Publer for live URLs
    inside this process.

    Legacy mode (`wait_for_live=True`): keep the old behavior — schedule, then
    poll up to 6 min for live permalinks, set status from actual outcomes.
    Useful for manual debugging; default behavior is non-blocking.
    """
    if not PUBLER_KEY:
        raise RuntimeError("PUBLER_API_KEY not set; cannot --publish")

    # 1) Upload media once
    media_ids: list[str] = []
    if piece.video and piece.video.local_path:
        media_ids = [_publer_upload(piece.video.local_path, "video/mp4")]
    else:
        for img in piece.images:
            if img.local_path:
                media_ids.append(_publer_upload(img.local_path, "image/png"))

    # 2) Schedule per platform — in parallel. Each platform is an independent
    # Publer API call; sequential adds ~5-10s, parallel collapses to ~one round-trip.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _schedule_one(platform: str) -> tuple[str, str]:
        """Returns (platform, job_id_or_FAILED_marker). Never raises."""
        account = PUBLER_ACCOUNTS.get(platform)
        if not account:
            log.warning(f"no PUBLER_{platform.upper()}_ID configured -- skipping {platform}")
            return platform, ""
        if platform == "twitter" and needs_thread(piece.body):
            tweets = split_thread(piece.body)
            try:
                jid = _publer_schedule_thread(account, tweets, media_ids)
                log.info(f"  -> twitter thread ({len(tweets)} tweets), job_id={jid}")
                return platform, jid
            except Exception as e:
                log.error(f"  !! twitter thread failed: {e}")
                return platform, f"FAILED: {e}"
        text = caption_for(piece, platform)
        if not text:
            log.warning(f"empty body for {platform} -- skipping")
            return platform, ""
        ptype = ("reel" if platform == "instagram" else "video") if piece.video else "photo"
        try:
            jid = _publer_schedule(platform, account, ptype, text, media_ids)
            log.info(f"  -> {platform} scheduled, job_id={jid}")
            return platform, jid
        except Exception as e:
            log.error(f"  !! {platform} failed: {e}")
            return platform, f"FAILED: {e}"

    with ThreadPoolExecutor(max_workers=max(1, len(piece.target_platforms))) as pool:
        for fut in as_completed([pool.submit(_schedule_one, p) for p in piece.target_platforms]):
            platform, jid = fut.result()
            if jid:
                piece.publer_job_ids[platform] = jid

    piece.posted_at = datetime.now(timezone.utc)

    attempted = [p for p in piece.target_platforms if p in piece.publer_job_ids]
    if not attempted:
        piece.status = "failed"
        log.info(f"  publish outcome: status=failed (no platforms accepted)")
        return

    # Default: hand the run off to the watcher. We've already done the only
    # latency-bound work (schedule API calls); polling for permalinks is what
    # used to take 2-6 min and now happens out-of-process.
    if not wait_for_live:
        piece.status = "scheduled"
        publish_enqueue(piece, max_wait_s=720)
        log.info(
            f"  publish outcome: status=scheduled, {len(attempted)} platforms enqueued for watcher "
            f"(run scripts/publish_watcher.py to resolve permalinks)"
        )
        return

    # Legacy / debugging path: keep the run alive until permalinks resolve.
    _wait_for_posts_live(piece, max_wait_s=360, poll_interval_s=15)
    confirmed: list[str] = []
    failed: list[str] = []
    for platform in attempted:
        if str(piece.publer_job_ids.get(platform, "")).startswith("FAILED"):
            failed.append(platform)
            continue
        url = str(piece.publer_job_ids.get(f"{platform}_url", ""))
        if url and not url.startswith("FAILED") and not url.startswith("pending"):
            confirmed.append(platform)
        else:
            failed.append(platform)
    if confirmed and not failed:
        piece.status = "posted"
    elif confirmed:
        piece.status = "partial"
    else:
        piece.status = "failed"
    log.info(
        f"  publish outcome: status={piece.status} "
        f"confirmed={confirmed or '[]'} failed={failed or '[]'}"
    )




# ---------------------------------------------------------------------------
# Orchestrator entry point
# ---------------------------------------------------------------------------
def run(
    content_type: ContentType,
    topic: str,
    *,
    slides: int,
    duration: int,
    do_publish: bool,
    audio_mode: AudioMode | None = None,
    wait_for_live: bool = False,
) -> ContentPiece:
    log.info("=" * 60)
    log.info(f"  Zeus pipeline -- {content_type.value}: {topic}")
    if audio_mode:
        log.info(f"  audio mode: {audio_mode.value}")
    log.info("=" * 60)

    # Build a stub piece up front so we can time text-gen onto it.
    piece = ContentPiece(content_type=content_type, title="", body="", topic=topic, audio_mode=audio_mode)
    with _Phase(piece, "text_gen"):
        title, body, text_cost, text_source = generate_article_text(topic, content_type)
    piece.title = title
    piece.body = body
    piece.add_cost(ORCHESTRATOR_MODEL, text_cost, kind="text", source=text_source)
    log.info(
        f"  text -> title='{title}' body={len(body)}c run_id={piece.run_id} "
        f"cost=${text_cost:.6f} ({text_source}) "
        f"took={piece.phase_durations_ms.get('text_gen', 0)}ms"
    )

    # Stable artifact dir — set BEFORE any paid call so a crash leaves recoverable
    # bytes on disk (NOT /tmp). orphan_sweep.py keys off this path.
    artifact_dir = ARTIFACT_ROOT / f"{piece.run_id}_{_safe_topic(piece.topic)}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    piece.local_artifact_dir = str(artifact_dir)
    log.info(f"  artifacts -> {piece.local_artifact_dir}")

    archive = NotionArchive()

    # Archive EARLY (text + run_id + artifact dir). If the pipeline later crashes
    # mid-media, the Notion row already exists pointing at the artifact dir, and
    # update_assets() below will patch in whatever bytes we did capture.
    with _Phase(piece, "notion_archive_early"):
        try:
            archive.archive(piece)
            log.info(f"  archived (pre-media) -> {piece.notion_page_id}")
        except Exception as e:
            log.error(f"  early Notion archive failed (proceeding so spend lands in ledger): {e}")

    media_error: Exception | None = None
    with _Phase(piece, "media_gen"):
        try:
            generate_media_for(piece, slides=slides, video_seconds=duration)
            log.info(
                f"  media -> images={len(piece.images)} video={'yes' if piece.video else 'no'} cost=${piece.total_cost:.3f} "
                f"took={piece.phase_durations_ms.get('media_gen', 0)}ms"
            )
            errors = piece.validate()
            if errors:
                log.warning(f"validation issues: {errors}")
        except Exception as e:
            media_error = e
            piece.status = "media_partial" if (piece.images or piece.video) else "failed"
            log.error(f"  media generation crashed (status={piece.status}): {e}")

    # Patch Notion with whatever assets we captured — success OR partial. If the
    # early archive failed, retry it now so the row at least exists.
    with _Phase(piece, "notion_assets"):
        try:
            if not piece.notion_page_id:
                archive.archive(piece)
                log.info(f"  archived (recovery) -> {piece.notion_page_id}")
            archive.update_assets(piece)
            log.info(f"  notion assets patched ({len(piece.images)} images, video={'yes' if piece.video else 'no'})")
        except Exception as e:
            log.error(f"  Notion update_assets failed: {e}")

    publish_error: Exception | None = None
    if do_publish and media_error is None:
        with _Phase(piece, "publish"):
            try:
                publish(piece, wait_for_live=wait_for_live)
                archive.update_status(piece)
                log.info(f"  published -> jobs={piece.publer_job_ids} took={piece.phase_durations_ms.get('publish', 0)}ms")
            except Exception as e:
                publish_error = e
                log.error(f"  publish failed: {e}")
    elif do_publish:
        log.warning("  skipping publish — media did not complete cleanly")
    else:
        log.info("  skip publish (use --publish to post)")

    # Always finalize: ledger row + email, regardless of upstream failures. The
    # ledger row supersedes any checkpoint rows for this run_id, and the email
    # surfaces the leaked-spend warning if status is failed/media_partial.
    try:
        ledger_append(piece)
    except Exception as e:
        log.error(f"  ledger_append failed: {e}")
    try:
        backend = send_pipeline_summary(piece)
        log.info(f"  notified -> backend={backend}")
    except Exception as e:
        log.error(f"  email failed: {e}")

    log.info(
        f"DONE — total cost ${piece.total_cost:.4f}, models {piece.models_used}, status={piece.status}"
    )

    if media_error is not None:
        raise media_error
    if publish_error is not None:
        raise publish_error
    return piece


def main() -> int:
    p = argparse.ArgumentParser(description="Zeus content pipeline test runner")
    p.add_argument("--type", required=True, choices=[t.value for t in ContentType])
    p.add_argument("--topic", required=True, help="Topic/headline for the content")
    p.add_argument("--slides", type=int, default=4, help="Slides for carousels (3-5)")
    p.add_argument("--duration", type=int, default=5, help="Seconds for video (5-10 per call)")
    p.add_argument(
        "--audio-mode",
        choices=[m.value for m in AudioMode],
        default=None,
        help="Audio mode for videos: music_only, music_narration, narration_primary",
    )
    p.add_argument("--publish", action="store_true", help="Also post to Publer (default: archive only)")
    p.add_argument(
        "--wait-for-live", action="store_true",
        help="Block until Publer confirms permalinks (~6 min). Default is non-blocking — "
             "scripts/publish_watcher.py resolves them out-of-process.",
    )
    args = p.parse_args()

    required = ["OPENROUTER_API_KEY", "FAL_KEY", "NOTION_API_KEY"]
    if args.publish:
        required.append("PUBLER_API_KEY")
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing env: {', '.join(missing)}")
        return 2

    audio_mode = AudioMode(args.audio_mode) if args.audio_mode else None
    if audio_mode and args.type not in ("short_video", "long_video"):
        log.warning(f"--audio-mode only applies to video types, ignoring for {args.type}")
        audio_mode = None

    try:
        run(
            ContentType(args.type),
            args.topic,
            slides=args.slides,
            duration=args.duration,
            do_publish=args.publish,
            audio_mode=audio_mode,
            wait_for_live=args.wait_for_live,
        )
        return 0
    except Exception as e:  # surface clean failure rather than dumping a traceback into the user's terminal
        log.exception(f"pipeline failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
