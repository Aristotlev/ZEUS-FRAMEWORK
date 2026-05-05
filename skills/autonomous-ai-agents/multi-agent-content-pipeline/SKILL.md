---
name: multi-agent-content-pipeline
description: "Build multi-agent content automation systems with specialized AI agents for content generation, media creation, and publishing"
triggers:
  - "build automated content creation system"
  - "multi-platform content publishing"
  - "content generation pipeline"
  - "automated social media posting"
  - "AI content orchestration"
  - "cost-optimized content automation"
---

# Multi-Agent Content Pipeline Architecture

Build automated content generation systems with specialized AI agents handling different pipeline stages.

## Core Architecture Pattern

**Orchestrator-Pipeline-Publisher Pattern:**
```
Daily Orchestrator (Premium Model) 
    ↓ 
Content Pipelines (Parallel Agents)
    ↓
Platform Publishers (API Integration)
```

## Key Components

### 0. Content Type Taxonomy (canonical, May 2026)

| Type | Media | Platforms | Description Length | Implementation |
|------|-------|-----------|--------------------|----------------|
| `article` | 1 image (1024x1024 default) | Twitter, IG, LI, TT | 550-900 chars (clears "read more" everywhere) | `ContentType.ARTICLE` |
| `carousel` | 3-5 slide images | Twitter, IG, LI, TT | <450 chars (user mandate) | `ContentType.CAROUSEL` |
| `short_video` | 1080x1920 video, <90s | Twitter, IG (reel), LI, TT, YouTube | 300-500 char script | `ContentType.SHORT_VIDEO` |
| `long_video` | 1920x1080 video | YouTube, Twitter, LI, Reddit | 700-1200 char script | `ContentType.LONG_VIDEO` |

The single `ContentPiece` dataclass (lib/content_types.py) flows through every pipeline stage: text gen -> variants -> media -> Notion archive -> Publer -> email -> ledger. The validate() method enforces per-type invariants (image counts, video resolution, duration cap).

### 1. Daily Orchestrator Agent
- **Purpose**: High-level content planning and pipeline coordination
- **Model**: Use premium model (expensive but runs once daily)
- **Responsibilities**: Analyze market/data, decide content mix, dispatch to pipelines
- **Cost optimization**: Single expensive call vs many cheap ones

### 2. Specialized Content Agents
Route by complexity, not content type:

**Complex Analysis** (Premium model):
- Long-form articles
- Research synthesis  
- Strategic analysis
- Educational content

**Simple Generation** (Cheap model):
- Social captions
- Breaking alerts
- Template fills
- Format conversions

### Media Generation Pipeline (fal-first, May 2026)
**All media goes through fal.ai. Replicate has been removed entirely.**
- **Images**: `fal-ai/openai/gpt-image-2` — medium ~$0.04, high $0.16 at 1920x1080. Valid sizes via `image_size: {width, height}` (multiples of 16, max 3840 edge) or presets. Raw article as prompt — no style prefix.
- **Video**: `fal-ai/kling-video/v2.5-turbo/pro/text-to-video` — $0.35 first 5s + $0.07/s. Ratios: 9:16 (short-form 1080x1920) or 16:9 (long-form 1920x1080). Single call max ~10s; chain + ffmpeg-stitch for longer.
- **Voice**: **fish.audio S1** — `https://api.fish.audio/v1/tts`, `Bearer FISH_AUDIO_API_KEY`. ~$15/1M chars. User mandate: "for TTS we use fish.audio cause everything else is unacceptable shit." Wrapper: `lib/fish.py:synthesize`.
- **Music**: `fal-ai/cassetteai/music-generator` (swappable via `model_slug` arg in `lib/fal.py:generate_music`).
- **Primary stack**: fal handles images + video + music. fish.audio handles TTS. OpenRouter handles text (gemini-2.5-flash).

**Image prompt strategy — raw article as prompt:**
- Feed the FULL article text directly into the image generation model as the prompt (truncate to ~1000 chars for GPT Image 2)
- Do NOT add style prefixes like "Professional financial visualization" or "Bloomberg-terminal style"
- Do NOT summarize or rewrite the article into a separate visual prompt
- The raw article text IS the prompt — the model interprets it naturally
- This produces images that are unique and varied based on the content, not locked to one visual style

