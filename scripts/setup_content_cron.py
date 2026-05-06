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
    """Build the job specs from a list of niche topics.

    Prompts are deliberately narrow: every content-generating job is just
    "run this one command, report the exit code, exit." The agent is
    explicitly forbidden from drafting, calling fal/Notion/Publer, or
    sending emails in its own context. The deterministic Python scripts
    own all of that — they handle the cost ledger, Notion archive,
    per-platform Publer scheduling with explicit timestamps, and the
    unified email rollup. Letting the agent improvise has historically
    caused: bulk-Publer slot rejections, premature `published` emails sent
    before posts went live, and orphaned ledger rows missing artifact_dir
    + phase_durations_ms.
    """
    PIPELINE = "skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts"
    HARD_RULES = (
        "\n\nHARD RULES (violating any = task failure):\n"
        "  • Your ONLY action is the bash command below. Do not write content, "
        "do not call fal / Notion / Publer / OpenRouter / fish.audio / any "
        "other API in your own context. The script owns ALL of that.\n"
        "  • Do NOT send emails yourself. The script's email_notify path "
        "sends the unified rollup with real platform permalinks AFTER "
        "publish_watcher resolves them. Your own emails would lie about "
        "post status (we got burned: an agent emailed 'Status: published' "
        "16 minutes before Publer reported all 4 platforms FAILED).\n"
        "  • Do NOT improvise a topic — the script's `--auto` flag uses "
        "perplexity/sonar-pro to pick a current headline and refuses if no "
        "story <72h old. Trust it.\n"
        "  • If the script exits non-zero, capture stdout+stderr to "
        "~/.hermes/zeus_email_outbox/<timestamp>_cron_error.txt and exit. "
        "Do NOT retry. Do NOT attempt a fallback flow.\n"
        "  • Run the command via execute_code with cwd=/opt/zeus (host) or "
        "/opt/zeus (container). The repo root contains the `skills/` tree.\n"
    )

    article_slot = (
        f"Run ONE deterministic long-form {_format_niche(niche)} article "
        f"pipeline. Topic auto-picked from past-72h headlines; script skips "
        f"already-archived stories.\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  python {PIPELINE}/pipeline_test.py --type long_article --auto --publish\n\n"
        f"On exit 0: report the run_id and total_cost_usd from the last "
        f"line of ~/.hermes/zeus_cost_ledger.jsonl in one line. Done."
        + HARD_RULES
    )

    carousel_slot = (
        f"Run ONE deterministic {_format_niche(niche)} carousel pipeline "
        f"(4 portrait slides). Topic auto-picked from past-72h headlines.\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  python {PIPELINE}/pipeline_test.py --type carousel --auto --slides 4 --publish\n\n"
        f"On exit 0: report the run_id and total_cost_usd from the last "
        f"line of ~/.hermes/zeus_cost_ledger.jsonl in one line. Done."
        + HARD_RULES
    )

    notion_ideas = (
        "Run the Content Ideas ingester. The script reads 'New' rows from "
        "the Notion Content Ideas DB, distills each (URL / YouTube / text) "
        "into a drafted archive page, and (when 'Auto Publish' is checked) "
        "flips the archive row to 'Ready to Publish'.\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  python {PIPELINE}/ingest_ideas.py --once\n\n"
        "On exit 0 with no work done: exit silently (no email). On exit 0 "
        "with new pages: report the count and archive page IDs in one line."
        + HARD_RULES
    )

    publish_ready = (
        "Run the Notion-driven publisher. The script reads archive rows "
        "whose Status is 'Ready to Publish', regenerates any missing media, "
        "and ships per-platform via Publer with explicit scheduled_at "
        "timestamps. publish_watcher resolves permalinks out-of-process.\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  python {PIPELINE}/publish_from_notion.py --once\n\n"
        "On exit 0 with empty queue: exit silently. On exit 0 with "
        "scheduled rows: report run_ids in one line."
        + HARD_RULES
    )

    publish_watcher = (
        "Run the publish watcher (poll Publer for live permalinks, patch "
        "Notion + Content Pipeline DB with real Post URLs, send the final "
        "'live' email via the unified email_notify path).\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  python {PIPELINE}/publish_watcher.py --once\n\n"
        "On empty queue: exit silently. On resolved runs: report one line "
        "per run with platform URLs."
        + HARD_RULES
    )

    daily_crawl = (
        f"Build today's {_format_niche(niche)} content brief by populating "
        f"the Notion Content Ideas DB. The crawler picks 6 stories from the "
        f"past 24h, writes 50-100 word briefs, tags them 'queued'.\n\n"
        f"This job does NOT have a deterministic script yet — you ARE "
        f"allowed to use execute_code to: (a) fetch RSS feeds, (b) call "
        f"OpenRouter for the briefs (gemini-2.5-flash, never v4-pro — this "
        f"is the only exception to the hard rules below), (c) write rows "
        f"to the Notion Ideas DB. Do NOT generate images. Do NOT publish. "
        f"Do NOT send a separate notification email — the brief is "
        f"reviewable in Notion."
        + HARD_RULES.replace(
            "Your ONLY action is the bash command below.",
            "Your ONLY action is to populate the Notion Ideas DB with 6 briefs.",
        )
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
