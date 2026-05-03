# 🏗️ Zeus Framework — Complete Content Pipeline Architecture
### Full Cost Analysis, Volume Scenarios & API Requirements
**Date:** May 3, 2026 | **Status:** Production-Ready Blueprint

---

## 📐 ARCHITECTURE OVERVIEW

```
                    ┌─────────────────────────────────────┐
                    │     DAILY ORCHESTRATOR (6 AM EST)    │
                    │     Premium LLM — plans content mix  │
                    └─────────────┬───────────────────────┘
                                  │
        ┌─────────────┬───────────┼───────────┬─────────────┐
        ▼             ▼           ▼           ▼             ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐
   │  IDEAS   │ │  TEXT   │ │ VISUALS │ │  AUDIO  │ │  VIDEO   │
   │ INTAKE   │ │  GEN    │ │  GEN    │ │  GEN    │ │   GEN    │
   └────┬─────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬─────┘
        │             │           │           │           │
        └─────────────┴───────────┼───────────┴───────────┘
                                  ▼
                    ┌─────────────────────────┐
                    │   QA / FORMATTING GATE   │
                    └─────────────┬───────────┘
                                  ▼
        ┌─────────────┬───────────┼───────────┬─────────────┐
        ▼             ▼           ▼           ▼             ▼
   ┌────────┐  ┌─────────┐ ┌────────┐ ┌────────┐  ┌──────────┐
   │   X    │  │INSTAGRAM│ │TIKTOK  │ │DISCORD │  │  EMAIL   │
   │ TWITTER│  │ REELS   │ │ SHORTS │ │ CHANNEL│  │NEWSLETTER│
   └────────┘  └─────────┘ └────────┘ └────────┘  └──────────┘
```

**Orchestrator → 5 Pipelines → 6 Distribution Channels**

---

## 📊 COST ANALYSIS BY CONTENT TYPE

### 1. TEXT GENERATION (LLM)

| Content Type | Model Tier | Cost/Unit | Chars | Words ~ |
|---|---|---|---|---|
| Social caption (X) | Cheap (Haiku/GPT-4o-mini) | $0.003 | 280 | 45 |
| Thread/carousel copy | Cheap | $0.008 | 800 | 130 |
| Breaking alert | Cheap | $0.002 | 200 | 30 |
| News summary | Mid (Sonnet/4o) | $0.15 | 3,000 | 500 |
| Long-form article | Premium (Opus/4.5) | $0.50 | 8,000 | 1,300 |
| Deep research piece | Premium | $1.00 | 15,000 | 2,500 |
| Weekly newsletter | Premium | $0.75 | 10,000 | 1,600 |
| Platform reformat | Cheap | $0.005 | 500 | 80 |

### 2. VISUAL GENERATION

| Content Type | Provider | Cost/Unit | Notes |
|---|---|---|---|
| AI image (basic) | Flux Schnell via fal.ai | $0.003 | 1024×1024, ~2 sec |
| AI image (quality) | Flux Pro via fal.ai | $0.055 | High detail, 4 sec |
| AI image (text-render) | Ideogram 2.0 | $0.08 | Best for text-in-image |
| Data chart | Template + cheap LLM | $0.01 | SVG/PNG auto-gen |
| Carousel (3 images) | Flux Schnell ×3 | $0.009 | + $0.005 text = $0.014 |
| Carousel (5 images, pro) | Flux Pro ×5 | $0.275 | + $0.008 text = $0.283 |
| Thumbnail | Flux Schnell | $0.003 | 1280×720 |

### 3. AUDIO GENERATION

| Content Type | Provider | Cost/Unit | Volume Basis |
|---|---|---|---|
| Short voiceover (500 chars) | Fish Audio | $0.0075 | ~45 sec audio |
| Medium narration (2,000 chars) | Fish Audio | $0.03 | ~3 min audio |
| Long narration (5,000 chars) | Fish Audio | $0.075 | ~7.5 min audio |
| Daily news audio (10,000 chars) | Fish Audio | $0.15 | ~15 min podcast |
| **Vs. ElevenLabs (avoid)** | N/A | $22/mo fixed | 10× more expensive |

