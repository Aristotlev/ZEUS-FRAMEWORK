#!/usr/bin/env python3
"""
publish_from_notion — flip Notion drafts into live posts.

Workflow:
  1. You drafted a piece in Notion (Status="Draft", Body filled in,
     optionally with Image URLs / Video URL already populated).
  2. You change the row's Status to "Ready to Publish" in the Notion UI.
  3. This script polls the archive DB, picks up rows in that state, and:
       - Locks the row by flipping Status to "Publishing"
       - Reconstructs a ContentPiece from the row's properties
       - Generates media via fal if the row has none
       - Publishes via Publer (non-blocking — watcher resolves permalinks)
       - Lets publish() leave the Notion status at "Scheduled"

Run schema bootstrap once before first use so "Ready to Publish" exists:
    python scripts/ensure_notion_schema.py

Usage:
    python scripts/publish_from_notion.py --once       # one pass, exit
    python scripts/publish_from_notion.py --watch 60   # loop every 60s
    python scripts/publish_from_notion.py --page-id <notion-page-id>  # one row
    python scripts/publish_from_notion.py --dry-run    # list candidates only

Required env (same set the rest of the pipeline uses):
    NOTION_API_KEY, ZEUS_NOTION_HUB_PAGE_ID
    OPENROUTER_API_KEY, FAL_KEY        (only if a row needs media regen)
    PUBLER_API_KEY, PUBLER_WORKSPACE_ID, PUBLER_<PLATFORM>_ID
"""
from __future__ import annotations

import argparse
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
    # Same as pipeline_test.py / publish_watcher.py: the cron agent's
    # execute_code subprocess has a clean env, so without this the script
    # can't see NOTION_API_KEY / PUBLER_API_KEY etc. Stdlib parser.
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
    GeneratedAsset,
    NotionArchive,
    download,
    ledger_append,
    send_pipeline_summary,
)
import pipeline_test  # noqa: E402  -- reuse publish() / generate_media_for() / ARTIFACT_ROOT

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("publish-from-notion")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

READY_STATUS = "Ready to Publish"
LOCK_STATUS = "Publishing"

CONTENT_TYPE_FROM_LABEL = {
    "Article": ContentType.ARTICLE,
    "Long Article": ContentType.LONG_ARTICLE,
    "Carousel": ContentType.CAROUSEL,
    "Short-form Video": ContentType.SHORT_VIDEO,
    "Long-form Video": ContentType.LONG_VIDEO,
}

AUDIO_MODE_FROM_LABEL = {
    "Music Only": AudioMode.MUSIC_ONLY,
    "Music Narration": AudioMode.MUSIC_AND_NARRATION,
    "Narration Primary": AudioMode.NARRATION_PRIMARY,
}


# ---------------------------------------------------------------------------
# Notion property readers — every Notion API page returns a dict where each
# property has a wrapper object whose shape depends on the property's type.
# These helpers flatten that into plain Python types so the reconstruction
# logic below stays readable.
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
    if kind == "number":
        n = prop.get("number")
        return str(n) if n is not None else ""
    return ""


def _multi_select(prop: dict | None) -> list[str]:
    if not prop or prop.get("type") != "multi_select":
        return []
    return [opt.get("name", "") for opt in prop.get("multi_select") or []]


def _patch_status(archive: NotionArchive, page_id: str, status_label: str) -> None:
    """Set the Status select on a Notion page. Used to lock + report state."""
    r = requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=archive.headers,
        json={"properties": {"Status": {"select": {"name": status_label}}}},
        timeout=15,
    )
    if r.status_code >= 400:
        log.error(f"  failed to patch status to {status_label}: {r.status_code} {r.text[:200]}")
        r.raise_for_status()


def _query_ready(archive: NotionArchive) -> list[dict]:
    """Fetch every row currently flagged 'Ready to Publish'."""
    pages: list[dict] = []
    cursor: Optional[str] = None
    while True:
        body: dict[str, Any] = {
            "filter": {"property": "Status", "select": {"equals": READY_STATUS}},
            "page_size": 50,
        }
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"{NOTION_API}/databases/{archive.archive_db_id}/query",
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
    r = requests.get(
        f"{NOTION_API}/pages/{page_id}", headers=archive.headers, timeout=15
    )
    r.raise_for_status()
    return r.json()


