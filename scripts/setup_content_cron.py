#!/usr/bin/env python3
"""Install Zeus's content-pipeline cron jobs.

Idempotent: removes any existing ``zeus-content-*`` jobs before recreating,
so re-running this updates the prompts/schedules without duplicating.

Creates three jobs:
  1. zeus-content-article-slot  — every 4-6h: generate + post one long-form
     article on the freshest finance/crypto/stocks/forex/geopolitics story.
     Fires at 04:00, 08:00, 12:00, 17:00, 21:00, 00:00 (server local time).
  2. zeus-content-notion-ideas  — daily 07:00: process team-submitted ideas
     from the Notion content database, draft articles, schedule via Publer.
  3. zeus-content-daily-crawl   — daily 06:00: crawl the day's top headlines
     across the niche, build a content brief, queue stories for the day.

Run from anywhere:
    python scripts/setup_content_cron.py

The cron daemon must be running for jobs to actually fire:
    hermes cron start         # foreground
    hermes cron daemon        # background
"""

import sys
from pathlib import Path

# Make `cron.jobs` importable regardless of where the script is invoked.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from cron.jobs import create_job, list_jobs, remove_job  # noqa: E402

NICHE = "finance, crypto, stocks, forex, and geopolitics"

ARTICLE_SLOT_PROMPT = f"""\
Generate and publish ONE long-form article on the most newsworthy current
story in {NICHE} from the past 4-6 hours.

Workflow:
1. Web search across the niche for breaking stories ("crypto news today",
   "stock market today", "forex today", "geopolitics today"). Pick the
   single most newsworthy story not already covered today (check the Notion
   archive database to avoid duplicates).
2. Web extract from 2-3 authoritative sources for full context and quotes.
3. Write a 1200-2000 word long-form article: hook, context, analysis,
   market/geopolitical implications, takeaways. Neutral tone, no hype.
4. Generate a 16:9 landscape header image with image_generate.
5. Publish via Publer to LinkedIn (full article), X/Twitter (as a thread),
   and any other configured long-form platforms in the workspace.
6. Save the article + image URL + post URLs + cost rollup to the Notion
   archive database.
7. Email a one-paragraph summary with the platforms posted to and the
   total run cost (image + LLM + Publer API calls).

Use the content-publish-workflow skill for the publishing pipeline.
"""

NOTION_IDEAS_PROMPT = f"""\
Process new team-submitted content ideas from the Notion content ideas
database.

Workflow:
1. Query Notion for entries with status "new" or no "processed" tag.
2. For each idea: read the title, description, any references the team
   member attached.
3. Research the topic via web_search and web_extract for current context.
4. Draft a long-form article (1200-2000 words) blending the team member's
   angle with current data and developments in {NICHE}.
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

DAILY_CRAWL_PROMPT = f"""\
Build today's content brief by crawling the day's top headlines in
{NICHE}.

Workflow:
1. Web search across the full niche: top crypto stories, biggest stock
   movers, forex shifts, geopolitical developments from the past 24h.
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


JOBS = [
    {
        "name": "zeus-content-article-slot",
        "schedule": "0 0,4,8,12,17,21 * * *",
        "prompt": ARTICLE_SLOT_PROMPT,
        "skills": ["content-publish-workflow"],
    },
    {
        "name": "zeus-content-notion-ideas",
        "schedule": "0 7 * * *",
        "prompt": NOTION_IDEAS_PROMPT,
        "skills": ["content-publish-workflow"],
    },
    {
        "name": "zeus-content-daily-crawl",
        "schedule": "0 6 * * *",
        "prompt": DAILY_CRAWL_PROMPT,
        "skills": ["multi-agent-content-pipeline"],
    },
]


def main():
    # Idempotency: nuke existing zeus-content-* jobs so re-running this
    # script reflects whatever's in JOBS without leaving stale duplicates.
    existing = list_jobs()
    removed = 0
    for job in existing:
        if (job.get("name") or "").startswith("zeus-content-"):
            remove_job(job["id"])
            removed += 1
    if removed:
        print(f"Removed {removed} existing zeus-content-* job(s)")

    for spec in JOBS:
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
