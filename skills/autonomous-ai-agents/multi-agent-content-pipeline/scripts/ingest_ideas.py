#!/usr/bin/env python3
"""
ingest_ideas — turn raw inputs in the Content Ideas Notion DB into fully
drafted pieces in the archive DB.

Inputs the user can drop into the Source column:
  • A URL (article, blog post, news page) — the page is fetched, headline
    + body are extracted, and an LLM distills it into a punchy topic +
    body in the niche's voice.
  • A YouTube link — title + channel + URL are read via oEmbed (no API
    key); the LLM turns that seed into a piece.
  • Plain text — used as the topic verbatim, like `pipeline_test --topic`.

Per-row knobs (all in the same Notion DB):
  • Source Type   = Auto | URL | YouTube | Text   (default Auto = classify)
  • Target Type   = Auto | Article | Long Article | Carousel | Short-form
                    Video | Long-form Video       (default Auto = Article)
  • Auto Publish  = checkbox; if checked, the resulting archive page lands
                    in "Ready to Publish" so publish_from_notion picks it
                    up on its next pass instead of waiting for review.
  • Notes         = free-form context the user wants the distiller to honor.

Run schema bootstrap once before first use:
    python scripts/ensure_ideas_db.py

Usage:
    python scripts/ingest_ideas.py --once          # one pass, exit
    python scripts/ingest_ideas.py --watch 300     # loop every 5 min
    python scripts/ingest_ideas.py --page-id <id>  # one row, regardless of status
    python scripts/ingest_ideas.py --dry-run       # list candidates only

Required env:
    NOTION_API_KEY, ZEUS_NOTION_HUB_PAGE_ID
    OPENROUTER_API_KEY, FAL_KEY                    (always — drafting needs both)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))
sys.path.insert(0, str(SCRIPT_DIR))


def _load_env_file() -> None:
    # Same as pipeline_test.py / publish_watcher.py / publish_from_notion.py:
    # the cron agent's execute_code subprocess has a clean env. Stdlib parser
    # since python-dotenv isn't in the system python.
    for path in ("/opt/data/.env", os.path.expanduser("~/.hermes/.env"), os.path.expanduser("~/.env")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            continue


_load_env_file()

from lib import (  # noqa: E402
    AudioMode,
    ContentPiece,
    ContentType,
    ExtractedIdea,
    NotionArchive,
    classify_idea_source,
    extract_idea,
    fetch_idea_url,
    fetch_idea_youtube,
    ledger_append,
    send_pipeline_summary,
)
import pipeline_test  # noqa: E402  -- reuse generate_media_for / openrouter_chat / ARTIFACT_ROOT

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("ingest-ideas")

NOTION_API = "https://api.notion.com/v1"
CONFIG_PATH = pathlib.Path(os.path.expanduser("~/.hermes/notion_ids.json"))

NEW_STATUS = "New"
LOCK_STATUS = "Processing"
DONE_STATUS = "Compiled"
SKIP_STATUS = "Skipped"
FAIL_STATUS = "Failed"

CONTENT_TYPE_FROM_LABEL = {
    "Article": ContentType.ARTICLE,
    "Long Article": ContentType.LONG_ARTICLE,
    "Carousel": ContentType.CAROUSEL,
    "Short-form Video": ContentType.SHORT_VIDEO,
    "Long-form Video": ContentType.LONG_VIDEO,
}

# Auto-route: if the user picks "Auto" Target Type, choose based on the
# kind of input. Plain text and URLs default to Article (cheap, fast,
# carousel-able later); YouTube videos default to ShortVideo since the
# source is already video and the audience expects motion.
AUTO_TARGET_BY_SOURCE = {
    "text": ContentType.ARTICLE,
    "url": ContentType.ARTICLE,
    "youtube": ContentType.SHORT_VIDEO,
}


# ---------------------------------------------------------------------------
# Notion helpers (same shape as publish_from_notion.py — purposefully
# duplicated rather than abstracted: the two scripts read different DBs with
# slightly different schemas, and a shared helper would be a leaky bag).
# ---------------------------------------------------------------------------
def _plain(prop: dict | None) -> str:
    if not prop:
        return ""
    kind = prop.get("type")
    if kind == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title") or [])
    if kind == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text") or [])
    if kind == "select":
        sel = prop.get("select") or {}
        return sel.get("name", "")
    if kind == "url":
        return prop.get("url") or ""
    return ""


def _checkbox(prop: dict | None) -> bool:
    return bool(prop and prop.get("type") == "checkbox" and prop.get("checkbox"))


def _number(prop: dict | None) -> Optional[float]:
    if not prop or prop.get("type") != "number":
        return None
    return prop.get("number")


def _ideas_db_id(archive: NotionArchive) -> str:
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            "ideas DB id not cached — run scripts/ensure_ideas_db.py first"
        )
    cfg = json.loads(CONFIG_PATH.read_text())
    db_id = cfg.get("ideas_db_id")
    if not db_id:
        raise RuntimeError(
            "ideas_db_id missing from ~/.hermes/notion_ids.json — run "
            "scripts/ensure_ideas_db.py to discover or create the DB"
        )
    return db_id


def _query_new(archive: NotionArchive, db_id: str) -> list[dict]:
    pages: list[dict] = []
    cursor: Optional[str] = None
    while True:
        body: dict[str, Any] = {
            "filter": {"property": "Status", "select": {"equals": NEW_STATUS}},
            "page_size": 50,
        }
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"{NOTION_API}/databases/{db_id}/query",
            headers=archive.headers,
            json=body,
            timeout=15,
        )
        if r.status_code >= 400:
            log.error(f"Notion query failed {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
        data = r.json()
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return pages


def _fetch_page(archive: NotionArchive, page_id: str) -> dict:
    r = requests.get(f"{NOTION_API}/pages/{page_id}", headers=archive.headers, timeout=15)
    r.raise_for_status()
    return r.json()


def _patch_idea(
    archive: NotionArchive,
    page_id: str,
    *,
    status: Optional[str] = None,
    compiled_page_url: Optional[str] = None,
    processed_at: Optional[datetime] = None,
    notes_append: Optional[str] = None,
) -> None:
    """Patch the ideas row. Skips properties left as None."""
    props: dict[str, Any] = {}
    if status:
        props["Status"] = {"select": {"name": status}}
    if compiled_page_url:
        props["Compiled Page"] = {"url": compiled_page_url}
    if processed_at:
        props["Processed At"] = {"date": {"start": processed_at.isoformat()}}
    if notes_append:
        # Append by overwriting — Notion's API has no native append for rich_text.
        # We read existing, concat, write back. Done lazily here since the
        # call site only triggers it on failure paths.
        page = _fetch_page(archive, page_id)
        existing = _plain((page.get("properties") or {}).get("Notes"))
        new_notes = (existing + "\n" + notes_append).strip() if existing else notes_append
        props["Notes"] = {"rich_text": [{"text": {"content": new_notes[:1900]}}]}
    if not props:
        return
    r = requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=archive.headers,
        json={"properties": props},
        timeout=15,
    )
    if r.status_code >= 400:
        log.error(f"  patch ideas row failed {r.status_code}: {r.text[:300]}")
        r.raise_for_status()


# ---------------------------------------------------------------------------
# LLM distillation — turns extracted material into title + body sized for the
# chosen content type, in the configured niche's voice. Mirrors the prompt
# style in pipeline_test.generate_article_text but seeds with the source
# material instead of just a topic string.
# ---------------------------------------------------------------------------
TARGET_CHARS = {
    ContentType.ARTICLE: "250-450",
    ContentType.LONG_ARTICLE: "550-900",
    ContentType.CAROUSEL: "300-440",
    ContentType.SHORT_VIDEO: "300-500",
    ContentType.LONG_VIDEO: "700-1200",
}


def _distill(
    extracted: ExtractedIdea,
    content_type: ContentType,
    notes: str,
) -> tuple[str, str, str, float, str]:
    """
    Returns (topic, title, body, cost_usd, cost_source).

    Topic is a short headline-style string the rest of the pipeline uses as
    its seed (logged, sent in emails). Title is the post title. Body is the
    actual social-post copy.
    """
    niche = pipeline_test.NICHE
    niche_clause = (
        f"DOMAIN: this piece is for a {' / '.join(niche)} audience. "
        f"Use vocabulary, references, tickers, and framing native to those "
        f"fields. No generic platitudes — be specific to the domain.\n"
        if niche else ""
    )
    target_chars = TARGET_CHARS[content_type]

    source_block = f"SOURCE TYPE: {extracted.source_type}\nSOURCE: {extracted.source}\n"
    if extracted.title:
        source_block += f"SOURCE TITLE: {extracted.title}\n"
    if extracted.metadata.get("author"):
        source_block += f"AUTHOR/CHANNEL: {extracted.metadata['author']}\n"
    if extracted.excerpt:
        source_block += f"\nSOURCE EXCERPT:\n{extracted.excerpt}\n"
    notes_block = f"\nUSER NOTES: {notes}\n" if notes.strip() else ""

    prompt = (
        f"You are compiling a social-ready post from raw source material.\n\n"
        f"{niche_clause}"
        f"{source_block}{notes_block}\n"
        f"Distill the source into ONE post:\n"
        f"  - Body length: {target_chars} characters\n"
        f"  - Tone: Bloomberg Terminal condensed. Concrete numbers, tickers, take.\n"
        f"  - The body must be long enough that Instagram, LinkedIn, TikTok and "
        f"Facebook all truncate it with a 'read more' affordance.\n"
        f"  - No hashtags. No 'in conclusion'. No filler.\n\n"
        f"Return ONLY a JSON object with this exact shape:\n"
        f'  {{"topic": "<6-14 word headline-style topic>", '
        f'"title": "<5-10 word punchy title (no dates)>", '
        f'"body": "<the post body>"}}\n'
    )
    raw, cost, source = pipeline_test.openrouter_chat(prompt, max_tokens=1100, json_mode=True)
    try:
        data = json.loads(raw)
        topic = (data.get("topic") or "").strip()
        title = (data.get("title") or "").strip().lstrip("#").strip()
        body = (data.get("body") or "").strip()
    except json.JSONDecodeError:
        # Distill JSON failed — degrade gracefully: extracted title becomes
        # both the topic and the title, the excerpt becomes the body. The
        # cost still landed on the openrouter call, so we record it.
        log.warning("distill: JSON parse failed; using raw extraction as fallback")
        topic = (extracted.title or extracted.source)[:80]
        title = extracted.title or topic
        body = extracted.excerpt or topic

    if not topic or not title or not body:
        raise RuntimeError(
            f"distill: LLM returned incomplete piece (topic={bool(topic)}, "
            f"title={bool(title)}, body={bool(body)})"
        )
    return topic, title, body, cost, source


# ---------------------------------------------------------------------------
# Per-idea processing
# ---------------------------------------------------------------------------
def _resolve_content_type(props: dict, source_type: str) -> ContentType:
    label = _plain(props.get("Target Type"))
    if label and label != "Auto" and label in CONTENT_TYPE_FROM_LABEL:
        return CONTENT_TYPE_FROM_LABEL[label]
    return AUTO_TARGET_BY_SOURCE.get(source_type, ContentType.ARTICLE)


def _resolve_source(raw_source: str, props: dict) -> tuple[ExtractedIdea, str]:
    """Returns (extracted, source_type_resolved)."""
    forced = _plain(props.get("Source Type"))
    if forced and forced != "Auto":
        kind = forced.lower()
        if kind == "text":
            return ExtractedIdea(source_type="text", source=raw_source, excerpt=raw_source[:6000]), "text"
        if kind == "url":
            return fetch_idea_url(raw_source), "url"
        if kind == "youtube":
            return fetch_idea_youtube(raw_source), "youtube"
        raise RuntimeError(f"unknown forced Source Type {forced!r}")
    return extract_idea(raw_source), classify_idea_source(raw_source)


def _compile_idea(archive: NotionArchive, page: dict) -> ContentPiece:
    """End-to-end: extract → distill → archive draft → generate media → patch assets.

    Never raises after the archive row exists — partial-failure pieces come
    back with status="media_partial" or "failed" so the caller can still
    record the cost row + email rather than losing the spend.
    """
    props = page.get("properties") or {}
    raw_source = _plain(props.get("Source"))
    if not raw_source:
        raise ValueError("Source field is empty — paste a URL, YouTube link, or topic text")
    notes = _plain(props.get("Notes"))

    extracted, source_kind = _resolve_source(raw_source, props)
    log.info(f"  extracted: type={extracted.source_type} title={extracted.title[:60]!r} excerpt={len(extracted.excerpt)}c")

    content_type = _resolve_content_type(props, source_kind)
    log.info(f"  target type: {content_type.value}")

    topic, title, body, distill_cost, distill_source = _distill(extracted, content_type, notes)
    log.info(f"  distilled -> topic='{topic}' title='{title}' body={len(body)}c (cost ${distill_cost:.5f})")

    piece = ContentPiece(
        content_type=content_type,
        title=title,
        body=body,
        topic=topic,
    )
    piece.add_cost(pipeline_test.ORCHESTRATOR_MODEL, distill_cost, kind="text", source=distill_source)

    # Stable artifact dir before any paid call (memory: never /tmp).
    artifact_dir = pipeline_test.ARTIFACT_ROOT / f"{piece.run_id}_{pipeline_test._safe_topic(piece.topic)}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    piece.local_artifact_dir = str(artifact_dir)

    # Archive EARLY so any media spend lands in Notion even if we crash.
    archive.archive(piece)
    log.info(f"  archive page (pre-media) -> {piece.notion_page_id}")

    # Per-row media knobs (Slides / Video Duration in the ideas DB). Fall
    # back to pipeline_test defaults when unset. Bounds enforced by
    # generate_media_for / _gen_video themselves.
    slides_val = _number(props.get("Slides"))
    duration_val = _number(props.get("Video Duration"))
    slides = int(slides_val) if slides_val else 4
    video_seconds = int(duration_val) if duration_val else 5

    try:
        pipeline_test.generate_media_for(piece, slides=slides, video_seconds=video_seconds)
        log.info(f"  media -> images={len(piece.images)} video={'yes' if piece.video else 'no'}")
        piece.status = "draft"  # ready for the user to flip / for auto-publish below
    except Exception as e:
        # Partial spend MUST still land in the archive row + ledger so cost
        # tracking stays accurate. Mirror pipeline_test.run's recovery shape.
        piece.status = "media_partial" if (piece.images or piece.video) else "failed"
        log.error(f"  media generation failed (status={piece.status}): {e}")

    try:
        archive.update_assets(piece)
    except Exception as e:
        log.warning(f"  update_assets failed: {e}")

    return piece


def _archive_page_url(archive: NotionArchive, page_id: str) -> str:
    """Compose the canonical https://www.notion.so/<id-without-dashes> URL."""
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def _process_one(archive: NotionArchive, page: dict, *, dry_run: bool) -> None:
    page_id = page["id"]
    props = page.get("properties") or {}
    title = _plain(props.get("Title")) or _plain(props.get("Name")) or page_id
    log.info(f"\n--- {title} [{page_id}]")

    if dry_run:
        log.info(f"  (dry-run) source={_plain(props.get('Source'))[:80]!r} type={_plain(props.get('Source Type'))} target={_plain(props.get('Target Type'))}")
        return

    # Lock the row.
    _patch_idea(archive, page_id, status=LOCK_STATUS)

    piece: Optional[ContentPiece] = None
    compile_error: Optional[Exception] = None
    try:
        piece = _compile_idea(archive, page)
    except Exception as e:
        compile_error = e
        log.exception(f"  compile failed pre-archive: {e}")

    # Always finalize observability when we got far enough to start spending —
    # ledger row + email — so this surface matches cron-driven runs
    # (memory: every run hits ledger + email + Notion).
    if piece is not None:
        try:
            ledger_append(piece)
        except Exception as e:
            log.warning(f"  ledger_append failed: {e}")
        try:
            backend = send_pipeline_summary(piece)
            log.info(f"  notified -> backend={backend}")
        except Exception as e:
            log.warning(f"  email failed: {e}")

    # Pre-archive failure (extract / distill / archive() crashed) — the ideas
    # row never got linked to anything, so just mark it failed and bail.
    if compile_error is not None or piece is None:
        _patch_idea(
            archive, page_id, status=FAIL_STATUS,
            processed_at=datetime.now(timezone.utc),
            notes_append=f"[{datetime.now(timezone.utc).isoformat()}] failed: {compile_error}",
        )
        return

    archive_page_url = _archive_page_url(archive, piece.notion_page_id)

    # Media partial / failed: leave the archive row Draft so the user can
    # decide whether to re-run media or salvage by hand. Skip auto-publish
    # even if it was set — half a carousel posted to Twitter is worse than
    # not posting at all.
    if piece.status in ("media_partial", "failed"):
        _patch_idea(
            archive, page_id, status=FAIL_STATUS,
            compiled_page_url=archive_page_url,
            processed_at=datetime.now(timezone.utc),
            notes_append=f"[{datetime.now(timezone.utc).isoformat()}] media failed (status={piece.status}); archive page exists for review",
        )
        log.info(f"  partial -> archive: {archive_page_url}  status={piece.status}")
        return

    auto_publish = _checkbox(props.get("Auto Publish"))
    if auto_publish:
        # Hand off to publish_from_notion: flip the archive row's Status to
        # "Ready to Publish" and let that script ship it on its next pass.
        try:
            r = requests.patch(
                f"{NOTION_API}/pages/{piece.notion_page_id}",
                headers=archive.headers,
                json={"properties": {"Status": {"select": {"name": "Ready to Publish"}}}},
                timeout=15,
            )
            r.raise_for_status()
            log.info(f"  auto-publish: archive page flipped to 'Ready to Publish'")
        except Exception as e:
            log.warning(f"  could not set archive page to Ready to Publish: {e}")

    _patch_idea(
        archive, page_id,
        status=DONE_STATUS,
        compiled_page_url=archive_page_url,
        processed_at=datetime.now(timezone.utc),
    )
    log.info(f"  done -> archive: {archive_page_url}  auto_publish={auto_publish}")