**Fish Audio pricing:** $15 per 1,000,000 characters = $0.000015/char

### 4. VIDEO GENERATION

| Content Type | Provider | Cost/Unit | Duration |
|---|---|---|---|
| Short clip | Kling 1.6 via fal.ai | $0.08 | 5 sec |
| 15-sec Reel/TikTok | Kling ×3 clips | $0.24 | 15 sec |
| 30-sec video | Kling ×6 clips | $0.48 | 30 sec |
| 60-sec video | Kling ×12 clips | $0.96 | 60 sec |
| Avatar video (daily) | Vidnoz | **FREE** | 60 free min/month |
| Avatar video (overflow) | Vidnoz paid | $0.20/min | After 60 min free |

### 5. DISTRIBUTION (Per-Post)

| Platform | Method | Cost | Rate Limit |
|---|---|---|---|
| X / Twitter | API v2 | Free | 1,500 posts/month (free) |
| Instagram | Meta Graph API | Free | 25 posts/day (business) |
| TikTok | Direct API | Free | N/A (creator account) |
| Discord | Webhook | Free | 30 msg/sec |
| Email (personal) | Gmail SMTP / AgentMail | Free | 500/day (Gmail) |
| Email (bulk) | Resend | Free | 100/day |

---

## 📈 VOLUME-BASED MONTHLY COST MODELS

### Scenario A: LAUNCH (5-10 pieces/day)

**Content Mix:**
- 2 long articles/week
- 1 newsletter/week
- 5 Twitter posts/day (150/month)
- 2 Instagram posts/day (60/month)
- 1 video/day (30/month)
- 1 carousel/3 days (10/month)

| Category | Volume | Unit Cost | Monthly |
|---|---|---|---|
| **TEXT** | | | |
| Long articles | 8 | $0.50 | $4.00 |
| Newsletter | 4 | $0.75 | $3.00 |
| Twitter captions | 150 | $0.003 | $0.45 |
| IG captions | 60 | $0.008 | $0.48 |
| Platform reformat | 60 | $0.005 | $0.30 |
| **VISUALS** | | | |
| AI images (basic) | 60 | $0.003 | $0.18 |
| Thumbnails | 30 | $0.003 | $0.09 |
| Carousels (3-img) | 10 | $0.014 | $0.14 |
| Data charts | 8 | $0.01 | $0.08 |
| **AUDIO** | | | |
| Short VO for videos | 30 | $0.0075 | $0.23 |
| Medium VO for articles | 8 | $0.03 | $0.24 |
| **VIDEO** | | | |
| 30-sec videos | 30 | $0.48 | $14.40 |
| Avatar (Vidnoz free) | 30 | $0.00 | $0.00 |
| **SUBTOTAL** | | | **$23.59** |
| **INFRASTRUCTURE** | | | **$75.00** |
| **TOTAL** | | | **~$99/mo** |

---

### Scenario B: GROWTH (15-25 pieces/day)

**Content Mix:**
- 5 long articles/week
- 2 newsletters/week
- 12 Twitter posts/day (360/month)
- 5 Instagram posts/day (150/month)
- 3 TikTok/Reels/day (90/month)
- 1 YouTube Short/week (4/month — longer)
- 3 carousels/week (12/month)
- Daily news roundup audio

| Category | Volume | Unit Cost | Monthly |
|---|---|---|---|
| **TEXT** | | | |
| Long articles | 20 | $0.50 | $10.00 |
| Research pieces | 5 | $1.00 | $5.00 |
| Newsletter | 8 | $0.75 | $6.00 |
| Twitter captions | 360 | $0.003 | $1.08 |
| IG captions | 150 | $0.008 | $1.20 |
| Reformat | 200 | $0.005 | $1.00 |
| **VISUALS** | | | |
| AI images (basic) | 120 | $0.003 | $0.36 |
| AI images (quality) | 30 | $0.055 | $1.65 |
| Thumbnails | 94 | $0.003 | $0.28 |
| Carousels (5-img) | 12 | $0.283 | $3.40 |
| Data charts | 25 | $0.01 | $0.25 |
| **AUDIO** | | | |
| Short VO | 90 | $0.0075 | $0.68 |
| Medium VO | 20 | $0.03 | $0.60 |
| Long VO (YouTube) | 4 | $0.075 | $0.30 |
| Daily news audio | 30 | $0.15 | $4.50 |
| **VIDEO** | | | |
| 30-sec (TikTok/Reels) | 90 | $0.48 | $43.20 |
| 60-sec (YouTube) | 4 | $0.96 | $3.84 |
| Avatar (Vidnoz free) | 90 | $0.00 | $0.00 |
| **SUBTOTAL** | | | **$83.34** |
| **INFRASTRUCTURE** | | | **$110.00** |
| **TOTAL** | | | **~$193/mo** |

