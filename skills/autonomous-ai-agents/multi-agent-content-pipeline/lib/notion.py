"""
Notion archive writer for Zeus content pipeline.

Saves every generated ContentPiece to your Notion content-hub archive database.
Auto-discovers the child database under the parent page given by ZEUS_NOTION_HUB_PAGE_ID
(or the legacy NOTION_CONTENT_HUB_PAGE_ID). The first call caches the resolved DB id
to ~/.hermes/notion_ids.json so subsequent runs skip the lookup.

Required env:
  NOTION_API_KEY               — Notion integration token
  ZEUS_NOTION_HUB_PAGE_ID      — Notion page id of the parent page that holds the
                                 archive database (32-char hex, no dashes; copy the
                                 trailing id from the page URL)

Resilience: queries the DB schema once and only sends properties that actually exist,
so the same code works whether the user's archive DB has Title/Status/Cost/etc. or a
different subset. Body always renders to the page block tree as a fallback.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests

from .content_types import ContentPiece, ContentType

log = logging.getLogger("zeus.notion")


def _platforms_posted(p: ContentPiece) -> list[str]:
    """Subset of target_platforms with a real https permalink — for the
    'Platforms Posted' multi_select. Lets the user filter the archive DB to
    'show me everything that actually shipped on Twitter' without parsing text.
    """
    out: list[str] = []
    for platform in p.target_platforms:
        url = p.publer_job_ids.get(f"{platform}_url", "")
        if url and isinstance(url, str) and url.startswith("http"):
            out.append(platform)
    return out


def _platforms_failed(p: ContentPiece) -> list[str]:
    """Subset of target_platforms that reported FAILED at any stage."""
    out: list[str] = []
    for platform in p.target_platforms:
        scheduled = str(p.publer_job_ids.get(platform, ""))
        url = str(p.publer_job_ids.get(f"{platform}_url", ""))
        if scheduled.startswith("FAILED") or url.startswith("FAILED"):
            out.append(platform)
    return out


def _render_post_links(p: ContentPiece) -> Optional[str]:
    """One ContentPiece -> one human-readable per-platform results block.

    Replaces the old "Job IDs" dump that mashed job ids, URLs, and FAILED
    markers together. Each line is `platform: <icon> <url-or-status>`.
    Skips platforms that were never attempted so the field stays tight.
    """
    if not p.publer_job_ids:
        return None
    lines: list[str] = []
    for platform in p.target_platforms:
        scheduled = str(p.publer_job_ids.get(platform, ""))
        url = str(p.publer_job_ids.get(f"{platform}_url", ""))
        if not scheduled:
            continue
        if scheduled.startswith("FAILED"):
            lines.append(f"{platform}: ✗ {scheduled}")
        elif url.startswith("http"):
            lines.append(f"{platform}: ✓ {url}")
        elif url.startswith("FAILED"):
            lines.append(f"{platform}: ✗ {url}")
        elif url.startswith("PENDING"):
            lines.append(f"{platform}: … {url}")
        else:
            lines.append(f"{platform}: scheduled (job={scheduled})")
    return "\n".join(lines) or None


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"  # 2025-09-03 silently drops props on database ops

CONFIG_PATH = Path(os.path.expanduser("~/.hermes/notion_ids.json"))

NOTION_TEXT_LIMIT = 1900  # Notion's hard cap on rich_text content is 2000


def _hyphenate(uid: Optional[str]) -> Optional[str]:
    if not uid:
        return None
    s = uid.replace("-", "")
    if len(s) != 32:
        return uid
    return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"


def extract_id_from_url(url: str) -> Optional[str]:
    """Extract a 32-hex Notion ID from a notion.so URL. Notion always puts the ID at the tail."""
    stripped = url.split("?")[0].rstrip("/").replace("-", "")
    # Anchor at end of string — Notion always trails with the ID.
    m = re.search(r"([a-f0-9]{32})$", stripped)
    if not m:
        # fall back to the last 32-hex run anywhere in the URL
        matches = re.findall(r"[a-f0-9]{32}", stripped)
        if not matches:
            return None
        return _hyphenate(matches[-1])
    return _hyphenate(m.group(1))


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


class NotionArchive:
    def __init__(
        self,
        api_key: Optional[str] = None,
        archive_db_id: Optional[str] = None,
        hub_page_id: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("NOTION_API_KEY")
        if not self.api_key:
            raise RuntimeError("NOTION_API_KEY not set in env")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        hub = (
            hub_page_id
            or os.getenv("ZEUS_NOTION_HUB_PAGE_ID")
            or os.getenv("NOTION_CONTENT_HUB_PAGE_ID")
        )
        # Skip the parent-page discovery walk entirely if the archive DB is
        # supplied directly. NOTION_ARCHIVE_DB_ID is the documented var name
        # in deploy/.env.prod.example and what users actually set in prod.
        archive_db_id = archive_db_id or os.getenv("NOTION_ARCHIVE_DB_ID")
        if not hub and not archive_db_id:
            raise RuntimeError(
                "Set ZEUS_NOTION_HUB_PAGE_ID to the 32-char hex id of your Notion "
                "content-hub page (the trailing id in the page URL), or pass "
                "archive_db_id directly (or set NOTION_ARCHIVE_DB_ID)."
            )
        self.hub_page_id = _hyphenate(hub) or hub
        self._archive_db_id: Optional[str] = _hyphenate(archive_db_id)
        self._db_schema: Optional[dict] = None

    @property
    def archive_db_id(self) -> str:
        if self._archive_db_id:
            return self._archive_db_id
        cfg = _load_config()
        if cached := cfg.get("archive_db_id"):
            self._archive_db_id = cached
            return cached
        self._archive_db_id = self._discover_archive_db()
        cfg["archive_db_id"] = self._archive_db_id
        _save_config(cfg)
        return self._archive_db_id

    def _discover_archive_db(self) -> str:
        """Walk children of the hub page, return the first child_database (preferring one named *archive*)."""
        log.info(f"discovering archive DB under page {self.hub_page_id}")
        r = requests.get(
            f"{NOTION_API}/blocks/{self.hub_page_id}/children?page_size=100",
            headers=self.headers,
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        databases = [b for b in results if b.get("type") == "child_database"]
        if not databases:
            raise RuntimeError(
                f"No child_database under page {self.hub_page_id}. "
                "Pass archive_db_id explicitly, or move the archive DB inside Content Hub."
            )
        for b in databases:
            title = (b.get("child_database") or {}).get("title", "").lower()
            if "archive" in title:
                log.info(f"  matched archive DB by title: {title} -> {b['id']}")
                return b["id"]
        b = databases[0]
        log.info(f"  no 'archive' match -- using first DB '{(b.get('child_database') or {}).get('title')}' -> {b['id']}")
        return b["id"]

    def _get_db_schema(self) -> dict:
        if self._db_schema is not None:
            return self._db_schema
        r = requests.get(
            f"{NOTION_API}/databases/{self.archive_db_id}", headers=self.headers, timeout=15
        )
        r.raise_for_status()
        self._db_schema = r.json().get("properties", {})
        log.info(f"  archive DB schema fields: {sorted(self._db_schema.keys())}")
        return self._db_schema

    def archive(self, piece: ContentPiece) -> str:
        """Create a Notion page in the archive DB. Returns new page id; also sets piece.notion_page_id."""
        schema = self._get_db_schema()
        props = self._build_properties(piece, schema)
        children = self._build_children(piece)
        body: dict[str, Any] = {
            "parent": {"database_id": self.archive_db_id},
            "properties": props,
            "children": children,
        }
        r = requests.post(f"{NOTION_API}/pages", headers=self.headers, json=body, timeout=30)
        if r.status_code >= 400:
            log.error(f"Notion archive failed {r.status_code}: {r.text[:500]}")
            r.raise_for_status()
        page_id = r.json()["id"]
        piece.notion_page_id = page_id
        log.info(f"  archived to Notion: {page_id}")
        return page_id

    def update_assets(self, piece: ContentPiece) -> None:
        """
        Patch the page with currently-captured media (image URLs, video URL, cost,
        models, status, run_id, local artifact dir) and append image/video blocks
        for every asset on the piece.

        Designed to be called once after media generation (or after a partial crash
        in the orchestrator's finally block). Re-calling will append duplicate
        blocks, so callers should call at most once per page; properties are
        overwritten, not duplicated.
        """
        if not piece.notion_page_id:
            log.warning("update_assets called but piece has no notion_page_id")
            return
        schema = self._get_db_schema()
        wanted: dict[str, tuple[str, Any]] = {
            "Status": ("select", _humanize_status(piece.status)),
            "Cost USD": ("number", piece.total_cost),
            "Models Used": ("multi_select", piece.models_used),
            "Image URLs": ("rich_text", _trunc("\n".join(a.url for a in piece.images))),
            "Video URL": ("url", piece.video.url if piece.video else None),
            "Local Artifact Dir": ("rich_text", piece.local_artifact_dir),
            "Artifact Dir": ("rich_text", piece.local_artifact_dir),
            "Run ID": ("rich_text", piece.run_id),
        }
        props: dict = {}
        for name, (kind, value) in wanted.items():
            if name not in schema:
                continue
            if value is None or value == "" or value == []:
                continue
            if schema[name].get("type") != kind:
                continue
            props[name] = _format_property(kind, value)
        if props:
            r = requests.patch(
                f"{NOTION_API}/pages/{piece.notion_page_id}",
                headers=self.headers,
                json={"properties": props},
                timeout=15,
            )
            if r.status_code >= 400:
                log.error(f"Notion update_assets props failed {r.status_code}: {r.text[:300]}")
                r.raise_for_status()

        children: list[dict] = []
        for img in piece.images:
            if not img.url:
                continue
            children.append(
                {
                    "object": "block",
                    "type": "image",
                    "image": {"type": "external", "external": {"url": img.url}},
                }
            )
        if piece.video and piece.video.url:
            children.append(
                {
                    "object": "block",
                    "type": "video",
                    "video": {"type": "external", "external": {"url": piece.video.url}},
                }
            )
        if children:
            r = requests.patch(
                f"{NOTION_API}/blocks/{piece.notion_page_id}/children",
                headers=self.headers,
                json={"children": children},
                timeout=20,
            )
            if r.status_code >= 400:
                log.error(f"Notion update_assets blocks failed {r.status_code}: {r.text[:300]}")
                r.raise_for_status()

    def update_status(self, piece: ContentPiece) -> None:
        """Patch the piece's existing Notion page with current status / posted_at / job ids."""
        if not piece.notion_page_id:
            log.warning("update_status called but piece has no notion_page_id")
            return
        schema = self._get_db_schema()
        props = self._build_properties(piece, schema, only_status=True)
        if not props:
            return
        r = requests.patch(
            f"{NOTION_API}/pages/{piece.notion_page_id}",
            headers=self.headers,
            json={"properties": props},
            timeout=15,
        )
        if r.status_code >= 400:
            log.error(f"Notion update failed {r.status_code}: {r.text[:300]}")
            r.raise_for_status()

    def _build_properties(
        self, p: ContentPiece, schema: dict, only_status: bool = False
    ) -> dict:
        # Per-platform results live as STRUCTURED columns on the same row, not
        # one-row-per-platform spam:
        #   - "Post Links"        rich_text   — clean `platform: <icon> <url-or-status>` block
        #   - "Platforms Posted"  multi_select — only platforms with a real https permalink
        #   - "Platforms Failed"  multi_select — anything that returned FAILED
        #   - "Job IDs"           rich_text   — debug only (raw Publer job ids, no URLs)
        # Any field missing from the user's DB schema is silently skipped, so
        # this is forward-compatible — add the columns in Notion when ready.
        wanted: dict[str, tuple[str, Any]] = {
            "Status": ("select", _humanize_status(p.status)),
            "Posted At": ("date", p.posted_at.isoformat() if p.posted_at else None),
            "Post Links": ("rich_text", _trunc(_render_post_links(p))),
            "Platforms Posted": ("multi_select", _platforms_posted(p)),
            "Platforms Failed": ("multi_select", _platforms_failed(p)),
            "Job IDs": (
                "rich_text",
                "\n".join(
                    f"{k}: {v}"
                    for k, v in p.publer_job_ids.items()
                    if not k.endswith("_url")
                ) or None,
            ),
        }
        if not only_status:
            wanted.update(
                {
                    "Title": ("title", p.title or p.topic),
                    "Name": ("title", p.title or p.topic),
                    "Content Type": ("select", _content_type_label(p.content_type)),
                    "Audio Mode": ("select", p.audio_mode.value.replace("_", " ").title() if p.audio_mode else None),
                    "Platforms": ("multi_select", p.target_platforms),
                    "Topic": ("rich_text", p.topic),
                    "Body": ("rich_text", _trunc(p.body)),
                    "Article Body": ("rich_text", _trunc(p.body)),
                    "Description": ("rich_text", _trunc(p.body)),
                    "Created At": ("date", p.created_at.isoformat()),
                    "Cost USD": ("number", p.total_cost),
                    "Models Used": ("multi_select", p.models_used),
                    "Image URLs": ("rich_text", _trunc("\n".join(a.url for a in p.images))),
                    "Video URL": ("url", p.video.url if p.video else None),
                    # Per-platform caption columns (Twitter / Instagram / ...) intentionally
                    # dropped — under the unified-caption mandate every platform gets the same
                    # body, so 7 columns of "same body truncated 7 different ways" was just
                    # noise. The structured Post Links + Platforms Posted/Failed columns
                    # already capture which platforms actually shipped and at what URL.
                }
            )
        out: dict = {}
        for name, (kind, value) in wanted.items():
            if name not in schema:
                continue
            if value is None or value == "" or value == []:
                continue
            schema_kind = schema[name].get("type")
            if schema_kind != kind:
                # property exists but as a different type — skip rather than 400
                log.debug(f"  skip {name}: schema type {schema_kind} != wanted {kind}")
                continue
            out[name] = _format_property(kind, value)
        return out

    def _build_children(self, p: ContentPiece) -> list:
        # Body paragraphs only. Media blocks are appended exclusively by
        # update_assets() so the early-archive + recovery + update_assets
        # sequence can never duplicate slides on the page.
        blocks: list[dict] = []
        if p.body:
            for chunk in _chunked(p.body, NOTION_TEXT_LIMIT):
                blocks.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": chunk}}]
                        },
                    }
                )
        return blocks