**Platform description — unified, no variants:**
- All platforms use the same description (article body, max 1500 chars)
- No LLM call for variant generation — zero extra cost and latency
- Truncate body to 1500 chars: `caption = piece.body[:1500]`

### 4. State Machine Database
PostgreSQL with status enum pipeline:
```sql
planned → writing → media_gen → formatting → qa → scheduled → posted → failed
```

## Implementation Steps

### 1. Design Agent Hierarchy
```python
class ContentOrchestrator:
    # Daily planning agent - premium model
    async def daily_planning() -> ContentPlan
    
class ContentPipeline:
    # Specialized generation agents
    async def generate_article(spec)
    async def generate_carousel(spec) 
    async def generate_video(spec)
```

### 2. Cost Optimization Strategy
- **Budget monitoring**: Track daily costs, switch to cheap mode when over limit
- **Model routing**: Complex → premium, simple → cheap
- **Media tiers**: Use cheapest option that meets quality bar
- **Batch processing**: Group similar operations

### 3. State Management
```sql
CREATE TYPE content_status AS ENUM (
  'planned', 'writing', 'media_generated', 
  'formatted', 'qa_passed', 'scheduled', 'posted', 'failed'
);
```

### 4. Queue Processing Loop
```python
while running:
    content_item = await get_next_from_queue()
    await route_to_pipeline(content_item)
    await update_status(content_item.id, new_status)
```

## Cost Management

### Provider Selection Strategy for Agents
**Key insight**: Subscription models are "stupid expensive" for agent usage patterns

**Pay-per-use vs Subscriptions:**
- **Subscriptions**: Fixed monthly costs regardless of usage, overage fees, annual lock-ins
- **Pay-per-use**: Scales with actual generation, no monthly minimums, pause anytime
- **Agent fit**: Character/minute-based pricing = predictable per-content costs

**Optimized Provider Stack:**
- **Voice**: Fish Audio ($15/1M chars) replaces ElevenLabs ($22/month) = $20.50/month savings
- **Avatars**: Vidnoz (60 FREE min/month) replaces HeyGen ($24/month) = $24/month savings  
- **Total savings**: $44.50/month = $534/year on media alone

### Daily Budget Allocation
- **Orchestrator**: 1x premium call (~$0.50)
- **Urgent content**: Multiple cheap calls (~$1.50) 
- **Scheduled content**: Few premium calls (~$10.00)
- **Media generation**: Largest cost (~$30-40)

### Fallback Strategies
```python
if daily_cost > budget * 0.8:
    switch_to_cheap_mode()
    reduce_content_volume()
    skip_premium_features()
```

## Distribution Architecture (CRITICAL — Publer-first)

**⚠️ DO NOT wire individual platform APIs (Twitter API, Meta Graph, TikTok API).**
The user's stack uses **Publer** as the single social media distribution layer.

### Correct Distribution Stack
```
Content Pipeline Output
        │
        ├── Publer API ───→ ALL social media (X, Instagram, TikTok, LinkedIn, Facebook, YouTube)
        ├── Resend API ───→ Email newsletters
        ├── Discord Webhook ───→ Discord channels
        ├── Telegram Bot API ───→ Telegram groups/channels
        └── WhatsApp Cloud API ───→ WhatsApp messages
```

**Publer handles ALL social platforms through ONE API.** No individual Twitter/Meta/TikTok keys needed.

### Publer API Reference (see `references/publer-api-reference.md` for complete details)

**⚠️ CRITICAL — Publer API has very specific auth & payload requirements. Using wrong base URL or auth format wastes 20+ minutes every time.**

Key facts:
- **Base URL**: `https://app.publer.com/api/v1` (NOT `app.publer.io`, NOT `api.publer.io`)
- **Auth header**: `Authorization: Bearer-API <key>` (NOT just `Bearer`!)
- **Required header**: `Publer-Workspace-Id: <workspace_id>`
- **Plan**: Requires Business or Enterprise plan (API locked on lower tiers)
- **Schedule endpoint**: `POST /api/v1/posts/schedule` (NOT `/posts`!)
- **Image posting is a 3-step workflow**: (1) download image bytes, (2) upload to `POST /api/v1/media` as multipart `file=<bytes>`, (3) reference in post as `"media": [{"id": "<returned_id>"}]`
- **`"photo": "<url>"` in network params FAILS** with "undefined method 'count' for nil"
- **Multi-account bulk posting has a Publer bug** — post one account at a time
- **Twitter blocks duplicate tweet text** — vary text per post
- Load `references/publer-api-reference.md` for full endpoint table, payload examples, and pitfall list before any Publer API call
### Multi-Platform Publishing (CORRECTED)