---

### Scenario C: SCALE (40-60 pieces/day)

**Content Mix:**
- 10 long articles/week
- 5 research pieces/week
- 3 newsletters/week
- 25 Twitter posts/day (750/month)
- 10 Instagram posts/day (300/month)
- 8 TikTok/Reels/day (240/month)
- 5 YouTube Shorts/week (20/month)
- 1 long YouTube/week (4/month)
- 7 carousels/week (28/month)
- Daily podcast/narration
- Breaking alerts 24/7

| Category | Volume | Unit Cost | Monthly |
|---|---|---|---|
| **TEXT** | | | |
| Long articles | 40 | $0.50 | $20.00 |
| Research pieces | 20 | $1.00 | $20.00 |
| Newsletter | 12 | $0.75 | $9.00 |
| Breaking alerts | 200 | $0.002 | $0.40 |
| Twitter captions | 750 | $0.003 | $2.25 |
| IG captions | 300 | $0.008 | $2.40 |
| Threads/long posts | 60 | $0.015 | $0.90 |
| Reformat | 600 | $0.005 | $3.00 |
| **VISUALS** | | | |
| AI images (basic) | 300 | $0.003 | $0.90 |
| AI images (quality) | 80 | $0.055 | $4.40 |
| Thumbnails | 264 | $0.003 | $0.79 |
| Carousels (5-img) | 28 | $0.283 | $7.92 |
| Data charts | 60 | $0.01 | $0.60 |
| **AUDIO** | | | |
| Short VO | 240 | $0.0075 | $1.80 |
| Medium VO | 40 | $0.03 | $1.20 |
| Long VO | 24 | $0.075 | $1.80 |
| Daily podcast | 30 | $0.15 | $4.50 |
| **VIDEO** | | | |
| 30-sec (TikTok/Reels) | 240 | $0.48 | $115.20 |
| 60-sec (YouTube) | 20 | $0.96 | $19.20 |
| 3-min (long YouTube) | 4 | $2.88 | $11.52 |
| Avatar overflow | 60 | $0.20 | $12.00 |
| **SUBTOTAL** | | | **$259.79** |
| **INFRASTRUCTURE** | | | **$135.00** |
| **TOTAL** | | | **~$395/mo** |

---

## 🔑 APIs YOU NEED TO PROVIDE

### Tier 1 — MANDATORY (Launch Can't Happen Without These)

| # | API / Key | Where to Get It | Price | What It Powers |
|---|---|---|---|---|
| 1 | **OpenRouter API Key** | openrouter.ai/keys | Pay-per-token | ALL text generation (orchestrator, articles, captions) |
| 2 | **fal.ai API Key** | fal.ai/dashboard/keys | Pay-per-use | All AI images (Flux) + all video generation (Kling) |
| 3 | **Fish Audio API Key** | fish.audio → API | $15/1M chars | All voiceovers, narration, podcast audio |

### Tier 2 — DISTRIBUTION (Needed to Publish)

| # | API / Key | Where to Get It | Price | What It Powers |
|---|---|---|---|---|
| 4 | **X/Twitter API v2** | developer.x.com → App | Free tier (1.5K posts/mo) | Twitter/X posting |
| 5 | **Meta Graph API** | developers.facebook.com → App | Free | Instagram + Facebook posting |
| 6 | **Resend API Key** | resend.com/api-keys | Free (100/day) | Email newsletters, alerts |
| 7 | **Discord Webhook URL** | Server Settings → Integrations | Free | Discord channel auto-posting |