def _piece_from_page(page: dict) -> ContentPiece:
    """
    Reconstruct a ContentPiece from a Notion archive row.

    Required: Title (or Name), Body, Content Type. Without all three we can't
    publish anything sensible, so the script raises rather than guessing.

    Optional: Topic (falls back to Title), Audio Mode, Image URLs, Video URL,
    Local Artifact Dir, Run ID. These are best-effort; missing ones either
    trigger media regen or leave the piece in a degraded but publishable state.
    """
    props = page.get("properties") or {}
    title = _plain(props.get("Title")) or _plain(props.get("Name"))
    body = _plain(props.get("Body")) or _plain(props.get("Article Body")) or _plain(props.get("Description"))
    ct_label = _plain(props.get("Content Type"))

    if not body:
        raise ValueError("page has empty Body — fill it in before flipping to Ready to Publish")
    if not ct_label:
        raise ValueError("page has no Content Type — set it to Article / Carousel / etc. first")
    if ct_label not in CONTENT_TYPE_FROM_LABEL:
        raise ValueError(f"unknown Content Type label {ct_label!r}; expected one of {list(CONTENT_TYPE_FROM_LABEL)}")
    content_type = CONTENT_TYPE_FROM_LABEL[ct_label]

    topic = _plain(props.get("Topic")) or title or "Untitled"
    am_label = _plain(props.get("Audio Mode"))
    audio_mode = AUDIO_MODE_FROM_LABEL.get(am_label) if am_label else None

    piece = ContentPiece(
        content_type=content_type,
        title=title or topic,
        body=body,
        topic=topic,
        audio_mode=audio_mode,
    )
    piece.notion_page_id = page["id"]
    # Keep the existing run_id if the row has one; ledger correlations stay sane.
    existing_run = _plain(props.get("Run ID"))
    if existing_run:
        piece.run_id = existing_run
    existing_dir = _plain(props.get("Local Artifact Dir")) or _plain(props.get("Artifact Dir"))
    if existing_dir:
        piece.local_artifact_dir = existing_dir

    image_urls = [u.strip() for u in _plain(props.get("Image URLs")).splitlines() if u.strip()]
    video_url = _plain(props.get("Video URL"))
    return _attach_existing_media(piece, image_urls, video_url)


