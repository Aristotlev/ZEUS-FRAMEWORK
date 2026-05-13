#!/usr/bin/env python3
"""Cron entry: one breaking-news watcher pass.

Polls MarketWatch / Investing.com / InvestingLive RSS + Finnhub general news,
scores fresh headlines, and auto-fires the ARTICLE pipeline for items above
threshold. Prints a one-line JSON summary on stdout.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.breaking_news_watcher import (  # noqa: E402
    ITEM_MAX_AGE_MINUTES,
    MAX_SHIPS_PER_FIRE,
    SCORE_THRESHOLD,
    run_once,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--threshold", type=float, default=SCORE_THRESHOLD)
    p.add_argument("--max-age-minutes", type=int, default=ITEM_MAX_AGE_MINUTES)
    p.add_argument(
        "--max-ships",
        type=int,
        default=MAX_SHIPS_PER_FIRE,
        help="Max ARTICLE pipelines to fire per pass (highest-scoring win).",
    )
    p.add_argument("--dry-run", action="store_true", help="score but do not ship")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    summary = run_once(
        threshold=args.threshold,
        max_age_minutes=args.max_age_minutes,
        max_ships=args.max_ships,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