### Tier 3 — ENHANCEMENT (Higher Quality, Scale)

| # | API / Key | Where to Get It | Price |
|---|---|---|---|
| 8 | **Vidnoz Account** | vidnoz.com → Free signup | Free (60 min/month) |
| 9 | **TikTok Creator API** | developers.tiktok.com | Free |
| 10 | **YouTube Data API** | console.cloud.google.com | Free quota |
| 11 | **Ideogram API** | ideogram.ai → API | $0.08/image |
| 12 | **Google Sheets API** (service account) | console.cloud.google.com → Service Account | Free |

### Tier 4 — OPTIONAL (Cost Savings / Redundancy)

| # | API / Key | What It Replaces | Savings |
|---|---|---|---|
| 13 | **Gmail App Password** (your_email@gmail.com) | Resend for personal sends | Free |
| 14 | **SendGrid API Key** | Resend fallback | Free (100/day) |
| 15 | **Proton Mail Bridge** (paid account) | Gmail/AgentMail | $4/mo |

---

## 🖥️ INFRASTRUCTURE BREAKDOWN

| Component | Launch ($75/mo) | Growth ($110/mo) | Scale ($135/mo) |
|---|---|---|---|
| VPS/Hosting (Hetzner/DigitalOcean) | $20 | $35 | $50 |
| PostgreSQL (managed) | $15 | $20 | $25 |
| Redis | $10 | $12 | $15 |
| CDN/Storage (S3/B2) | $5 | $8 | $12 |
| Monitoring (Sentry/Datadog) | $10 | $15 | $18 |
| Domain + DNS | $5 | $5 | $5 |
| CI/CD + Backups | $10 | $15 | $10 |

---

## 📋 TOTAL COST SUMMARY

| Scenario | Volume | Monthly Content | Monthly Infra | **TOTAL/MO** | **Annual** | Cost/Unit |
|---|---|---|---|---|---|---|
| **Launch** | 5-10/day | $24 | $75 | **~$99** | ~$1,188 | ~$0.33 |
| **Growth** | 15-25/day | $83 | $110 | **~$193** | ~$2,316 | ~$0.26 |
| **Scale** | 40-60/day | $260 | $135 | **~$395** | ~$4,740 | ~$0.22 |

**Key takeaway:** Cost per piece drops 33% from Launch to Scale (economies of scale on infrastructure + cheaper models for high-volume content).

---

## ⚡ DEPLOYMENT: WHAT HAPPENS WHEN YOU PASTE EACH KEY

| You paste me: | I deploy: | Time: |
|---|---|---|
| `OPENROUTER_API_KEY=sk-or-...` | Full text pipeline — articles, captions, threads, newsletters | 5 min |
| `FAL_KEY=...` | Image + video generation pipeline (Flux + Kling) | 3 min |
| `FISH_AUDIO_KEY=...` | Voiceover/narration pipeline | 2 min |
| `TWITTER_BEARER=...` | Auto-post to X | 5 min |
| `RESEND_KEY=re_...` | Email newsletter distribution | 2 min |
| `DISCORD_WEBHOOK=https://...` | Discord channel auto-posting | 1 min |
| All 6 above | **FULL PIPELINE LIVE** | ~20 min |

---

## 🔄 PIPELINE STATE MACHINE

```
content_ideas → planned → writing → media_gen → formatting → qa
                                                       ↓
                                                  [qa_passed]
                                                       ↓
                                                  scheduled → posted
                                                       ↓
                                                    failed → retry
```

Each piece tracked in PostgreSQL with status enum, timestamps, cost tracking, and engagement metrics.

---

## 🚨 COST GUARDRAILS

```
if daily_cost > budget * 0.8:
    → switch cheap models for captions/alerts
    → reduce video frequency
    → skip premium images

if daily_cost > budget * 1.0:
    → pause all generation
    → send alert to Discord
    → wait for manual override
```

---

*Built for drop-in deployment. Paste keys → Pipeline live.*