def _attach_existing_media(
    piece: ContentPiece, image_urls: list[str], video_url: str
) -> ContentPiece:
    """
    Download any media URLs the Notion row already references so Publer can
    upload from a real local file. fal URLs expire — re-downloading from the
    Notion record means we lose nothing if the user delays publishing for
    days. If the row had no media at all, the caller falls back to fresh
    media generation.
    """
    if not image_urls and not video_url:
        return piece

    out_dir = pathlib.Path(
        piece.local_artifact_dir
        or pipeline_test.ARTIFACT_ROOT / f"{piece.run_id}_{pipeline_test._safe_topic(piece.topic)}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    piece.local_artifact_dir = str(out_dir)

    for idx, url in enumerate(image_urls):
        try:
            local = download(url, str(out_dir / f"slide_{idx + 1}.png"))
        except Exception as e:
            log.warning(f"  could not download image {url!r}: {e}; skipping this asset")
            continue
        piece.images.append(
            GeneratedAsset(
                url=url,
                kind="image",
                model="archived",
                cost_usd=0.0,
                local_path=local,
            )
        )

    if video_url:
        try:
            local = download(video_url, str(out_dir / "video.mp4"))
        except Exception as e:
            log.warning(f"  could not download video {video_url!r}: {e}; skipping video asset")
        else:
            piece.video = GeneratedAsset(
                url=video_url,
                kind="video",
                model="archived",
                cost_usd=0.0,
                local_path=local,
            )

    return piece


def _ensure_media(piece: ContentPiece) -> None:
    """
    Generate media if the reconstructed piece has none. We check the validity
    rules for the content type rather than raw counts so a row with 1 image
    on a Carousel still triggers regen (Carousel needs 3-5).
    """
    has_video_target = piece.content_type in (ContentType.SHORT_VIDEO, ContentType.LONG_VIDEO)
    if has_video_target and piece.video and piece.video.local_path:
        return
    if not has_video_target and piece.images and not piece.validate():
        return

    log.info("  media missing or insufficient — generating fresh assets")
    pipeline_test.generate_media_for(piece, slides=4, video_seconds=5)


def _publish_one(archive: NotionArchive, page: dict, *, dry_run: bool) -> Optional[ContentPiece]:
    page_id = page["id"]
    title = _plain((page.get("properties") or {}).get("Title")) or _plain(
        (page.get("properties") or {}).get("Name")
    ) or page_id
    log.info(f"\n--- {title} [{page_id}]")

    if dry_run:
        log.info("  (dry-run) would lock + reconstruct + publish")
        return None

    # Lock first so a concurrent run of this script doesn't double-publish.
    _patch_status(archive, page_id, LOCK_STATUS)

    try:
        piece = _piece_from_page(page)
    except Exception as e:
        log.error(f"  reconstruction failed: {e}")
        _patch_status(archive, page_id, "Failed")
        return None

    log.info(f"  rebuilt -> type={piece.content_type.value} body={len(piece.body)}c run_id={piece.run_id}")

    try:
        _ensure_media(piece)
    except Exception as e:
        log.error(f"  media step failed: {e}")
        piece.status = "media_partial" if (piece.images or piece.video) else "failed"
        # Leave Notion status at Failed so the user can investigate / re-flip.
        _patch_status(archive, page_id, "Failed")
        return piece

    # Flip the row's media-related fields so anything we just generated lands
    # back in Notion before the publish hand-off (image URLs, cost, etc.).
    try:
        archive.update_assets(piece)
    except Exception as e:
        log.warning(f"  update_assets failed (continuing): {e}")

    try:
        pipeline_test.publish(piece)
    except Exception as e:
        log.error(f"  publish failed: {e}")
        piece.status = "failed"
        _patch_status(archive, page_id, "Failed")
        return piece

    # publish() sets piece.status to "scheduled" (non-blocking path); update_status
    # mirrors that into the Notion row alongside Posted At + Job IDs + Post Links.
    try:
        archive.update_status(piece)
    except Exception as e:
        log.warning(f"  update_status failed: {e}")

    # Summary email + ledger row so on-demand publishes show up in the same
    # observability surface as cron-driven runs (memory: every run must hit
    # ledger + email + Notion).
    try:
        ledger_append(piece)
    except Exception as e:
        log.warning(f"  ledger_append failed: {e}")
    try:
        backend = send_pipeline_summary(piece)
        log.info(f"  notified -> backend={backend}")
    except Exception as e:
        log.warning(f"  email failed: {e}")

    log.info(f"  done -> status={piece.status} jobs={list(piece.publer_job_ids.keys())}")
    return piece


def run_pass(archive: NotionArchive, *, dry_run: bool) -> int:
    pages = _query_ready(archive)
    if not pages:
        log.info("no pages in 'Ready to Publish' status — nothing to do")
        return 0
    log.info(f"found {len(pages)} ready page(s)")
    handled = 0
    for page in pages:
        try:
            _publish_one(archive, page, dry_run=dry_run)
            handled += 1
        except Exception as e:  # last-ditch — never let one row stall a batch
            log.exception(f"  unhandled error on page {page.get('id')}: {e}")
    return handled


def main() -> int:
    p = argparse.ArgumentParser(description="Publish Notion drafts marked 'Ready to Publish'")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="single pass then exit (default)")
    g.add_argument("--watch", type=int, metavar="SECONDS", help="loop forever, sleep N seconds between passes")
    g.add_argument("--page-id", help="publish exactly this Notion page id, regardless of status")
    p.add_argument("--dry-run", action="store_true", help="list candidates without locking or publishing")
    args = p.parse_args()

    required = ["NOTION_API_KEY", "ZEUS_NOTION_HUB_PAGE_ID"]
    if not args.dry_run:
        # Publishing fires real Publer + (sometimes) fal calls.
        required += ["PUBLER_API_KEY", "OPENROUTER_API_KEY", "FAL_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    # ZEUS_NOTION_HUB_PAGE_ID can also be NOTION_CONTENT_HUB_PAGE_ID (legacy);
    # NotionArchive accepts either. So only complain if BOTH are unset.
    if "ZEUS_NOTION_HUB_PAGE_ID" in missing and os.getenv("NOTION_CONTENT_HUB_PAGE_ID"):
        missing.remove("ZEUS_NOTION_HUB_PAGE_ID")
    if missing:
        log.error(f"missing env: {', '.join(missing)}")
        return 2

    archive = NotionArchive()
    log.info(f"archive DB: {archive.archive_db_id}")

    if args.page_id:
        page = _fetch_page(archive, args.page_id)
        _publish_one(archive, page, dry_run=args.dry_run)
        return 0

    if args.watch:
        log.info(f"watch mode: polling every {args.watch}s — Ctrl-C to stop")
        while True:
            try:
                run_pass(archive, dry_run=args.dry_run)
            except Exception as e:
                log.exception(f"pass failed: {e}")
            time.sleep(args.watch)

    run_pass(archive, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
