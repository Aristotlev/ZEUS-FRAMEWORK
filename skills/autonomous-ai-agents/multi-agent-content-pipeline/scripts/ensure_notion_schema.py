#!/usr/bin/env python3
"""
ensure_notion_schema — add the structured per-platform columns to the Zeus
archive Notion DB so the pipeline's `_build_properties` writes don't no-op.

Run once after pulling the carousel-cleanup update. Idempotent: re-running
just confirms the schema is already in shape.

Adds (if missing):
  - Post Links         rich_text     clean `platform: ✓ <url>` / `✗ FAILED` block
  - Platforms Posted   multi_select  platforms with a real https permalink
  - Platforms Failed   multi_select  platforms that returned FAILED

Pre-existing columns are NEVER touched. The Notion API's
`PATCH /databases/{id}` only operates on the property names you pass.

Usage:
    export $(grep -v '^#' ~/.hermes/.env | xargs)
    python scripts/ensure_notion_schema.py
"""
from __future__ import annotations

import logging
import pathlib
import sys

import requests

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from lib import NotionArchive  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("ensure-notion-schema")

NOTION_API = "https://api.notion.com/v1"

# Pre-seed the multi_select with every provider Publer supports for this
# pipeline so writes never silently drop an unknown option. Notion will
# create options on-demand anyway, but seeding gets stable colors and means
# the column is usable from the Notion UI even before the first write.
_PLATFORM_OPTIONS = [
    {"name": "twitter"},
    {"name": "instagram"},
    {"name": "linkedin"},
    {"name": "tiktok"},
    {"name": "youtube"},
    {"name": "facebook"},
    {"name": "reddit"},
]

_CONTENT_TYPE_OPTIONS = [
    {"name": "Article"},
    {"name": "Long Article"},
    {"name": "Carousel"},
    {"name": "Short-form Video"},
    {"name": "Long-form Video"},
]

_AUDIO_MODE_OPTIONS = [
    {"name": "Music Only"},
    {"name": "Music Narration"},
    {"name": "Narration Primary"},
]

_STATUS_OPTIONS = [
    {"name": "Draft"},
    # User-flipped trigger: publish_from_notion.py picks rows in this state and
    # ships them. The script flips the row to "Publishing" before doing work,
    # then publish() leaves it in "Scheduled" / "Posted" / "Failed".
    {"name": "Ready to Publish"},
    {"name": "Publishing"},
    {"name": "Scheduled"},
    {"name": "Media Generated"},
    {"name": "Media Partial"},
    {"name": "Posted"},
    {"name": "Partial"},
    {"name": "Failed"},
]

# Full canonical schema the pipeline writes to. Every property the pipeline's
# `_build_properties` and `update_assets` paths emit. Existing columns with the
# same name are preserved (the script never overwrites). Conflicting legacy
# columns (e.g. you have `Cost` number but pipeline writes `Cost USD`) are
# flagged in the audit summary at the end so you can rename/delete by hand.
WANTED_PROPERTIES = {
    # Carousel-cleanup additions (the original reason for this script)
    "Post Links": {"rich_text": {}},
    "Platforms Posted": {"multi_select": {"options": _PLATFORM_OPTIONS}},
    "Platforms Failed": {"multi_select": {"options": _PLATFORM_OPTIONS}},
    # Core run metadata
    "Topic": {"rich_text": {}},
    "Body": {"rich_text": {}},
    "Content Type": {"select": {"options": _CONTENT_TYPE_OPTIONS}},
    "Audio Mode": {"select": {"options": _AUDIO_MODE_OPTIONS}},
    "Platforms": {"multi_select": {"options": _PLATFORM_OPTIONS}},
    "Posted At": {"date": {}},
    "Run ID": {"rich_text": {}},
    "Local Artifact Dir": {"rich_text": {}},
    # Cost + media
    "Cost USD": {"number": {"format": "dollar"}},
    "Cost Breakdown": {"rich_text": {}},
    "Models Used": {"multi_select": {}},
    "Image URLs": {"rich_text": {}},
    "Video URL": {"url": {}},
    # Publer plumbing — clean (URLs / errors live in Post Links instead)
    "Job IDs": {"rich_text": {}},
    # Status — only added if missing; existing Status (any type) is left alone.
    "Status": {"select": {"options": _STATUS_OPTIONS}},
}


_LEGACY_NAME_HINTS = {
    # legacy column name -> (canonical pipeline name, what the user can do)
    "Cost":  ("Cost USD",     "rename to 'Cost USD' or delete; pipeline writes 'Cost USD' (number, dollar format)"),
    "Type":  ("Content Type", "rename to 'Content Type' or delete; pipeline writes 'Content Type' (select)"),
    "Model": ("Models Used",  "rename to 'Models Used' or delete; pipeline writes 'Models Used' (multi_select)"),
}


def main() -> int:
    archive = NotionArchive()
    db_id = archive.archive_db_id
    schema = archive._get_db_schema()
    log.info(f"archive DB: {db_id}")
    log.info(f"existing properties: {sorted(schema.keys())}")

    missing: dict[str, dict] = {}
    type_conflicts: list[str] = []
    for name, spec in WANTED_PROPERTIES.items():
        if name in schema:
            existing_kind = schema[name].get("type")
            wanted_kind = next(iter(spec))
            if existing_kind != wanted_kind:
                msg = (
                    f"property {name!r} exists as type {existing_kind!r} but pipeline writes "
                    f"{wanted_kind!r}; leaving as-is. Rename or delete manually if you want "
                    f"the new layout."
                )
                log.warning(msg)
                type_conflicts.append(msg)
            continue
        missing[name] = spec

    legacy: list[str] = []
    for col, (canonical, hint) in _LEGACY_NAME_HINTS.items():
        if col in schema and canonical not in schema:
            legacy.append(f"  - '{col}' (legacy): {hint}")

    if missing:
        log.info(f"adding {len(missing)} column(s): {sorted(missing.keys())}")
        r = requests.patch(
            f"{NOTION_API}/databases/{db_id}",
            headers=archive.headers,
            json={"properties": missing},
            timeout=20,
        )
        if r.status_code >= 400:
            log.error(f"Notion PATCH failed {r.status_code}: {r.text[:500]}")
            return 1
        log.info("schema updated. Re-fetch to confirm.")
        new_schema = requests.get(
            f"{NOTION_API}/databases/{db_id}", headers=archive.headers, timeout=15
        ).json().get("properties", {})
        log.info(f"properties after patch: {sorted(new_schema.keys())}")
    else:
        log.info("schema already has every column the pipeline needs — nothing to add")

    if type_conflicts:
        log.warning("=== type conflicts (no auto-fix) ===")
        for m in type_conflicts:
            log.warning(f"  {m}")

    if legacy:
        log.warning("=== legacy columns the pipeline doesn't write to ===")
        for line in legacy:
            log.warning(line)
        log.warning(
            "Pipeline data will populate the canonical columns; legacy columns will stay empty "
            "until you rename / delete them manually."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
