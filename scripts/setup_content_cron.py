#!/usr/bin/env python3
"""Install Zeus's content-pipeline cron jobs.

Niche-agnostic: reads the topic(s) from ``content_pipeline.niche`` in
``~/.hermes/config.yaml`` so each user / team can configure their own.
Override per-run with ``--niche "a,b,c"``.

Idempotent: removes any existing ``zeus-content-*`` jobs before recreating,
so re-running this updates the prompts/schedules without duplicating.

Creates 5 cron jobs (publish_watcher runs as an in-memory daemon, not cron):
  1. zeus-content-article-slot   — 6×/day: generate + post one long-form
     article on the freshest story. Fires at 00,04,08,12,17,21 UTC.
  2. zeus-content-carousel-slot  — 2×/day (00:30 / 12:30): one carousel.
  3. zeus-content-notion-ideas   — 1×/day (06:15 UTC): pulls "New" rows
     from the Notion Content Ideas DB and drafts them. Cron is a safety
     net; for urgent ingestion run scripts/ingest_ideas.py --once on demand.
  4. zeus-content-publish-ready  — 1×/day (06:30 UTC): ships archive rows
     flagged "Ready to Publish" AND ensures the publish_watcher daemon is
     alive (via watcher_supervisor.sh). Cron is a safety net for stuck
     rows + post-container-restart daemon revival.
  5. zeus-content-daily-crawl    — 1×/day (06:00 UTC): builds today's brief
     into the Notion Content Ideas DB.

The publish_watcher runs as a self-respawning daemon (started by
watcher_supervisor.sh) — polls Publer in-memory every 30s for permalink
resolution. Faster than the old every-10-min cron, zero agent overhead.

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
        "flips the archive row to 'Ready to Publish'. Runs once a day — "
        "manual idea entry isn't time-sensitive; on-demand invocation "
        "(if you ever need it sooner) is faster than tightening this cron.\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  python {PIPELINE}/ingest_ideas.py --once\n\n"
        "On exit 0 with no work done: exit silently (no email). On exit 0 "
        "with new pages: report the count and archive page IDs in one line."
        + HARD_RULES
    )

    publish_ready = (
        "Daily safety-net for Notion rows the user flipped to 'Ready to "
        "Publish' but that the on-demand publish path didn't catch. ALSO "
        "ensures the publish_watcher daemon is alive (faster permalink "
        "resolution than cron, and zero agent overhead for the 99% of "
        "ticks that previously found nothing to do).\n\n"
        f"COMMANDS (run both in order, this is the entire task):\n"
        f"  bash {PIPELINE}/watcher_supervisor.sh\n"
        f"  python {PIPELINE}/publish_from_notion.py --once\n\n"
        "watcher_supervisor.sh is idempotent — no-op if daemon alive, "
        "respawns it if dead (it self-respawns on python crashes, so this "
        "only matters after a container restart).\n\n"
        "On exit 0 with empty Notion queue: report only the watcher status "
        "(alive / restarted) in one line. On exit 0 with scheduled rows: "
        "report watcher status + run_ids."
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
        f"to the Notion Ideas DB.\n\n"
        f"HARD CONSTRAINT — you MUST NOT invoke any of the following, even "
        f"as a fallback or 'just to draft something':\n"
        f"  • pipeline_test.py (any --type)\n"
        f"  • ingest_ideas.py\n"
        f"  • publish_from_notion.py\n"
        f"  • publish_watcher.py\n"
        f"  • any other script under /opt/zeus/skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/\n"
        f"  • any tool that generates images (fal, gpt-image-2, replicate, etc.)\n"
        f"  • any tool that posts to social media (Publer, native APIs, etc.)\n"
        f"The ONLY external calls allowed are: (1) HTTP GET to RSS feed "
        f"URLs, (2) OpenRouter chat-completions for the briefs, (3) Notion "
        f"REST API to write rows to the Ideas DB. If you find yourself "
        f"reaching for any other tool, ABORT with a non-zero exit code and "
        f"a one-line message — do NOT improvise. Do NOT send a separate "
        f"notification email — the brief is reviewable in Notion."
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
            # Once a day at 06:15 UTC. Manual idea entry isn't time-
            # sensitive; the previous every-30-min cadence ran the agent
            # 48 times/day on an empty queue at $0.01-0.02/tick.
            "schedule": "15 6 * * *",
            "prompt": notion_ideas,
        },
        {
            "name": "zeus-content-publish-ready",
            # Once a day at 06:30 UTC. Doubles as the watcher-daemon health
            # check (supervisor.sh is idempotent — no-op if alive). Replaces
            # the every-10-min agent cron + the every-10-min watcher cron;
            # the watcher now runs as a respawning in-memory daemon, so
            # permalink resolution is FASTER (~30s) while costing zero
            # agent ticks. publish-ready itself is a safety net only —
            # urgent ships should use the on-demand CLI/Discord path.
            "schedule": "30 6 * * *",
            "prompt": publish_ready,
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
