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

### Media Generation Pipeline
**Tiered cost strategy with provider optimization:**
- **Images**: Replicate GPT Image 2 ($0.047 medium). Valid ratios: 1:1, 3:2, 2:3 ONLY. Raw article as prompt — NO style prefix.
- **Video**: Replicate Minimax Video-01 ($0.50/28s) — ONLY accessible video model on Replicate (Seedance, Wan 2.2, Kling, CogVideoX all 403/404 or on fal.ai which is balance-exhausted). Duration: up to 28s. Aspect: 9:16 or 1:1. Prompt_optimizer: true.
- **Voice**: Fish Audio ($15/1M chars) vs ElevenLabs ($22/month) = 98.8% savings. Replicate XTTS-v2 deprecated — use Fish Audio primary.
- **Primary stack**: Replicate handles images (GPT Image 2) + video (Minimax Video-01). OpenRouter handles text (gemini-2.5-flash).

**Image prompt strategy — raw article as prompt:**
- Feed the FULL article text directly into the image generation model as the prompt (truncate to ~1000 chars for GPT Image 2)
- Do NOT add style prefixes like "Professional financial visualization" or "Bloomberg-terminal style"
- Do NOT summarize or rewrite the article into a separate visual prompt
- The raw article text IS the prompt — the model interprets it naturally
- This produces images that are unique and varied based on the content, not locked to one visual style

**Platform variant generation — single-call optimization:**
- Generate ALL platform variants in ONE LLM call (Twitter, Instagram, LinkedIn, TikTok)
- Request JSON output with `response_format: {"type": "json_object"}`
- Prompt for platform-specific constraints inline (char limits, tone, hashtag strategy)
- This is 4x faster and 4x cheaper than separate calls per platform

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

### Platform Optimization
Every piece of content gets platform-specific variants generated alongside the article:
- **Twitter**: 280 chars max, punchy, zero hashtags unless natural
- **Instagram**: visual-first caption, up to 2200 chars, relevant hashtags footer
- **LinkedIn**: professional tone, 2+ paragraphs, industry depth
- **TikTok**: 150 chars max, short punchy, 2-3 hashtags
- **YouTube**: community post format, engaging question/prompt

Generate all variants in a single LLM call with `response_format: json_object` for speed.

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

### Platform Optimization
Every piece of content gets platform-specific variants generated alongside the article:
- **Twitter**: 280 chars max, punchy, zero hashtags unless natural
- **Instagram**: visual-first caption, up to 2200 chars, relevant hashtags footer
- **LinkedIn**: professional tone, 2+ paragraphs, industry depth
- **TikTok**: 150 chars max, short punchy, 2-3 hashtags
- **YouTube**: community post format, engaging question/prompt

Generate all variants in a single LLM call with `response_format: json_object` for speed.

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

### Pitfall: Using wrong video model on Replicate
**Solution**: minimax/video-01 is the ONLY accessible video generation model. Seedance, Wan 2.2, Kling 1.6, CogVideoX all return 403/404 on standard Replicate API keys. Don't waste time trying them. fal.ai has Kling but balance exhausts fast. Minimax is reliable, $0.50 per 28s clip.

### Pitfall: Summarizing the article for the image prompt instead of using the full text
**Solution**: Feed the raw article text directly into the image model as the prompt. Do NOT extract keywords, themes, or add style prefixes. The user directive is explicit: use the article as the prompt — no wrapper, no forced style. Truncate only if model rejects the length (~1000 chars for GPT Image 2).

### Pitfall: Saving content to Google Docs without OAuth setup
**Solution**: Google Workspace OAuth is one-time setup. Check auth first (`setup.py --check`). If unauthenticated, guide user through the OAuth flow before attempting saves. Content can be staged in `~/.hermes/latest_content.json` until auth is ready.

### Pitfall: Generated content lacks platform optimization
**Solution**: Dedicated formatting agents that understand platform-specific requirements

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