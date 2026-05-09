# Content Cron Jobs

Four idempotent cron jobs ship with Zeus. Set your niche once, run the setup script, and Zeus runs the [content pipeline](content-pipeline.md) on schedule.

## The jobs

| Job | Schedule | What it does |
|---|---|---|
| `zeus-content-article-slot` | every 2h on the hour (12×/day, UTC) | Auto-pick a fresh story (past-72h headline), draft a long-form article, publish across Twitter / IG / LinkedIn / TikTok / Facebook |
| `zeus-content-ideas-ingest` | daily 06:00 UTC | Drain the Notion Ideas DB — distill each new row (URL / YouTube / text / PDF / photo / video) into the chosen content type and queue for publish |
| `zeus-content-publish-ready` | every 10 min | Drain any archive rows the user manually flipped to "Ready to Publish" in Notion. Also keeps the `publish_watcher` daemon alive (idempotent supervisor restart) |
| `zeus-content-weekly-analytics` | Sunday 17:00 UTC | Pull last-7-day Publer post insights, generate a "what's working / why / patterns" analysis, write a row to the Notion Weekly Analytics DB, email the report |

`carousel_slot` and `daily_crawl` job builders remain defined in [`scripts/setup_content_cron.py`](../scripts/setup_content_cron.py) for easy re-enable but are not in the active list. Add them back to the returned list and re-run the setup script — it nukes existing `zeus-content-*` jobs first, so the diff is reflected exactly.

## Setup

1. Set your niche in `~/.hermes/config.yaml`:
   ```yaml
   content_pipeline:
     niche: [ai, machine-learning, robotics]
     # or: niche: "ai research"
   ```

2. Run the setup script (idempotent — re-runs reset the jobs without duplicating):
   ```bash
   python scripts/setup_content_cron.py
   ```

   Override the niche per-run:
   ```bash
   python scripts/setup_content_cron.py --niche "ai, machine learning, robotics"
   ```

3. Start the Hermes cron daemon:
   ```bash
   hermes cron start    # foreground
   hermes cron daemon   # background
   ```

   In production, the entrypoint launches `hermes gateway run` and re-runs `setup_content_cron.py` on every container boot — schedule/prompt edits auto-apply on `git pull && docker restart`.

## The publish_watcher daemon (separate from cron)

`publish_watcher` is a long-running Python daemon — **not a cron job** — that polls Publer every ~30s in-memory, resolves real platform permalinks, updates the Notion archive, and fires the unified email rollup once all platforms report a state.

- Started by the prod entrypoint via `watcher_supervisor.sh`
- Self-respawns on Python crashes
- The `publish-ready` cron's first command (`watcher_supervisor.sh`) is a no-op when the daemon is healthy and respawns it if dead

## Catch-up after outages

`scripts/cron_catchup.sh` runs on container boot and fires backfill runs for any content slot whose most recent ledger entry is older than the slot's max gap (e.g. `0 */2 * * *` → max gap 2h, grace 3h). This recovers from gateway crashes or container restarts that spanned a slot boundary.

## Why a fast model for cron

The cron loops are 20–40 turn agent runs. Flagship reasoning models (`deepseek-v4-pro` etc.) cold-start, throttle, and timeout in those loops. Defaults in [`scripts/setup_content_cron.py`](../scripts/setup_content_cron.py) point at `deepseek/deepseek-v4-flash` for this reason. Override per-job via `content_pipeline.cron_model` in `config.yaml`.

## Removing jobs

```bash
hermes cron list
hermes cron remove zeus-content-article-slot
```

The setup script also removes any existing `zeus-content-*` jobs before recreating, so re-running it is safe.
