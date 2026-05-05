# Content Cron Jobs

Three idempotent cron jobs ship with Zeus. Set your niche once, run the setup script, and Zeus runs the [content pipeline](content-pipeline.md) on schedule.

## The jobs

| Job | Schedule | What it does |
|---|---|---|
| `zeus-content-article-slot` | every 4–6h (04:00 / 08:00 / 12:00 / 17:00 / 21:00 / 00:00 server local) | Research + draft + publish a long-form article on the freshest niche story |
| `zeus-content-notion-ideas` | daily 07:00 | Process team-submitted ideas from the Notion content database, draft articles, schedule via Publer |
| `zeus-content-daily-crawl` | daily 06:00 | Crawl the day's headlines across the niche, build a 6-story content brief, queue stories for the day |

A separate carousel job runs at 00:30 + 12:30 if you have carousel mode enabled.

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

## Why a fast model for cron

The cron loops are 20–40 turn agent runs. Flagship reasoning models (`deepseek-v4-pro` etc.) cold-start, throttle, and timeout in those loops. Defaults in [`scripts/setup_content_cron.py`](../scripts/setup_content_cron.py) point at `deepseek/deepseek-v4-flash` for this reason. Override per-job via `content_pipeline.cron_model` in `config.yaml`.

## Removing jobs

```bash
hermes cron list
hermes cron remove zeus-content-article-slot
```

The setup script also removes any existing `zeus-content-*` jobs before recreating, so re-running it is safe.