**YouTube restriction**: Image/text community posts NOT supported. Only video posts work (type: "video"). 28s Minimax Video-01 posts to all 5 platforms including YouTube.

### Correct Distribution Architecture

**Publer is the SOLE social media distribution layer.** One API → all platforms.

```
Content Output → Publer API → X, Instagram, TikTok, LinkedIn, Facebook, YouTube
Content Output → Resend → Email newsletters
Content Output → Discord Webhook → Discord channels
Content Output → Telegram Bot → Telegram
Content Output → WhatsApp Cloud API → WhatsApp
```

**Do NOT list individual platform APIs (Twitter API, Meta Graph, TikTok API).** Publer handles all of them.

## Content Generation Workflow (MANDATORY ORDER)

### Article → Image Pipeline
**CRITICAL: The article text IS the image prompt.** Do not summarize, rephrase, or extract themes. Feed the full article text directly into the image generation model as the prompt. Truncate only if the model has a hard token limit (Flux Schnell accepts ~400 chars comfortably). This ensures visual coherence with the written content.

### Platform Description — Unified (No Variants)
**All platforms receive the same description.** No per-platform rewrites. No separate LLM call. The article body is used directly as the post caption across every platform.

- **Article**: 380-400 chars
- **Long Article**: 1400-1500 chars
- **Carousel**: <450 chars (user-mandated cap — visuals do the heavy lifting), same body across every platform (3-5 portrait slides as swipe post)
- **Short/Long Video**: body = narration script (300-500 / 700-1200 chars)
- Truncate to stated limit if over. Never generate per-platform rewrites.
- Same text to Twitter, Instagram, LinkedIn, TikTok, YouTube.
- No token waste, no variant generation step.

```python
caption = piece.body[:1500]  # same for all platforms
```

### Post-Content Save to Google Docs
After every publish, save the full article + all platform variants + image URL + publish results to Google Docs. The content file at `~/.hermes/latest_content.json` is the staging area. Google OAuth setup required once (see google-workspace skill).

### API Integration Pattern (CORRECTED)
```python
# Publer for ALL social — single API
async def publish_social(content, platforms):
    return await publer_api.create_post(
        text=content.text,
        media_urls=content.media_urls,
        platforms=platforms,  # ['twitter', 'instagram', 'tiktok', etc.]
        schedule_time=content.scheduled_for
    )

# Resend for email
async def publish_email(content, recipients):
    return await resend.Emails.send({
        "from": "Hermes <newsletter@yourdomain.com>",
        "to": recipients,
        "subject": content.subject,
        "html": content.html_body
    })

# Discord/Telegram/WhatsApp for messaging
async def publish_messaging(content, channels):
    tasks = []
    for channel in channels:
        if channel.type == "discord":
            tasks.append(send_discord_webhook(channel.url, content))
        elif channel.type == "telegram":
            tasks.append(send_telegram_message(channel.token, channel.chat_id, content))
        elif channel.type == "whatsapp":
            tasks.append(send_whatsapp_message(channel.api_key, channel.phone_id, content))
    await asyncio.gather(*tasks)
```

### Multi-Platform Publishing (CORRECTED)

**YouTube restriction**: Image/text community posts NOT supported. Only video posts work (type: "video"). 28s Minimax Video-01 posts to all 5 platforms including YouTube.

### Correct Distribution Architecture

**Publer is the SOLE social media distribution layer.** One API → all platforms.

```
Content Output → Publer API → X, Instagram, TikTok, LinkedIn, Facebook, YouTube
Content Output → Resend → Email newsletters
Content Output → Discord Webhook → Discord channels
Content Output → Telegram Bot → Telegram
Content Output → WhatsApp Cloud API → WhatsApp
```

**Do NOT list individual platform APIs (Twitter API, Meta Graph, TikTok API).** Publer handles all of them.

## Content Generation Workflow (MANDATORY ORDER)