def _content_type_label(ct: ContentType) -> str:
    return {
        ContentType.ARTICLE: "Article",
        ContentType.LONG_ARTICLE: "Long Article",
        ContentType.CAROUSEL: "Carousel",
        ContentType.SHORT_VIDEO: "Short-form Video",
        ContentType.LONG_VIDEO: "Long-form Video",
    }[ct]


def _humanize_status(status: str) -> str:
    return status.replace("_", " ").title()


def _trunc(s: Optional[str], limit: int = NOTION_TEXT_LIMIT) -> Optional[str]:
    if not s:
        return s
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _chunked(s: str, size: int):
    for i in range(0, len(s), size):
        yield s[i : i + size]


def _format_property(kind: str, value: Any) -> dict:
    if kind == "title":
        return {"title": [{"text": {"content": str(value)[:200]}}]}
    if kind == "rich_text":
        return {"rich_text": [{"text": {"content": str(value)}}]}
    if kind == "select":
        return {"select": {"name": str(value)}}
    if kind == "multi_select":
        return {"multi_select": [{"name": str(v)} for v in value]}
    if kind == "date":
        return {"date": {"start": value}}
    if kind == "number":
        return {"number": value}
    if kind == "url":
        return {"url": value}
    raise ValueError(f"unknown property kind: {kind}")
