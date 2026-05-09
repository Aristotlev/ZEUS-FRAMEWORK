#!/usr/bin/env python3
"""Install Zeus's content-pipeline cron jobs.

Niche-agnostic: reads the topic(s) from ``content_pipeline.niche`` in
``~/.hermes/config.yaml`` so each user / team can configure their own.
Override per-run with ``--niche "a,b,c"``.

Idempotent: removes any existing ``zeus-content-*`` jobs before recreating,
so re-running this updates the prompts/schedules without duplicating.

Active cron jobs (2026-05-08 — pruned to one stream, will re-expand):
  1. zeus-content-article-slot   — 12×/day on "0 */2 * * *": generate +
     post one long-form article on the freshest story.

Other job builders (carousel-slot, notion-ideas, publish-ready,
daily-crawl) are kept in ``_build_jobs`` but excluded from the return
list. To re-enable any of them, add their dict back to the returned
list and re-run this script — it nukes existing jobs first, so the
diff is reflected exactly.

The publish_watcher runs as a self-respawning daemon (started by
watcher_supervisor.sh from the entrypoint) — polls Publer in-memory
every 30s for permalink resolution. Independent of the cron list.

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
    # Absolute path to the venv interpreter that has all pipeline deps
    # (requests, fal-client, notion-client, etc.). The prod container's
    # `/usr/bin/python3` lacks them, and bare `python` isn't on PATH at
    # all, so a manual `docker exec` rerun of these commands would fail
    # without this. Cron-fired runs go through the gateway's venv-aware
    # shell so they worked either way, but pinning makes both paths match.
    PYTHON = "/opt/hermes/.venv/bin/python"
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
        f"  {PYTHON} {PIPELINE}/pipeline_test.py --type long_article --auto --publish\n\n"
        f"On exit 0: report the run_id and total_cost_usd from the last "
        f"line of ~/.hermes/zeus_cost_ledger.jsonl in one line. Done."
        + HARD_RULES
    )

    carousel_slot = (
        f"Run ONE deterministic {_format_niche(niche)} carousel pipeline "
        f"(4 portrait slides). Topic auto-picked from past-72h headlines.\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  {PYTHON} {PIPELINE}/pipeline_test.py --type carousel --auto --slides 4 --publish\n\n"
        f"On exit 0: report the run_id and total_cost_usd from the last "
        f"line of ~/.hermes/zeus_cost_ledger.jsonl in one line. Done."
        + HARD_RULES
    )

    notion_ideas = (
        "Run the Content Ideas ingester. The script reads 'New' rows from "
        "the Notion Content Ideas DB, distills each input (URL / YouTube / "
        "text / attached photos / attached PDF / attached video) into a "
        "drafted archive page that matches the row's Target Type (Article / "
        "Long Article / Carousel / Short Video / Long Video), then (when "
        "'Auto Publish' is checked, default ON) flips the archive row to "
        "'Ready to Publish' for the publish_watcher to ship. Runs once a "
        "day at 06:00 UTC; idea drops aren't time-sensitive.\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  {PYTHON} {PIPELINE}/ingest_ideas.py --once\n\n"
        "On exit 0 with no work done: exit silently (no email). On exit 0 "
        "with new pages: report the count and archive page IDs in one line."
        + HARD_RULES
    )

    weekly_analytics = (
        "Run the weekly Publer-analytics rollup. The script pulls last-7-day "
        "post insights from every connected social account, asks DeepSeek V4 "
        "Pro to write a 'what's working / why / patterns' analysis, writes "
        "one row to the Notion Weekly Analytics DB (auto-created on first "
        "run), and emails the report via the same backend rail as the per-"
        "post pipeline.\n\n"
        f"COMMAND (this is the entire task):\n"
        f"  {PYTHON} {PIPELINE}/weekly_analytics.py\n\n"
        "On exit 0: report the run_id and total_reach from the JSON line on "
        "stdout in one line. On non-zero exit, capture stdout+stderr to "
        "~/.hermes/zeus_email_outbox/<timestamp>_weekly_error.txt and exit."
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
        f"  {PYTHON} {PIPELINE}/publish_from_notion.py --once\n\n"
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

    # Active jobs (2026-05-09 — pruned to two):
    #   - article-slot:  every-2h auto-pick + post (long_article, main loop)
    #   - ideas-ingest:  once a day at 06:00 UTC. Drains anything the user
    #     dumped into the Notion Content Ideas DB (URL/file/PDF/etc) into
    #     the chosen Target Type. Once-daily is enough — drops are user-
    #     paced, not time-sensitive.
    # publish_watcher (the daemon, NOT a cron) handles permalink
    # resolution every 30s in-memory; it's started by the entrypoint via
    # watcher_supervisor.sh and self-respawns. carousel_slot and daily_crawl
    # stay defined above for easy re-enable.
    _ = (carousel_slot, daily_crawl)  # keep refs

    return [
        {
            "name": "zeus-content-article-slot",
            # Every 6h (00, 06, 12, 18 UTC = 4×/day). Publer's trial plan
            # silently throttles non-Twitter publishes to ~4/day/platform —
            # any 12×/day cadence had 8 of those posts shadow-dropped on
            # FB/IG/LI/TT/YT and the matching gen costs (gpt-image-2 + LLM)
            # were burned for nothing. 4×/day matches the throttle ceiling.
            "schedule": "0 */6 * * *",
            "prompt": article_slot,
        },
        {
            "name": "zeus-content-ideas-ingest",
            # 06:00 UTC daily. Catches whatever the user dropped into the
            # Notion Ideas DB during the prior 24h. Exits silently on
            # empty queue — zero cost when nothing to do.
            "schedule": "0 6 * * *",
            "prompt": notion_ideas,
        },
        {
            "name": "zeus-content-publish-ready",
            # Every 10 min. Drains any archive rows the user manually flipped
            # to "Ready to Publish" — the only automated recovery path for
            # posts that Publer's trial throttle silently dropped (no API
            # error fires, watcher can't auto-detect). Also keeps the
            # publish_watcher daemon alive (idempotent supervisor restart).
            # Exits silently on empty queue, so 144 ticks/day at near-zero
            # cost is acceptable.
            "schedule": "*/10 * * * *",
            "prompt": publish_ready,
        },
        {
            "name": "zeus-content-weekly-analytics",
            # Sunday 17:00 UTC = 20:00 Europe/Athens (EEST, UTC+3). Picks up
            # the full Mon-Sun week and emails the analysis ahead of Monday's
            # planning window.
            "schedule": "0 17 * * 0",
            "prompt": weekly_analytics,
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
