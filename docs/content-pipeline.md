# Content Automation Pipeline

Generate professional content across Twitter, Instagram, LinkedIn, TikTok, YouTube, Reddit, and Facebook from a single command. Every run archives to Notion **before** any external API spend, downloads all media locally, appends to a persistent cost ledger, and emails a summary with post links and always-on cost rollups.

## The 5 live content types (+ 2 scaffolded)

| Type | Media | Platforms | Caption | Status |
|---|---|---|---|---|
| **Article** | 1 image (1024×1024) | Twitter, IG, LinkedIn, TikTok | <480 chars (single tweet) | ✅ live |
| **LongArticle** | 1 image (1024×1024) | Twitter (thread), IG, LinkedIn, TikTok | 550–900 chars | ✅ live |
| **Carousel** | 3–5 slide images (1024×1536 portrait) | Twitter, IG, LinkedIn, TikTok | 550–900 chars | ✅ live |
| **ShortVideo** | 1080×1920, <90s | Twitter, IG (reel), LinkedIn, TikTok, YouTube Shorts | mobile-native | ✅ live |
| **LongVideo** | 1920×1080 | YouTube, Twitter, LinkedIn, Reddit | landscape, 16:9 | ✅ live |
| **ShortVideoAvatar** | avatar-driven 1080×1920 | same as ShortVideo | mobile-native | 🔧 scaffolded |
| **LongVideoAvatar** | avatar-driven 1920×1080 | same as LongVideo | landscape | 🔧 scaffolded |

The two `*Avatar` types share the live counterparts' platform map and caption rules but route media through an avatar provider (LoRA fine-tuned per persona). Provider selection is still TBD — the `ContentPiece` plumbing is in place so they generate-and-archive end-to-end as soon as a provider is wired up.

A single `ContentPiece` dataclass (in [`lib/content_types.py`](../skills/autonomous-ai-agents/multi-agent-content-pipeline/lib/content_types.py)) flows through every stage: text gen → variants → media → archive → publish → ledger → email.

## The stack

| Stage | Service | Notes |
|---|---|---|
| Text | OpenRouter `google/gemini-2.5-flash` | ~$0.001/post |
| Images | fal.ai `openai/gpt-image-2` | ~$0.04 medium / $0.16 high quality |
| Video | fal.ai `kling-video/v2.5-turbo/pro` | $0.35 first 5s + $0.07/s |
| TTS | **fish.audio** S1 | ~$15/1M chars. Project mandate: TTS only via fish.audio |
| Music | fal.ai `cassetteai/music-generator` | swappable via `model_slug` |
| Distribution | Publer | single API key, all platforms |
| Archive | Notion | auto-discovered by `ZEUS_NOTION_HUB_PAGE_ID` |
| Email | Resend / AgentMail / Gmail SMTP | first one configured wins |
| Cost ledger | `~/.hermes/zeus_cost_ledger.jsonl` | every run, every model, every dollar |

**Replicate is dead.** Removed entirely — generations were lost when Replicate's storage rotated. All media now flows through `lib/fal.py` with mandatory local download + Notion archive *before* any publish step.

## Required env

| Var | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | text generation |
| `FAL_KEY` | yes | image + video generation |
| `FISH_AUDIO_API_KEY` | yes (for video) | narration TTS |
| `NOTION_API_KEY` | yes | archive |
| `ZEUS_NOTION_HUB_PAGE_ID` | yes | parent Notion page that holds the archive DB (32-char hex from page URL) |
| `PUBLER_API_KEY`, `PUBLER_WORKSPACE_ID`, `PUBLER_<PLATFORM>_ID` | only with `--publish` | distribution |
| `ZEUS_NOTIFY_EMAIL` | yes | recipient for the post-run summary |
| `RESEND_API_KEY` *or* `AGENTMAIL_API_KEY` *or* `HERMES_GMAIL_USER` + `HERMES_GMAIL_APP_PASSWORD` | one of these | email backend |

## Quick start

```bash
# 1. Set the keys above in ~/.hermes/.env

# 2. Install Python deps
pip install fal-client requests

# 3. Generate + archive (no posting yet — safe mode)
cd skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts
export $(grep -v '^#' ~/.hermes/.env | xargs)
python3 pipeline_test.py --type article --topic "Whatever you want to write about"

# 4. When ready, post to Publer
python3 pipeline_test.py --type article --topic "..." --publish
```

Every run: archives to Notion BEFORE any external API spend, downloads media locally, appends to `~/.hermes/zeus_cost_ledger.jsonl`, and sends an email summary with post permalinks + 24h/7d/30d/all-time cost rollups.

## Surrounding services

These run alongside `pipeline_test.py` and turn the pipeline into a self-driving content system:

| Component | Type | Purpose |
|---|---|---|
| [`publish_watcher.py`](../skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/publish_watcher.py) | long-running daemon | Polls Publer every ~30s in-memory, resolves real platform permalinks, updates Notion archive, fires the unified email rollup. Self-respawns on crash; supervised by [`watcher_supervisor.sh`](../skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/watcher_supervisor.sh). |
| [`ingest_ideas.py`](../skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/ingest_ideas.py) | daily cron | Drains the Notion Ideas DB. Each row can be a URL / YouTube link / raw text / attached photo / PDF / video — the script distills it into the row's chosen Target Type and queues for publish. |
| [`publish_from_notion.py`](../skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/publish_from_notion.py) | every-10m cron | Drains archive rows the user manually flipped to "Ready to Publish" in Notion. The supported manual-publish path. |
| [`weekly_analytics.py`](../skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/weekly_analytics.py) | Sunday cron | Pulls last-7-day Publer post insights, asks an LLM for "what's working / why / patterns", writes a row to the Notion Weekly Analytics DB, emails the report via the same backend rail as the per-post pipeline. |
| [`fal_reconcile.py`](../skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/fal_reconcile.py) | on-demand audit | Walks the cost ledger, verifies fal.ai job IDs, downloads any missing media, flags orphaned rows. |
| [`republish_now.py`](../skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts/republish_now.py) | on-demand | Force a piece through the publish queue immediately. |

## See also

- [Content publish workflow skill](../skills/autonomous-ai-agents/content-publish-workflow/SKILL.md) — the full per-pitfall procedural guide the agent reads
- [cron.md](cron.md) — the four cron jobs that run this pipeline automatically