### Article → Image Pipeline
**CRITICAL: The article text IS the image prompt.** Do not summarize, rephrase, or extract themes. Feed the full article text directly into the image generation model as the prompt. Truncate only if the model has a hard token limit (Flux Schnell accepts ~400 chars comfortably). This ensures visual coherence with the written content.

### Platform Description — Unified (No Variants)
**All platforms receive the same description.** No per-platform rewrites. No separate LLM call. The article body is used directly as the post caption across every platform.

- **Article**: 380-400 chars
- **Long Article**: 1400-1500 chars
- **Carousel**: <450 chars (user-mandated cap — visuals do the heavy lifting), same body across every platform (3-5 portrait slides as swipe post)
- **Short/Long Video**: body = narration script (300-500 / 700-1200 chars)
- Truncate to stated limit if over. Never generate per-platform rewrites.
- Same text to Twitter, Instagram, LinkedIn, TikTok, YouTube.
- No token waste, no variant generation step.

```python
caption = piece.body[:1500]  # same for all platforms
```

### Post-Content Save to Google Docs
After every publish, save the full article + all platform variants + image URL + publish results to Google Docs. The content file at `~/.hermes/latest_content.json` is the staging area. Google OAuth setup required once (see google-workspace skill).

### API Integration Pattern
```python
async def publish_to_all_platforms(content, platforms):
    formatted_versions = await format_for_platforms(content, platforms)
    await asyncio.gather(*[
        publish_to_platform(version, platform) 
        for platform, version in formatted_versions.items()
    ])
```

## Monitoring & Health

### Key Metrics
- Content success rate (published vs failed)
- Daily cost vs budget
- Pipeline processing time
- Queue backup depth

### Alert Conditions  
- Cost overrun (>120% daily budget)
- Low success rate (<80%)
- Queue backup (>20 items pending >2 hours)
- API failures (>5 consecutive)

## Pitfalls & Solutions

### Pitfall: Model costs spiral out of control
**Solution**: Implement hard budget limits with automatic cheap-mode fallback

### Pitfall: Queue backs up during high-volume events
**Solution**: Priority-based processing (breaking news = priority 9, scheduled = priority 5)

### Pitfall: API rate limits cause cascading failures  
**Solution**: Exponential backoff with jitter, separate rate limiters per service

### Pitfall: Assuming individual platform APIs for distribution
**Solution**: User uses Publer for ALL social media. Never suggest wiring Twitter API, Meta Graph, TikTok API individually. Publer handles X, Instagram, TikTok, LinkedIn, Facebook, YouTube through one API key. This mistake causes immediate frustration — the user will say "arent we fucking using publer."

### Pitfall: Proposing paid solutions when free exists
**Solution**: "The free that works today always go with that solution first if it is available." Exhaust free tiers (Vidnoz, Resend 100/day, Publer free, Pipedream) before suggesting paid alternatives. Free-first is a hard requirement, not a preference.

### Pitfall: Taking too long / over-explaining
**Solution**: The user values speed over explanation. "What is taking so long" and "finish fast dog" are signals. Execute first, explain only when asked. When researching, compile results quickly instead of over-optimizing each step. Prefer parallel execution and delegate_task over sequential browser navigation.

### Pitfall: Using wrong Publer API base URL or auth format — wastes 20+ min
**Solution**: Publer base is `https://app.publer.com/api/v1` (NOT `app.publer.io`). Auth is `Bearer-API <key>` (NOT `Bearer`). Requires `Publer-Workspace-Id` header. Images need 3-step workflow: download → upload to `/api/v1/media` → reference as `"media": [{"id": "<id>"}]`. `"photo": "<url>"` fails. Multi-account bulk has a Publer bug — post one account at a time. Full reference in `references/publer-api-reference.md`.

### Pitfall: Twitter/X blocks duplicate tweets across accounts
**Solution**: Always vary tweet text between posts — even small wording changes matter. Use OpenRouter to generate slightly different variants for each platform.

### Pitfall: Claiming post/publish worked without actual API confirmation
**Solution**: Never say "done" or "posted" without a confirmed API response. For Publer: check job status returns `{"status": "complete"}` with no failures in `payload.failures`. For any API: verify the success response before reporting it. User will call it "lying" if you claim success based on assumption. Triple-check: schedule call → job status check → confirm no failures. Only then report success.

