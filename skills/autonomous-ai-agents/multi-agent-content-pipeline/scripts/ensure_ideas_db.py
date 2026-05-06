#!/usr/bin/env python3
"""
ensure_ideas_db — locate or create the "Content Ideas" Notion DB under the
content-hub page, then ensure every column the ingester needs is present.

Workflow:
  1. Walks children of ZEUS_NOTION_HUB_PAGE_ID looking for a child_database
     whose title contains "ideas" (case-insensitive).
  2. If found, audits the schema and PATCHes any missing properties.
  3. If not found, creates a new database titled "Content Ideas" with the
     full schema in one shot.
  4. Caches the resolved DB id to ~/.hermes/notion_ids.json under
     `ideas_db_id` so subsequent ingester runs skip the lookup.

Idempotent: safe to re-run. Pre-existing columns are NEVER overwritten —
only missing properties are added.

Usage:
    export $(grep -v '^#' ~/.hermes/.env | xargs)
    python scripts/ensure_ideas_db.py
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys

import requests

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from lib import NotionArchive  # noqa: E402
from lib.paths import zeus_data_path  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("ensure-ideas-db")

NOTION_API = "https://api.notion.com/v1"
CONFIG_PATH = zeus_data_path("notion_ids.json")


_SOURCE_TYPE_OPTIONS = [
    {"name": "Auto"},
    {"name": "URL"},
    {"name": "YouTube"},
    {"name": "Text"},
]

_TARGET_TYPE_OPTIONS = [
    {"name": "Auto"},
    {"name": "Article"},
    {"name": "Long Article"},
    {"name": "Carousel"},
    {"name": "Short-form Video"},
    {"name": "Long-form Video"},
]

_STATUS_OPTIONS = [
    {"name": "New"},
    {"name": "Processing"},
    {"name": "Compiled"},
    {"name": "Skipped"},
    {"name": "Failed"},
]

# `Title` is provided automatically by Notion as the primary title column.
WANTED_PROPERTIES: dict[str, dict] = {
    "Source": {"rich_text": {}},
    "Source Type": {"select": {"options": _SOURCE_TYPE_OPTIONS}},
    "Target Type": {"select": {"options": _TARGET_TYPE_OPTIONS}},
    "Status": {"select": {"options": _STATUS_OPTIONS}},
    "Notes": {"rich_text": {}},
    "Compiled Page": {"url": {}},
    "Auto Publish": {"checkbox": {}},
    # Per-row media knobs. Empty cell -> ingester defaults (4 slides, 5s video).
    "Slides": {"number": {}},
    "Video Duration": {"number": {}},
    "Processed At": {"date": {}},
    "Created": {"created_time": {}},
}


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            import json

            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_config(cfg: dict) -> None:
    import json

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _list_hub_databases(archive: NotionArchive) -> list[dict]:
    r = requests.get(
        f"{NOTION_API}/blocks/{archive.hub_page_id}/children?page_size=100",
        headers=archive.headers,
        timeout=15,
    )
    r.raise_for_status()
    return [b for b in r.json().get("results", []) if b.get("type") == "child_database"]


def _find_ideas_db(archive: NotionArchive) -> str | None:
    for b in _list_hub_databases(archive):
        title = (b.get("child_database") or {}).get("title", "").lower()
        if "idea" in title:
            log.info(f"  matched ideas DB by title: {title!r} -> {b['id']}")
            return b["id"]
    return None


def _create_ideas_db(archive: NotionArchive) -> str:
    log.info(f"creating new 'Content Ideas' DB under hub page {archive.hub_page_id}")
    body = {
        "parent": {"type": "page_id", "page_id": archive.hub_page_id},
        "title": [{"type": "text", "text": {"content": "Content Ideas"}}],
        "properties": {
            "Title": {"title": {}},
            **WANTED_PROPERTIES,
        },
    }
    r = requests.post(
        f"{NOTION_API}/databases", headers=archive.headers, json=body, timeout=20,
    )
    if r.status_code >= 400:
        log.error(f"create DB failed {r.status_code}: {r.text[:500]}")
        r.raise_for_status()
    db_id = r.json()["id"]
    log.info(f"  created ideas DB {db_id}")
    return db_id


def _audit_schema(archive: NotionArchive, db_id: str) -> None:
    r = requests.get(
        f"{NOTION_API}/databases/{db_id}", headers=archive.headers, timeout=15
    )
    r.raise_for_status()
    schema = r.json().get("properties", {})
    log.info(f"existing properties: {sorted(schema.keys())}")

    missing: dict[str, dict] = {}
    type_conflicts: list[str] = []
    for name, spec in WANTED_PROPERTIES.items():
        if name in schema:
            existing_kind = schema[name].get("type")
            wanted_kind = next(iter(spec))
            if existing_kind != wanted_kind:
                type_conflicts.append(
                    f"property {name!r} exists as type {existing_kind!r} but ingester writes "
                    f"{wanted_kind!r}; leaving as-is"
                )
            continue
        missing[name] = spec

    if missing:
        log.info(f"adding {len(missing)} column(s): {sorted(missing.keys())}")
        r = requests.patch(
            f"{NOTION_API}/databases/{db_id}",
            headers=archive.headers,
            json={"properties": missing},
            timeout=20,
        )
        if r.status_code >= 400:
            log.error(f"PATCH failed {r.status_code}: {r.text[:500]}")
            r.raise_for_status()
        log.info("schema updated.")
    else:
        log.info("schema already in shape — nothing to add")

    for c in type_conflicts:
        log.warning(c)


def main() -> int:
    archive = NotionArchive()
    log.info(f"hub page: {archive.hub_page_id}")

    cfg = _load_config()
    db_id = cfg.get("ideas_db_id")
    if db_id:
        # Verify it still exists; fall through to discover/create if not.
        r = requests.get(
            f"{NOTION_API}/databases/{db_id}", headers=archive.headers, timeout=15
        )
        if r.status_code == 200:
            log.info(f"using cached ideas DB id: {db_id}")
        else:
            log.warning(f"cached ideas DB id {db_id} no longer accessible — re-discovering")
            db_id = None

    if not db_id:
        db_id = _find_ideas_db(archive)
    if not db_id:
        db_id = _create_ideas_db(archive)
        cfg["ideas_db_id"] = db_id
        _save_config(cfg)
    elif cfg.get("ideas_db_id") != db_id:
        cfg["ideas_db_id"] = db_id
        _save_config(cfg)

    _audit_schema(archive, db_id)
    log.info(f"\nideas DB ready: {db_id}")
    log.info("paste an idea (URL / YouTube link / plain text) into the Source column,")
    log.info("set Status=New, then run scripts/ingest_ideas.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
