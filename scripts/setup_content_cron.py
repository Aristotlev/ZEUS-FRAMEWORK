#!/usr/bin/env python3
"""Install Zeus's content-pipeline cron jobs.

Niche-agnostic: reads the topic(s) from ``content_pipeline.niche`` in
``~/.hermes/config.yaml`` so each user / team can configure their own.
Override per-run with ``--niche "a,b,c"``.

Idempotent: removes any existing ``zeus-content-*`` jobs before recreating,
so re-running this updates the prompts/schedules without duplicating.

Creates three jobs:
  1. zeus-content-article-slot  — every 4-6h: generate + post one long-form
     article on the freshest story in your niche.
     Fires at 04:00, 08:00, 12:00, 17:00, 21:00, 00:00 (server local time).
  2. zeus-content-notion-ideas  — daily 07:00: process team-submitted ideas
     from the Notion content database, draft articles, schedule via Publer.
  3. zeus-content-daily-crawl   — daily 06:00: crawl the day's top headlines
     across the niche, build a content brief, queue stories for the day.

Run from anywhere:
    python scripts/setup_content_cron.py
    python scripts/setup_content_cron.py --niche "ai, machine learning, robotics"

The cron daemon must be running for jobs to actually fire:
    hermes cron start         # foreground
    hermes cron daemon        # background
"""

import argparse
import sys
from pathlib import Path
from typing import List

# Make `cron.jobs` and `hermes_cli.config` importable regardless of where
# the script is invoked.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from cron.jobs import create_job, list_jobs, remove_job  # noqa: E402


def _load_niche_from_config() -> List[str]:
    """Read content_pipeline.niche from ~/.hermes/config.yaml. List or string."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
    except Exception:
        return []

    section = cfg.get("content_pipeline") if isinstance(cfg, dict) else None
    if not isinstance(section, dict):
        return []
    raw = section.get("niche")
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def _format_niche(niche: List[str]) -> str:
    """Render a niche list as a readable phrase: 'a, b, and c'."""
    if not niche:
        return ""
    if len(niche) == 1:
        return niche[0]
    if len(niche) == 2:
        return f"{niche[0]} and {niche[1]}"
    return ", ".join(niche[:-1]) + f", and {niche[-1]}"


def _resolve_niche(cli_override: str = "") -> List[str]:
    if cli_override:
        return [t.strip() for t in cli_override.split(",") if t.strip()]
    return _load_niche_from_config()

def _build_jobs(niche: List[str]):
    """Build the three job specs from a list of niche topics."""
    phrase = _format_niche(niche)
    # Search query examples derived from the configured niche, e.g.
    # ["crypto news today", "stocks news today", ...]
    search_examples = ", ".join(f'"{t} news today"' for t in niche[:4])

    article_slot = f"""\
Generate and publish ONE long-form article on the most newsworthy current
story in {phrase} from the past 4-6 hours.

Workflow:
1. Web search across the niche for breaking stories (e.g. {search_examples}).
   Pick the single most newsworthy story not already covered today (check
   the Notion archive database to avoid duplicates).
2. Web extract from 2-3 authoritative sources for full context and quotes.
3. Write a 1200-2000 word long-form article: hook, context, analysis,
   implications, takeaways. Neutral tone, no hype.
4. Generate a 16:9 landscape header image with image_generate.
5. Publish via Publer to LinkedIn (full article), X/Twitter (as a thread),
   and any other configured long-form platforms in the workspace.
6. Save the article + image URL + post URLs + cost rollup to the Notion
   archive database.
7. Email a one-paragraph summary with the platforms posted to and the
   total run cost (image + LLM + Publer API calls).

Use the content-publish-workflow skill for the publishing pipeline.
"""

    notion_ideas = f"""\
Process new team-submitted content ideas from the Notion content ideas
database.

Workflow:
1. Query Notion for entries with status "new" or no "processed" tag.
2. For each idea: read the title, description, any references the team
   member attached.
3. Research the topic via web_search and web_extract for current context.
4. Draft a long-form article (1200-2000 words) blending the team member's
   angle with current data and developments in {phrase}.
5. Generate a 16:9 header image.
6. Schedule via Publer at the next available slot in today's content
   calendar (08:00, 12:00, 17:00, 21:00, 00:00, 04:00).
7. Update the Notion entry: status → "processed", attach the scheduled
   post URL, the article text, the image URL, and the slot time.
8. Email a summary listing each idea processed, who submitted it, and
   when it's scheduled. Include total cost rollup.

If there are no new ideas, exit cleanly with a one-line email saying so
(no cost spent on generation).

Use the content-publish-workflow skill.
"""

    daily_crawl = f"""\
Build today's content brief by crawling the day's top headlines in
{phrase}.

Workflow:
1. Web search across the full niche for the past 24h, one query per topic
   (e.g. {search_examples}).
2. Identify the 6 most newsworthy stories worth long-form coverage today.
3. For each story, write a 50-100 word brief: angle, key facts, why it
   matters, suggested headline.
4. Save the brief as a single Notion page in the content ideas database
   tagged "daily-crawl-{{date}}", with each story as a separate row tagged
   "queued" so the article-slot jobs can pick them up first instead of
   re-crawling.
5. Email the daily content brief to the team with all 6 story summaries
   so editors can override or add to the queue manually.

Use the multi-agent-content-pipeline skill for the planning + brief stage.
"""

    return [
        {
            "name": "zeus-content-article-slot",
            "schedule": "0 0,4,8,12,17,21 * * *",
            "prompt": article_slot,
            "skills": ["content-publish-workflow"],
        },
        {
            "name": "zeus-content-notion-ideas",
            "schedule": "0 7 * * *",
            "prompt": notion_ideas,
            "skills": ["content-publish-workflow"],
        },
        {
            "name": "zeus-content-daily-crawl",
            "schedule": "0 6 * * *",
            "prompt": daily_crawl,
            "skills": ["multi-agent-content-pipeline"],
        },
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--niche",
        default="",
        help="Comma-separated topics (overrides content_pipeline.niche in config.yaml)",
    )
    args = parser.parse_args()

    niche = _resolve_niche(args.niche)
    if not niche:
        print(
            "ERROR: no niche configured.\n\n"
            "Set it in ~/.hermes/config.yaml:\n"
            "  content_pipeline:\n"
            "    niche: [your, topics, here]\n\n"
            "Or pass --niche \"a, b, c\" for a one-off run.",
            file=sys.stderr,
        )
        sys.exit(2)

    print(f"Niche: {_format_niche(niche)}")

    # Idempotency: nuke existing zeus-content-* jobs so re-running this
    # script reflects the current niche/prompts without leaving duplicates.
    existing = list_jobs()
    removed = 0
    for job in existing:
        if (job.get("name") or "").startswith("zeus-content-"):
            remove_job(job["id"])
            removed += 1
    if removed:
        print(f"Removed {removed} existing zeus-content-* job(s)")

    for spec in _build_jobs(niche):
        job = create_job(
            name=spec["name"],
            schedule=spec["schedule"],
            prompt=spec["prompt"],
            skills=spec["skills"],
            deliver="local",
        )
        print(f"Created {spec['name']:35s}  schedule={spec['schedule']:25s}  id={job['id'][:8]}")

    print()
    print("Done. To start firing them:")
    print("  hermes cron daemon       # background scheduler")
    print("  hermes cron list         # verify jobs are registered")
    print("  hermes cron logs <name>  # tail run output")


if __name__ == "__main__":
    main()
