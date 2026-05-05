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

# Default fast model for cron loops. Flagship reasoning models (v4-pro, etc.)
# are wrong for 20-40 turn agent loops — they cold-start, throttle, and timeout.
# Override via content_pipeline.cron_model in config.yaml.
DEFAULT_CRON_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_CRON_PROVIDER = "openrouter"
DEFAULT_CRON_BASE_URL = "https://openrouter.ai/api/v1"


def _load_content_pipeline_section() -> dict:
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
    except Exception:
        return {}
    section = cfg.get("content_pipeline") if isinstance(cfg, dict) else None
    return section if isinstance(section, dict) else {}


def _load_niche_from_config() -> List[str]:
    """Read content_pipeline.niche from ~/.hermes/config.yaml. List or string."""
    raw = _load_content_pipeline_section().get("niche")
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def _load_cron_model() -> tuple:
    """Read content_pipeline.cron_model / provider / base_url from config."""
    section = _load_content_pipeline_section()
    return (
        str(section.get("cron_model") or DEFAULT_CRON_MODEL),
        str(section.get("cron_provider") or DEFAULT_CRON_PROVIDER),
        str(section.get("cron_base_url") or DEFAULT_CRON_BASE_URL),
    )


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
    """Build the three job specs from a list of niche topics.

    Prompts are self-contained — no skill auto-load. The skill SKILL.md
    files were ~32K each and got injected verbatim into every cron run's
    system prompt, blowing TTFT past 5 min on flash models. The agent
    picks up Notion + Publer credentials from env and figures out the
    HTTP calls via execute_code; the prompt only tells it what to do,
    not how (the skill / API docs are accessible via skills_list/skill_view
    if the agent needs them mid-run).
    """
    phrase = _format_niche(niche)

    common_outro = (
        " Use execute_code with the Notion API (env: NOTION_API_KEY, see "
        "~/.hermes/notion_ids.json) and Publer API (env: PUBLER_API_KEY, "
        "PUBLER_WORKSPACE_ID, PUBLER_*_ID per platform). Email summary via "
        "AgentMail (AGENTMAIL_API_KEY). Always include a cost rollup "
        "(image + LLM tokens + API calls) in the email."
    )

    article_slot = (
        f"Generate and publish ONE long-form {phrase} article on the most "
        f"newsworthy story from the past 4-6h. Skip stories already covered "
        f"today (check the Notion archive). Write 1200-2000 words. Generate "
        f"a 16:9 header image. Publish NOW to all configured Publer "
        f"platforms (don't schedule for later). Archive to Notion. "
        f"Be decisive — no questions, no back-and-forth."
        + common_outro
    )

    carousel_slot = (
        f"Generate and publish ONE {phrase} carousel (3-5 portrait slides) on "
        f"a story from the past 4-6h that lends itself to visual breakdown — "
        f"timeline, ranking, or step-by-step. Skip stories already covered "
        f"today (check the Notion archive). Invoke the proper artifact-first "
        f"pipeline via execute_code: "
        f"`python skills/autonomous-ai-agents/multi-agent-content-pipeline/"
        f"scripts/pipeline_test.py --type carousel --topic '<headline>' "
        f"--slides 4 --publish` so the cost ledger, Notion archive, and "
        f"crash-recovery flow all run. Be decisive — no questions."
        + common_outro
    )

    notion_ideas = (
        f"Process new team-submitted {phrase} ideas in the Notion content "
        f"ideas database (entries with no 'processed' tag). For each: "
        f"research, draft 1200-2000 words, generate a header image, "
        f"schedule via Publer at the next available slot in today's "
        f"calendar (08:00/12:00/17:00/21:00/00:00/04:00), then tag the "
        f"Notion entry 'processed' with links. If zero new ideas, exit "
        f"immediately with a one-line email — no generation cost."
        + common_outro
    )

    daily_crawl = (
        f"Build today's {phrase} content brief. Crawl the past 24h headlines "
        f"across the niche, pick the top 6 stories worth long-form coverage, "
        f"write 50-100 word briefs (angle, key facts, why it matters, "
        f"suggested headline), save them to the Notion content ideas "
        f"database tagged 'queued' so article-slot jobs pick them up first. "
        f"Email the brief to the team for editorial review."
        + common_outro
    )

    return [
        {
            "name": "zeus-content-article-slot",
            "schedule": "0 0,4,8,12,17,21 * * *",
            "prompt": article_slot,
        },
        {
            "name": "zeus-content-carousel-slot",
            # User-set: 12:30 PM and 12:30 AM daily (00:30 / 12:30).
            "schedule": "30 0,12 * * *",
            "prompt": carousel_slot,
        },
        {
            "name": "zeus-content-notion-ideas",
            "schedule": "0 7 * * *",
            "prompt": notion_ideas,
        },
        {
            "name": "zeus-content-daily-crawl",
            "schedule": "0 6 * * *",
            "prompt": daily_crawl,
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

    cron_model, cron_provider, cron_base_url = _load_cron_model()

    print(f"Niche: {_format_niche(niche)}")
    print(f"Model: {cron_model}  ({cron_provider})")

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
            deliver="local",
            model=cron_model,
            provider=cron_provider,
            base_url=cron_base_url,
            toolsets=["content_cron"],  # slim 12-tool toolset for fast TTFT
        )
        print(f"Created {spec['name']:35s}  schedule={spec['schedule']:25s}  id={job['id'][:8]}")

    print()
    print("Done. To start firing them:")
    print("  hermes cron daemon       # background scheduler")
    print("  hermes cron list         # verify jobs are registered")
    print("  hermes cron logs <name>  # tail run output")


if __name__ == "__main__":
    main()