### Pitfall: Not including costs in email notifications and Notion records
**Solution**: Every email notification MUST include an itemized cost breakdown (article gen, image/video gen, total). Every Notion record MUST have `Cost` (number) and `Model` (select) properties. The user explicitly requires cost tracking on every post. Example email: "Costs: Article: $0.001 (gemini-2.5-flash) | Image: $0.047 (GPT Image 2) | Total: $0.048".

### Pitfall: Reaching for Replicate
**Solution**: Replicate is dead in this project. Burned $15 of generations that were never archived (May 2026). All media generation goes through `lib/fal.py`. If you find old code referencing `api.replicate.com` or the `replicate` Python package, rip it out and call the fal wrappers instead.

### Pitfall: Money spent but content lost (orphan generations)
**Symptom**: pipeline crashes mid-run, fal/fish bills for what was generated, but no Notion row exists and bytes vanish — the user has no idea what was generated or where it went. Drove the May 2026 incident: $0.45 on GPT Image 2 with only 2 images to show.

**Solution**: orchestrator now follows an **artifact-first** flow:
1. Stable artifact dir at `~/.hermes/zeus_artifacts/<run_id>_<topic>/` is created BEFORE any paid call. Never `/tmp` — OS reaps it.
2. Notion row is archived **early** (right after text-gen) so even a crash mid-media leaves a row pointing at the run_id + artifact dir.
3. `generate_media_for(...)` is wrapped in try/except. On failure, status flips to `media_partial` (some assets) or `failed` (none) and the run continues to:
4. `archive.update_assets(piece)` patches Notion with whatever URLs/blocks WERE captured.
5. `ledger_append(piece)` writes the final row (supersedes checkpoints).
6. `send_pipeline_summary(piece)` emails — with a red `Run did NOT complete cleanly` banner if applicable, plus the artifact dir path and asset URLs.

The exception is re-raised at the end so callers still see the failure.

### Pitfall: Pipeline blocks ~6 min per run polling Publer for live URLs
**Solution**: `publish()` is non-blocking by default — it schedules every platform in parallel via `ThreadPoolExecutor`, sets `status="scheduled"`, and enqueues the run for `scripts/publish_watcher.py` via `lib/publish_queue.py`. The watcher polls Publer out-of-process, captures permalinks, patches Notion via `update_status`, writes the final ledger row, and sends the "posts live" email. Run the watcher from cron (`*/2 * * * * publish_watcher.py --once`) or as a daemon (`publish_watcher.py --daemon`). Pass `--wait-for-live` to `pipeline_test.py` to keep the old blocking behavior for debugging.

### Pitfall: Carousel image gen is the slowest step
**Solution**: `_gen_carousel_images` runs all N slides through `ThreadPoolExecutor(max_workers=slides)` — fal's queue handles concurrent jobs fine. Cuts a 4-slide carousel from ~4 min sequential to ~60s parallel. Same pattern for fish narration + fal music in `audio_mix.py` (was sequential, now overlapped).

### Pitfall: No data on where pipeline time actually goes
**Solution**: Every `run()` records per-phase wall-clock to `piece.phase_durations_ms` (`text_gen`, `notion_archive_early`, `media_gen`, `notion_assets`, `publish`). Persisted in the ledger. `ledger_summary()` returns `timing_by_type` (p50/p90/max per content type) and `timing_by_phase` (p50/p90 per phase) — optimization is data-driven instead of guessed.

### Pitfall: Cost rollups don't match reality
**Solution**: ledger now writes per-paid-call **checkpoint rows** (already in place) AND tracks `artifact_dir`, `asset_urls`, `asset_local_paths`, plus a `cost_sources` map tagging each cost line as `actual` or `estimate`. `ledger_summary()` returns `total_cost_usd`, `leaked_cost_usd`, `actual_cost_usd`, `estimated_cost_usd`, `accuracy_pct`. Email shows `Last 30d: $4.20 (12 runs, 2 leaked $0.45) Accuracy 87% (actual $3.65, est $0.55)`.

**Per-provider accuracy:**
- **OpenRouter**: `usage.cost` captured per call → `actual` (the dollar amount they billed). Driven by `"usage": {"include": True}` in the request body.
- **fish.audio**: char-count × published rate IS their billing primitive → `actual`. Side log at `~/.hermes/zeus_fish_calls.jsonl`.
- **fal.ai (image/video/music)**: standard response has no cost field → `estimate` from local price table. Every call is side-logged at `~/.hermes/zeus_fal_calls.jsonl` with request_id + response payload. `lib/fal.py` also tries to extract `cost` / `metrics.cost` / `pricing.charge` from the response and prefers any actual it finds.

