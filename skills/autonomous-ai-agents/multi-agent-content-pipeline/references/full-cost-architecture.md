# Full Cost Architecture — 3-Scenario Production Model

Complete production-ready cost analysis for the Zeus Content Pipeline, with 3 volume scenarios and per-unit pricing for every content type.

## Architecture Summary

```
Daily Orchestrator (6 AM EST, premium LLM) 
    → 5 Parallel Pipelines (Ideas Intake, Text Gen, Visuals, Audio, Video)
    → QA/Formatting Gate
    → 5 Distribution Channels (Publer → all social, Resend → email, Discord, Telegram, WhatsApp)
```

## Distribution Stack (CORRECT)

| Channel | Provider | Cost | What it handles |
|---|---|---|---|
| ALL social media | Publer API | Free-$20/mo | X, Instagram, TikTok, LinkedIn, Facebook, YouTube |
| Email newsletters | Resend API | Free (100/day) | Broadcast emails |
| Discord | Webhook | Free | Community channels |
| Telegram | Bot API | Free | Groups/channels |
| WhatsApp | Cloud API | Free (1K msg/mo) | Direct messaging |

## Monthly Cost Models

### Scenario A: LAUNCH (5-10 pieces/day) — ~$99/mo

| Category | Volume | Unit Cost | Monthly |
|---|---|---|---|
| Long articles | 8 | $0.50 | $4.00 |
| Newsletter | 4 | $0.75 | $3.00 |
| Twitter captions | 150 | $0.003 | $0.45 |
| IG captions | 60 | $0.008 | $0.48 |
| AI images (basic) | 60 | $0.003 | $0.18 |
| Carousels (3-img) | 10 | $0.014 | $0.14 |
| Short VO | 30 | $0.0075 | $0.23 |
| 30-sec videos | 30 | $0.48 | $14.40 |
| **Content Subtotal** | | | **$23.59** |
| **Infrastructure** | | | **$75.00** |
| **TOTAL** | | | **~$99/mo** |

### Scenario B: GROWTH (15-25 pieces/day) — ~$205/mo

| Category | Volume | Unit Cost | Monthly |
|---|---|---|---|
| Long articles | 20 | $0.50 | $10.00 |
| Research pieces | 5 | $1.00 | $5.00 |
| Newsletter | 8 | $0.75 | $6.00 |
| Twitter captions | 360 | $0.003 | $1.08 |
| IG captions | 150 | $0.008 | $1.20 |
| AI images (basic) | 120 | $0.003 | $0.36 |
| AI images (quality) | 30 | $0.055 | $1.65 |
| Carousels (5-img) | 12 | $0.283 | $3.40 |
| Short VO | 90 | $0.0075 | $0.68 |
| Daily news audio | 30 | $0.15 | $4.50 |
| 30-sec videos | 90 | $0.48 | $43.20 |
| 60-sec videos | 4 | $0.96 | $3.84 |
| **Content Subtotal** | | | **$83.34** |
| Publer Pro | | | **$12.00** |
| **Infrastructure** | | | **$110.00** |
| **TOTAL** | | | **~$205/mo** |

### Scenario C: SCALE (40-60 pieces/day) — ~$415/mo

| Category | Volume | Unit Cost | Monthly |
|---|---|---|---|
| Long articles | 40 | $0.50 | $20.00 |
| Research pieces | 20 | $1.00 | $20.00 |
| Newsletter | 12 | $0.75 | $9.00 |
| Twitter captions | 750 | $0.003 | $2.25 |
| IG captions | 300 | $0.008 | $2.40 |
| Threads | 60 | $0.015 | $0.90 |
| Breaking alerts | 200 | $0.002 | $0.40 |
| AI images (basic) | 300 | $0.003 | $0.90 |
| AI images (quality) | 80 | $0.055 | $4.40 |
| Carousels (5-img) | 28 | $0.283 | $7.92 |
| Short VO | 240 | $0.0075 | $1.80 |
| Daily podcast | 30 | $0.15 | $4.50 |
| 30-sec videos | 240 | $0.48 | $115.20 |
| 60-sec videos | 20 | $0.96 | $19.20 |
| 3-min YouTube | 4 | $2.88 | $11.52 |
| **Content Subtotal** | | | **$259.79** |
| Publer Business | | | **$20.00** |
| **Infrastructure** | | | **$135.00** |
| **TOTAL** | | | **~$415/mo** |

## Per-Unit Cost Reference

| Content Type | Cost | Provider |
|---|---|---|
| Social caption (X) | $0.003 | Cheap LLM (Haiku/4o-mini) |
| Thread carousel copy | $0.008 | Cheap LLM |
| Breaking alert | $0.002 | Cheap LLM |
| Long-form article | $0.50 | Premium LLM (Opus/4.5) |
| Research piece | $1.00 | Premium LLM |
| Weekly newsletter | $0.75 | Premium LLM |
| AI image (basic) | $0.003 | Flux Schnell via fal.ai |
| AI image (quality) | $0.055 | Flux Pro via fal.ai |
| Carousel (3 basic images) | $0.014 | Flux Schnell ×3 + text |
| Carousel (5 quality images) | $0.283 | Flux Pro ×5 + text |
| Data chart | $0.01 | Template + cheap LLM |
| Short VO (500 chars) | $0.0075 | Fish Audio |
| Medium VO (2K chars) | $0.03 | Fish Audio |
| Daily news audio (10K chars) | $0.15 | Fish Audio |
| 5-sec video clip | $0.08 | Kling 1.6 via fal.ai |
| 30-sec TikTok/Reel | $0.48 | Kling ×6 + VO |
| 60-sec YouTube Short | $0.96 | Kling ×12 + VO |
| Avatar video (daily) | FREE | Vidnoz (60 free min/month) |

## Required APIs (Tiered)

### Tier 1 — MANDATORY
| # | API | Source | Price |
|---|---|---|---|
| 1 | OpenRouter | openrouter.ai/keys | Pay-per-token |
| 2 | fal.ai | fal.ai/dashboard | Pay-per-use |
| 3 | Fish Audio | fish.audio | $15/1M chars |
| 4 | Publer | publer.io → Settings → API | Free-$20/mo |
| 5 | Resend | resend.com/api-keys | Free (100/day) |

### Tier 2 — MESSAGING
| # | API | Source | Price |
|---|---|---|---|
| 6 | Discord Webhook | Server Settings → Integrations | Free |
| 7 | Telegram Bot | @BotFather | Free |
| 8 | WhatsApp Cloud API | Meta for Developers | Free (1K/mo) |

### Tier 3 — ENHANCEMENT
| # | API | Source |
|---|---|---|
| 9 | Vidnoz Account | vidnoz.com (free) |
| 10 | Ideogram API | ideogram.ai ($0.08/img) |
| 11 | Google Sheets API | console.cloud.google.com |

## Cost Guardrails
```python
if daily_cost > budget * 0.8:
    → switch cheap models for captions/alerts
    → reduce video frequency
    → skip premium images
if daily_cost > budget * 1.0:
    → pause all generation
    → send alert to Discord
    → wait for manual override
```

## Key Principle: Free First
Always exhaust free tiers before paid. Vidnoz (60 min/month free), Resend (100/day free), Publer (free tier), Pipedream (10K invocations/month free).
