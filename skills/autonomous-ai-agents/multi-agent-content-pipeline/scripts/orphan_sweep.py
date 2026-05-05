#!/usr/bin/env python3
"""
Orphan-sweep for the Zeus content pipeline.

Reads ~/.hermes/zeus_cost_ledger.jsonl and surfaces every run where money was
spent but the pipeline did not complete cleanly:

  * "leaked" runs   — only checkpoint rows exist (process died before the final
                      ledger entry; usually a Notion / network / OOM crash).
  * "failed_status" — final row exists with status in {failed, partial,
                      media_partial}; pipeline knew it failed but still spent.

For each, prints: run_id, ts, topic, status, leaked_cost, artifact_dir, notion
page id, and a directory listing of bytes that survived. Use --json for machine
output, --window-days N to scope.

Usage:
    python3 orphan_sweep.py
    python3 orphan_sweep.py --window-days 30
    python3 orphan_sweep.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from lib import ledger_incomplete_runs, ledger_summary  # noqa: E402


def _list_dir(path: str | None) -> list[tuple[str, int]]:
    if not path:
        return []
    p = pathlib.Path(path)
    if not p.exists() or not p.is_dir():
        return []
    out: list[tuple[str, int]] = []
    for f in sorted(p.iterdir()):
        if f.is_file():
            try:
                out.append((f.name, f.stat().st_size))
            except OSError:
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="List orphaned / leaked Zeus pipeline runs")
    ap.add_argument("--window-days", type=int, default=None, help="Only show runs in the last N days")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of human output")
    args = ap.parse_args()

    rows = ledger_incomplete_runs(window_days=args.window_days)

    if args.json:
        out = []
        for r in rows:
            out.append({**r, "artifact_files": _list_dir(r.get("artifact_dir"))})
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 0

    summary = ledger_summary(window_days=args.window_days)
    window = f"last {args.window_days}d" if args.window_days else "all time"

    print("=" * 70)
    print(f"  Zeus orphan sweep — {window}")
    print("=" * 70)
    print(
        f"  {summary['runs']} clean runs, {summary['leaked_runs']} leaked"
        f" — leaked spend ${summary['leaked_cost_usd']:.4f}"
        f" of ${summary['total_cost_usd']:.4f} total"
    )
    print()

    if not rows:
        print("  No orphaned runs in window. ")
        return 0

    for r in rows:
        print("-" * 70)
        print(f"  {r['ts']}   run_id={r.get('run_id')}   reason={r.get('incomplete_reason')}")
        print(f"  topic:    {r.get('topic')}")
        print(f"  status:   {r.get('status')}")
        print(f"  cost:     ${float(r.get('total_cost_usd') or 0):.4f}")
        print(f"  models:   {', '.join(r.get('models') or [])}")
        notion = r.get("notion_page_id")
        print(f"  notion:   {notion or 'NOT ARCHIVED'}")
        adir = r.get("artifact_dir")
        print(f"  artifact: {adir or 'NONE — bytes lost'}")
        files = _list_dir(adir)
        if files:
            for name, size in files:
                kb = size / 1024.0
                print(f"            • {name}  ({kb:,.1f} KB)")
        elif adir:
            print(f"            (dir empty or missing — bytes likely reaped)")
        urls = r.get("asset_urls") or []
        if urls:
            print(f"  fal urls: {len(urls)} (likely expired):")
            for u in urls:
                print(f"            • {u}")
    print("-" * 70)
    print()
    print("To recover: re-archive surviving local bytes by uploading them to Notion")
    print("manually, or write a one-off using NotionArchive.update_assets() with a")
    print("ContentPiece reconstructed from the ledger row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