def run_pass(archive: NotionArchive, db_id: str, *, dry_run: bool) -> int:
    pages = _query_new(archive, db_id)
    if not pages:
        log.info("no rows in 'New' status — nothing to ingest")
        return 0
    log.info(f"found {len(pages)} new idea(s)")
    handled = 0
    for page in pages:
        try:
            _process_one(archive, page, dry_run=dry_run)
            handled += 1
        except Exception as e:
            log.exception(f"  unhandled error on page {page.get('id')}: {e}")
    return handled


def main() -> int:
    p = argparse.ArgumentParser(description="Compile Notion content ideas into drafted pieces")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="single pass then exit (default)")
    g.add_argument("--watch", type=int, metavar="SECONDS", help="loop forever, sleep N seconds between passes")
    g.add_argument("--page-id", help="process exactly this ideas-DB page id, regardless of status")
    p.add_argument("--dry-run", action="store_true", help="list candidates without locking or compiling")
    args = p.parse_args()

    required = ["NOTION_API_KEY"]
    if not args.dry_run:
        required += ["OPENROUTER_API_KEY", "FAL_KEY"]
    # Hub page id is checked by NotionArchive itself.
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"missing env: {', '.join(missing)}")
        return 2

    archive = NotionArchive()
    db_id = _ideas_db_id(archive)
    log.info(f"ideas DB: {db_id}")

    if args.page_id:
        page = _fetch_page(archive, args.page_id)
        _process_one(archive, page, dry_run=args.dry_run)
        return 0

    if args.watch:
        log.info(f"watch mode: polling every {args.watch}s — Ctrl-C to stop")
        while True:
            try:
                run_pass(archive, db_id, dry_run=args.dry_run)
            except Exception as e:
                log.exception(f"pass failed: {e}")
            time.sleep(args.watch)

    run_pass(archive, db_id, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
