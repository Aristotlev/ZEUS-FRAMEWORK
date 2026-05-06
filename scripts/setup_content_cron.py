#!/usr/bin/env python3
"""Install Zeus's content-pipeline cron jobs.

Niche-agnostic: reads the topic(s) from ``content_pipeline.niche`` in
``~/.hermes/config.yaml`` so each user / team can configure their own.
Override per-run with ``--niche "a,b,c"``.

Idempotent: removes any existing ``zeus-content-*`` jobs before recreating,
so re-running this updates the prompts/schedules without duplicating.

Creates these jobs:
  1. zeus-content-article-slot   — every 4-6h: generate + post one long-form
     article on the freshest story in your niche.
     Fires at 04:00, 08:00, 12:00, 17:00, 21:00, 00:00 (server local time).
  2. zeus-content-carousel-slot  — twice daily (00:30 / 12:30): one carousel.
  3. zeus-content-notion-ideas   — every 30 min: pulls "New" rows from the
     Notion Content Ideas DB through scripts/ingest_ideas.py, distills each
     into a drafted piece in the archive DB. Auto-publishes drafts whose
     "Auto Publish" checkbox is on (chains into job 4).
  4. zeus-content-publish-ready  — every 10 min: ships every archive row
     whose Status is "Ready to Publish" via scripts/publish_from_notion.py.
  5. zeus-content-daily-crawl    — daily 06:00: crawl the day's top headlines
     across the niche, build a content brief, queue stories for the day.

Run from anywhere:
    python scripts/setup_content_cron.py
    python scripts/setup_content_cron.py --niche "ai, machine learning, robotics"

The gateway drives cron — it must be running for jobs to actually fire:
    hermes gateway run                  # foreground
    hermes gateway install              # systemd user service (Linux)
    sudo hermes gateway install --system  # boot-time system service
Inside the production container the entrypoint launches `hermes gateway run`,
so jobs fire as long as the container is up.
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
        f"today (check the Notion archive). Invoke the proper artifact-first "
        f"pipeline via execute_code: "
        f"`python skills/autonomous-ai-agents/multi-agent-content-pipeline/"
        f"scripts/pipeline_test.py --type long_article --auto --publish` so "
        f"the cost ledger (picker + orchestrator + fal image), Notion "
        f"archive, and crash-recovery flow all run. The script handles topic "
        f"selection from current headlines, image generation, Notion archive, "
        f"and Publer publishing — do NOT draft, generate, or publish in your "
        f"own context. Be decisive — no questions."
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
        "Run the Content Ideas ingester via execute_code: "
        "`python skills/autonomous-ai-agents/multi-agent-content-pipeline/"
        "scripts/ingest_ideas.py --once`. The script reads 'New' rows from "
        "the Notion Content Ideas DB, distills each (URL / YouTube / text) "
        "into a drafted archive page, and (when 'Auto Publish' is checked) "
        "flips the archive row to 'Ready to Publish' for the publish-ready "
        "job to ship. If the script reports zero new ideas, exit silently — "
        "no email, no further work. Otherwise email a one-line summary "
        "listing the compiled archive page URLs. Do NOT do any drafting in "
        "your own context — the script handles all generation, archival, "
        "ledger, and notification. Your job is just to invoke it and "
        "summarize."
    )

    publish_ready = (
        "Run the on-demand publisher via execute_code: "
        "`python skills/autonomous-ai-agents/multi-agent-content-pipeline/"
        "scripts/publish_from_notion.py --once`. The script reads archive "
        "rows whose Status is 'Ready to Publish', regenerates any missing "
        "media, ships through Publer, and lets the publish_watcher resolve "
        "permalinks out-of-process. If zero rows are ready, exit silently. "
        "Otherwise email a one-line summary of which pieces were scheduled. "
        "Do NOT draft, regenerate, or modify content in your own context — "
        "the script is the entire pipeline. Your job is just to invoke it "
        "and summarize."
    )

    publish_watcher = (
        "Run the publish watcher via execute_code: "
        "`python skills/autonomous-ai-agents/multi-agent-content-pipeline/"
        "scripts/publish_watcher.py --once`. The script polls Publer for "
        "live permalinks of every scheduled run still in the queue, patches "
        "the Notion archive row AND the per-publish row in the Content "
        "Pipeline DB with real Post URLs, then sends the final 'live' email. "
        "If the queue is empty, exit silently. Otherwise email one line per "
        "resolved run with the platform URLs. Do NOT do any drafting — the "
        "watcher is purely a poll+patch loop."
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
            # Every 30 min — the script no-ops on an empty queue, so
            # frequent polling is cheap and means new rows compile quickly.
            "schedule": "*/30 * * * *",
            "prompt": notion_ideas,
        },
        {
            "name": "zeus-content-publish-ready",
            # Every 10 min — Aris flips a Draft to "Ready to Publish" and
            # within ~10 min Zeus picks it up + ships through Publer.
            "schedule": "*/10 * * * *",
            "prompt": publish_ready,
        },
        {
            "name": "zeus-content-publish-watcher",
            # Every 10 min, offset by 5 min from the publish-ready job so we
            # don't fight for Publer's rate limit. Picks up fresh schedules
            # quickly and patches Notion + sends the "post is LIVE" email.
            "schedule": "5,15,25,35,45,55 * * * *",
            "prompt": publish_watcher,
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
    print("Done. The gateway drives the schedule — it must be running for jobs to fire:")
    print("  hermes gateway run            # foreground (or already running in the prod container)")
    print("  hermes cron status            # confirm the gateway is up + see active jobs")
    print("  hermes cron list              # list registered jobs")
    print("  hermes cron logs <name>       # tail a job's run output")


if __name__ == "__main__":
    main()
