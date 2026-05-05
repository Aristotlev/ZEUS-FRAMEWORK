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
import tempfile
import time
from datetime import datetime, timedelta, timezone

import requests

# Make sibling lib/ package importable when running this script directly.
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from lib import (  # noqa: E402
    AudioMode,
    ContentPiece,
    ContentType,
    GeneratedAsset,
    NotionArchive,
    PlatformVariants,
    download,
    generate_image,
    generate_video_kling,
    ledger_append,
    ledger_checkpoint,
    mix_audio_for_video,
    needs_thread,
    send_pipeline_summary,
    split_thread,
    validate_lengths,
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
def openrouter_chat(prompt: str, *, max_tokens: int = 800, json_mode: bool = False) -> str:
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    body = {
        "model": ORCHESTRATOR_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
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
    text = r.json()["choices"][0]["message"]["content"]
    if not text:
        raise RuntimeError("OpenRouter returned empty content")
    return text


def generate_article_text(topic: str, content_type: ContentType) -> tuple[str, str]:
    """Generate (title, body). Body length is tuned to clear 'read more' on every visual platform."""
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
    raw = openrouter_chat(prompt, max_tokens=800)
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    title = lines[0].lstrip("#").strip()
    body = "\n\n".join(lines[1:]) if len(lines) > 1 else raw
    return title, body


def generate_variants(piece: ContentPiece) -> None:
    """Fill piece.variants for each target platform via a single JSON-mode LLM call."""
    targets = piece.target_platforms
    constraints = []
    if "twitter" in targets:
        if needs_thread(piece.body):
            constraints.append(
                "twitter_thread: an array of strings (each <=270 chars), splitting the article into a thread on sentence boundaries"
            )
        else:
            constraints.append("twitter: <=270 chars, punchy")
    if "instagram" in targets:
        constraints.append("instagram: 600-2000 chars, hook in first 125 chars, hashtag block at the end")
    if "linkedin" in targets:
        constraints.append("linkedin: 800-2500 chars, professional tone, hook in first 210 chars")
    if "tiktok" in targets:
        constraints.append("tiktok: 200-500 chars, hook in first 80 chars, 2-3 hashtags")
    if "youtube" in targets:
        constraints.append("youtube: 300-900 chars description, hook in first 100 chars")
    if "reddit" in targets:
        constraints.append("reddit: 800-3000 chars, no hashtags, plain markdown")
    if "facebook" in targets:
        constraints.append("facebook: 600-1500 chars, hook in first 480 chars")

    prompt = (
        f"You are formatting a piece of content for multiple platforms.\n"
        f"{_niche_clause()}"
        f"TITLE: {piece.title}\n\n"
        f"ARTICLE:\n{piece.body}\n\n"
        f"Output ONLY valid JSON with these keys (omit keys not in the constraint list):\n"
        + "\n".join(f"- {c}" for c in constraints)
        + "\n\nUse the same facts as the article. Vary phrasing per platform to avoid duplicate-content flags."
    )
    raw = openrouter_chat(prompt, max_tokens=1500, json_mode=True)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Retry once with a stricter framing. If we fall through to raw-body
        # fallback like before, Twitter ends up posting the article body cut
        # into 270-char chunks — exactly what produced the generic-tweet bug.
        log.warning("variants: model returned non-JSON; retrying once with stricter prompt")
        retry_prompt = (
            "Your previous response was not valid JSON. Output ONLY a single JSON object, "
            "no prose, no markdown code fences, no commentary. Start with { and end with }.\n\n"
            + prompt
        )
        raw = openrouter_chat(retry_prompt, max_tokens=1500, json_mode=True)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            # Abort the run before any publishing step. Generation cost is already
            # on the ledger via ledger_checkpoint, so this failure is visible.
            raise RuntimeError(
                "variant generation failed: OpenRouter returned non-JSON twice in a row; "
                "refusing to fall back to raw article body (would post unstructured text "
                "to every platform). Inspect OpenRouter response for cause."
            ) from e

    v = piece.variants
    v.twitter = data.get("twitter")
    if "twitter_thread" in data and isinstance(data["twitter_thread"], list):
        v.twitter_thread = [str(t) for t in data["twitter_thread"]]
    elif needs_thread(piece.body) and not v.twitter_thread:
        v.twitter_thread = split_thread(piece.body)
    v.instagram = data.get("instagram")
    v.linkedin = data.get("linkedin")
    v.tiktok = data.get("tiktok")
    v.youtube = data.get("youtube")
    v.reddit = data.get("reddit")
    v.facebook = data.get("facebook")

    text_cost = 0.0015 if needs_thread(piece.body) else 0.001
    piece.add_cost(ORCHESTRATOR_MODEL, text_cost, kind="text")

    errors = validate_lengths(
        {
            "twitter": v.twitter or "",
            "instagram": v.instagram or "",
            "linkedin": v.linkedin or "",
            "tiktok": v.tiktok or "",
            "youtube": v.youtube or "",
            "reddit": v.reddit or "",
            "facebook": v.facebook or "",
        }
    )
    if errors:
        log.warning(f"variant length issues: {errors}")


# ---------------------------------------------------------------------------
# Media generation
# ---------------------------------------------------------------------------
def _local_dir(piece: ContentPiece) -> pathlib.Path:
    safe = "".join(c if c.isalnum() else "_" for c in piece.topic)[:40]
    out = pathlib.Path(tempfile.gettempdir()) / "zeus_content" / f"{int(time.time())}_{safe}"
    out.mkdir(parents=True, exist_ok=True)
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
    url, cost = generate_image(prompt, width=w, height=h, quality=q)
    local = download(url, str(out_dir / "image_1.png"))
    piece.images.append(
        GeneratedAsset(url=url, kind="image", width=w, height=h, model="gpt-image-2", cost_usd=cost, local_path=local)
    )
    piece.add_cost("gpt-image-2", cost, kind="image")
    ledger_checkpoint(piece, "article_image_generated")


def _gen_carousel_images(piece: ContentPiece, out_dir: pathlib.Path, slides: int) -> None:
    slides = max(3, min(5, slides))
    slide_prompts = _carousel_slide_prompts(piece, slides)
    w, h, q = IMAGE_SPECS[ContentType.CAROUSEL]
    for i, sp in enumerate(slide_prompts):
        url, cost = generate_image(sp, width=w, height=h, quality=q)
        local = download(url, str(out_dir / f"slide_{i + 1}.png"))
        piece.images.append(
            GeneratedAsset(
                url=url,
                kind="image",
                width=w,
                height=h,
                model="gpt-image-2",
                cost_usd=cost,
                local_path=local,
            )
        )
        piece.add_cost("gpt-image-2", cost, kind="image")
        ledger_checkpoint(piece, f"carousel_slide_{i + 1}_generated")


def _carousel_slide_prompts(piece: ContentPiece, slides: int) -> list[str]:
    prompt = (
        f"You are designing a {slides}-slide social carousel based on this article. "
        f"Output ONLY a JSON object {{\"slides\": [\"prompt1\", \"prompt2\", ...]}} with "
        f"exactly {slides} image-generation prompts. Each prompt is a vivid visual description "
        f"(no text overlays, no captions) of one slide. Slide 1 is a strong hook visual; the rest "
        f"depict key data points or beats from the article in order.\n\n"
        f"ARTICLE TITLE: {piece.title}\n\nARTICLE BODY:\n{piece.body}"
    )
    raw = openrouter_chat(prompt, max_tokens=900, json_mode=True)
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
    url, cost = generate_video_kling(prompt, aspect_ratio=aspect, duration_s=duration)
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
    piece.add_cost("kling-v2.5-turbo-pro", cost, kind="video")
    ledger_checkpoint(piece, "video_generated")

    if piece.audio_mode:
        final_path, audio_costs = mix_audio_for_video(
            piece, local, str(out_dir), narration_text=piece.body,
        )
        if final_path != local:
            piece.video.local_path = final_path
        for model, model_cost in audio_costs.items():
            piece.add_cost(model, model_cost, kind="audio")
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
    try:
        r = requests.get(f"{PUBLER_BASE}/posts?limit=30", headers=_publer_headers(), timeout=15)
        if r.status_code != 200:
            return None
        snippet = (text_snippet or "")[:40].strip()
        for post in r.json().get("posts", []):
            if post.get("account_id") != account_id:
                continue
            ptext = (post.get("text") or "").strip()
            if snippet and ptext.startswith(snippet):
                return post.get("id")
        # fallback: most recent on the account
        for post in r.json().get("posts", []):
            if post.get("account_id") == account_id:
                return post.get("id")
    except Exception:
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


def _wait_for_posts_live(piece: ContentPiece, *, max_wait_s: int = 360, poll_interval_s: int = 15) -> None:
    """
    Poll Publer until each scheduled post goes live (state='posted' with post_link).
    Mutates piece.publer_job_ids in place: adds '<platform>_url' for each live post.
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
        # match by account + first 40 chars of the text we scheduled
        snippet = _platform_text(piece, platform) or piece.body
        post_id = _publer_find_post_id(account, snippet)
        if post_id:
            pending[platform] = post_id
            log.info(f"  tracking {platform} -> publer_post_id={post_id}")

    if not pending:
        log.warning("no Publer post IDs resolved; skipping live-wait")
        return

    deadline = time.time() + max_wait_s
    log.info(f"  waiting up to {max_wait_s}s for {len(pending)} posts to go live...")
    while pending and time.time() < deadline:
        time.sleep(poll_interval_s)
        for platform in list(pending.keys()):
            post = _publer_get_post(pending[platform])
            if not post:
                continue
            state = post.get("state")
            link = post.get("post_link") or post.get("url")
            if state == "posted" and link:
                piece.publer_job_ids[f"{platform}_url"] = link
                log.info(f"  ✓ {platform} live: {link}")
                pending.pop(platform)
            elif state in ("error", "failed"):
                err = post.get("error") or "unknown error"
                piece.publer_job_ids[f"{platform}_url"] = f"FAILED: {err}"
                log.error(f"  ✗ {platform} failed: {err}")
                pending.pop(platform)
    if pending:
        log.warning(f"  posts still pending at deadline: {list(pending.keys())}")
        for platform, post_id in pending.items():
            piece.publer_job_ids[f"{platform}_url"] = f"pending Publer post_id={post_id}"


def publish(piece: ContentPiece) -> None:
    """Push piece to Publer for every target platform that has a configured account id."""
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

    # 2) Schedule per platform
    for platform in piece.target_platforms:
        account = PUBLER_ACCOUNTS.get(platform)
        if not account:
            log.warning(f"no PUBLER_{platform.upper()}_ID configured -- skipping {platform}")
            continue

        # Twitter thread: use the thread endpoint when we have multiple tweets
        if platform == "twitter" and piece.variants.twitter_thread:
            try:
                job_id = _publer_schedule_thread(account, piece.variants.twitter_thread, media_ids)
                piece.publer_job_ids[platform] = job_id
                log.info(f"  -> twitter thread ({len(piece.variants.twitter_thread)} tweets), job_id={job_id}")
            except Exception as e:
                log.error(f"  !! twitter thread failed: {e}")
                piece.publer_job_ids[platform] = f"FAILED: {e}"
            continue

        text = _platform_text(piece, platform)
        if not text:
            log.warning(f"no variant text for {platform} -- skipping")
            continue
        if piece.video:
            ptype = "reel" if platform == "instagram" else "video"
        else:
            ptype = "photo"
        try:
            job_id = _publer_schedule(platform, account, ptype, text, media_ids)
            piece.publer_job_ids[platform] = job_id
            log.info(f"  -> {platform} scheduled, job_id={job_id}")
        except Exception as e:
            log.error(f"  !! {platform} failed: {e}")
            piece.publer_job_ids[platform] = f"FAILED: {e}"

    piece.posted_at = datetime.now(timezone.utc)

    # Wait for each scheduled post to actually go live, then capture its permalink.
    # 6-minute window covers our 2-min schedule offset + Publer lag + slow platforms.
    _wait_for_posts_live(piece, max_wait_s=360, poll_interval_s=15)

    # Status reflects ACTUAL outcome, not Publer's "we accepted your schedule request"
    # response. A platform is confirmed only when _wait_for_posts_live captured a real
    # post_link in publer_job_ids[f"{platform}_url"].
    attempted = [p for p in piece.target_platforms if p in piece.publer_job_ids]
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

    if not attempted:
        piece.status = "failed"
    elif confirmed and not failed:
        piece.status = "posted"
    elif confirmed:
        piece.status = "partial"
    else:
        piece.status = "failed"
    log.info(
        f"  publish outcome: status={piece.status} "
        f"confirmed={confirmed or '[]'} failed={failed or '[]'}"
    )


def _platform_text(piece: ContentPiece, platform: str) -> str | None:
    v = piece.variants
    if platform == "twitter":
        if v.twitter_thread:
            # Publer first-tweet-only fallback: post the lead tweet, full thread saved in Notion.
            return v.twitter_thread[0]
        return v.twitter
    return getattr(v, platform, None)


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
) -> ContentPiece:
    log.info("=" * 60)
    log.info(f"  Zeus pipeline -- {content_type.value}: {topic}")
    if audio_mode:
        log.info(f"  audio mode: {audio_mode.value}")
    log.info("=" * 60)

    title, body = generate_article_text(topic, content_type)
    piece = ContentPiece(content_type=content_type, title=title, body=body, topic=topic, audio_mode=audio_mode)
    log.info(f"  text -> title='{title}' body={len(body)}c")

    generate_variants(piece)
    log.info(f"  variants -> {[k for k in vars(piece.variants) if getattr(piece.variants, k)]}")

    generate_media_for(piece, slides=slides, video_seconds=duration)
    log.info(
        f"  media -> images={len(piece.images)} video={'yes' if piece.video else 'no'} cost=${piece.total_cost:.3f}"
    )

    errors = piece.validate()
    if errors:
        log.warning(f"validation issues: {errors}")

    archive = NotionArchive()
    archive.archive(piece)
    log.info(f"  archived -> {piece.notion_page_id}")

    if do_publish:
        publish(piece)
        archive.update_status(piece)
        log.info(f"  published -> jobs={piece.publer_job_ids}")
    else:
        log.info("  skip publish (use --publish to post)")

    # Always-on cost ledger + email notification (every run, regardless of publish flag)
    ledger_append(piece)
    backend = send_pipeline_summary(piece)
    log.info(f"  notified -> backend={backend}")

    log.info(f"DONE — total cost ${piece.total_cost:.4f}, models {piece.models_used}")
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
        )
        return 0
    except Exception as e:  # surface clean failure rather than dumping a traceback into the user's terminal
        log.exception(f"pipeline failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