**Closing the gap:**
- `scripts/orphan_sweep.py` — leaked / failed runs with their on-disk bytes
- `scripts/fal_reconcile.py` — pulls fal billing actuals (or summarizes the side log if billing API is dashboard-only) and writes deltas to `~/.hermes/zeus_fal_reconciled.jsonl`

### Pitfall: Summarizing the article for the image prompt instead of using the full text
**Solution**: Feed the raw article text directly into the image model as the prompt. Do NOT extract keywords, themes, or add style prefixes. The user directive is explicit: use the article as the prompt — no wrapper, no forced style. Truncate only if model rejects the length (~1000 chars for GPT Image 2).

### Pitfall: Saving content to Google Docs without OAuth setup
**Solution**: Google Workspace OAuth is one-time setup. Check auth first (`setup.py --check`). If unauthenticated, guide user through the OAuth flow before attempting saves. Content can be staged in `~/.hermes/latest_content.json` until auth is ready.

### Pitfall: Generating per-platform variants
**Solution**: Do NOT generate platform variants. All platforms use the same description (article body, max 1500 chars). No separate LLM call needed. `caption = piece.body[:1500]`

### Pitfall: DeepSeek V4 Pro returns null content via OpenRouter
**Solution**: Use `google/gemini-2.5-flash` or `deepseek/deepseek-chat` instead. Test model availability before committing to it for content generation.

### Pitfall: Publer API auth fails despite valid token
**Solution**: Publer API requires **Business or Enterprise plan** — free/pro plans CANNOT use the API regardless of valid tokens. Use `https://app.publer.com/api/v1` — `app.publer.io` returns Cloudflare 1010 "browser_signature_banned". Auth is `Bearer-API <token>` (NOT plain `Bearer`). Always include `Accept: application/json`, `User-Agent: Mozilla/5.0`, `Origin: https://app.publer.com` headers or Cloudflare blocks you. YouTube community posts are NOT supported — only video uploads. Skip YouTube. Full reference at `references/publer-api.md`.

### Pitfall: No visibility into system health
**Solution**: Real-time monitoring dashboard with cost tracking and alert thresholds

## Content Ideas Intelligence Integration

**Problem**: Content pipelines starve without continuous idea flow  
**Solution**: Multi-source intelligent discovery system

**3-Source Pattern:**
- **File System Drops**: Screenshots, links, notes → Vision AI analysis
- **Google Sheets Integration**: Live collaboration with structured input
- **Automated Market Crawling**: 8 financial categories + congressional trading

See `references/content-ideas-intelligence.md` for complete implementation details, `references/cost-optimization-analysis.md` for detailed provider cost comparisons, `references/full-cost-architecture.md` for a production-ready 3-scenario cost model (Launch $99/mo → Growth $193/mo → Scale $395/mo) with per-piece pricing and required API tier list, and `references/publer-api.md` for Publer API endpoints, auth format, DNS quirks, and plan requirements.

**Key Integration Points:**
```python
# Enhanced daily scheduler (7 AM EST)
results = await enhanced_daily_content_ideas_processing(llm_client, db)
await queue_high_potential_ideas(results)  # 7+/10 → content queue
```

## Success Patterns

1. **Start simple**: Begin with 1-2 content types, add complexity gradually
2. **Cost-first design**: Build budget constraints into architecture from day 1  
3. **Fail gracefully**: Every pipeline stage should handle errors and retry logic
4. **Monitor everything**: Log costs, timing, success rates for optimization
5. **Platform-aware**: Don't treat all social platforms the same
6. **Never starve the pipeline**: Implement multi-source content discovery to maintain continuous flow
7. **Test before building**: Run `scripts/pipeline_test.py --topic "..."` to validate text+image+distribution works before building the full orchestrator. Confirms all API keys are live.

## User Preference Integration

- **Action-oriented approach**: Build working system first, explain manual steps second
- **Immediate execution**: Provide complete implementations, not just concepts
- **Cost transparency**: Always include detailed cost breakdowns with specific provider pricing