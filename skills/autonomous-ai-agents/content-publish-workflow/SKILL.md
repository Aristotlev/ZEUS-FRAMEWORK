---
name: content-publish-workflow
description: "End-to-end content generation + publishing for all 4 Zeus content types (Article, Carousel, Short-form Video, Long-form Video). fal.ai for media, fish.audio for TTS, Publer for distribution, Notion archive on every run, email summary with always-on cost analysis."
triggers:
  - "publish content"
  - "generate and post"
  - "create article and post everywhere"
  - "content pipeline run"
  - "post to all platforms"
  - "content post"
---

# Content Publish Workflow

FOUR CONTENT TYPES. Pick exactly one per run — never interleave.

| Type | Media | Platforms | Notes |
|------|-------|-----------|-------|
| **Article** | 1 image (1024x1024 default) + long description | Twitter, Instagram, LinkedIn, TikTok | Description must clear "read more" on every visual platform |
| **Carousel** | 3-5 slide images + long description | Twitter, Instagram, LinkedIn, TikTok | Slides 1024x1024 each |
| **Short-form Video** | 1080x1920 video, <90s | Twitter, Instagram (reel), LinkedIn, TikTok, YouTube Shorts | Mobile-native portrait |
| **Long-form Video** | 1920x1080 video | YouTube, Twitter, LinkedIn, Reddit | Landscape; chain Kling clips for >10s |

**ALWAYS archive to Notion BEFORE any Publer call.** $15 of Replicate spend was lost in May 2026 because content was never persisted. The `pipeline_test.py` orchestrator does this automatically.

## Stack (fal-first, May 2026)

- **Text**: OpenRouter `google/gemini-2.5-flash`
- **Images**: `fal-ai/openai/gpt-image-2` (medium ~$0.04, high $0.16 at 1920x1080)
- **Video**: `fal-ai/kling-video/v2.5-turbo/pro/text-to-video` ($0.35 first 5s + $0.07/s)
- **TTS**: **fish.audio** S1 — user mandate: "for TTS we use fish.audio cause everything else is unacceptable shit". Endpoint `https://api.fish.audio/v1/tts`, header `Authorization: Bearer $FISH_AUDIO_API_KEY`, body `{text, format, prosody, reference_id?}`. ~$15/1M chars on S1.
- **Music**: `fal-ai/cassetteai/music-generator` (swappable via `model_slug`)
- **Distribution**: Publer
- **Archive**: Notion (your content-hub page -> Archive DB, auto-discovered)
- **Notifications**: Resend / AgentMail / Gmail SMTP (auto-pick by configured env)
- **Cost ledger**: `~/.hermes/zeus_cost_ledger.jsonl` — every run, every cost

**Replicate is dead.** Removed entirely after the May 2026 incident. Do not write Replicate calls. If you encounter old code that imports the Replicate SDK or calls `api.replicate.com`, port it to `lib/fal.py`.

## Prerequisites

- `OPENROUTER_API_KEY`, `FAL_KEY`, `NOTION_API_KEY`, `PUBLER_API_KEY`, `FISH_AUDIO_API_KEY` in `~/.hermes/.env`
- Optional Publer overrides: `PUBLER_TWITTER_ID`, `PUBLER_INSTAGRAM_ID`, `PUBLER_LINKEDIN_ID`, `PUBLER_TIKTOK_ID`, `PUBLER_YOUTUBE_ID`, `PUBLER_REDDIT_ID`, `PUBLER_FACEBOOK_ID`, `PUBLER_WORKSPACE_ID`
- Required notification env: `ZEUS_NOTIFY_EMAIL` (recipient address); plus one of `RESEND_API_KEY` / `AGENTMAIL_API_KEY` / (`HERMES_GMAIL_USER`+`HERMES_GMAIL_APP_PASSWORD`)
- Notion archive DB cached at `~/.hermes/notion_ids.json`. First run auto-discovers the database under the parent page id given by `ZEUS_NOTION_HUB_PAGE_ID` (copy the trailing 32-char hex id from your Notion content-hub page URL).
- Python deps: `pip install fal-client requests`

## Quickstart

`pipeline_test.py` is the canonical orchestrator. Don't reinvent — wire new flows by importing from `lib/`.

```bash
cd skills/autonomous-ai-agents/multi-agent-content-pipeline/scripts
export $(grep -v '^#' ~/.hermes/.env | xargs)

# Generate + archive to Notion + email summary (no posting yet — safe mode)
python3 pipeline_test.py --type article    --topic "Bitcoin breaks 100K"
python3 pipeline_test.py --type carousel   --topic "..." --slides 4
python3 pipeline_test.py --type short_video --topic "..." --duration 8
python3 pipeline_test.py --type long_video  --topic "..." --duration 10

# Same flow but also post to Publer
python3 pipeline_test.py --type article --topic "..." --publish
```

Library at `skills/autonomous-ai-agents/multi-agent-content-pipeline/lib/`:
- `content_types.py` — `ContentType` enum + `ContentPiece` dataclass (single object that flows everywhere)
- `fal.py` — `generate_image`, `generate_video_kling`, `generate_music`, `download`, `kling_cost`
- `fish.py` — `synthesize` (fish.audio TTS, mp3 binary -> local path)
- `notion.py` — `NotionArchive` (auto-discovers archive DB; only writes properties that exist in schema)
- `platforms.py` — `LIMITS`, `READ_MORE_TRIGGER`, `needs_thread`, `split_thread`, `validate_lengths`
- `ledger.py` — `append_entry`, `summary` (always-on JSONL ledger)
- `email_notify.py` — `send_pipeline_summary` (Resend / AgentMail / Gmail / file fallback)

## Budget Constraints (HARD LIMITS)

- **Video**: MAX $0.07/second after the $0.35 first-5s base on Kling Turbo Pro.
- **Short-form video format**: 1080x1920 (9:16), <90s. Mobile-native for TikTok, Reels, Shorts, YouTube Shorts.
- **Long-form video format**: 1920x1080 (16:9). Posted to YouTube, Twitter, LinkedIn, Reddit.
- **Cost tracking**: every run appends to the JSONL ledger; the post-run email shows this-run + 24h + 7d + 30d + all-time totals automatically.
- **Free-first** still applies for music — only pay for TTS (fish.audio is the chosen paid TTS).
- **Self-upgrading**: after every fix or smart solution, update this skill immediately.

## Workflow (execute in this order)

### 0. Fetch real-time data (MANDATORY for finance content)

Stale prices in images/posts are a hard failure. Fetch live numbers via Google Finance / Yahoo / CoinGecko before generating anything. Pass the verified numbers to both the article prompt AND the image prompt. GPT models hallucinate training-era prices otherwise.

### 1. Generate article text

`pipeline_test.py:generate_article_text` calls OpenRouter with constraints tuned per type so descriptions clear "read more" on every visual platform:
- Article / Carousel: 550-900 chars body
- Short video script: 300-500 chars
- Long video script: 700-1200 chars
- First line is the title (5-10 words, no dates)

### 2. Generate platform variants (single LLM call, JSON mode)

`generate_variants` requests every target platform's variant in one JSON-mode call. If `needs_thread(body)` returns True (>480 chars), the model is asked for a `twitter_thread` array; otherwise a single `twitter` string. `validate_lengths` flags any variant that exceeds the platform's hard limit.

### 3. Generate media

Dispatch by type:
- **Article**: 1 image via `generate_image(prompt=body[:1000], width=1024, height=1024, quality="medium")`
- **Carousel**: 3-5 image prompts derived from the article via a second LLM call (slide-by-slide narrative), each fed through `generate_image`
- **Short video**: `generate_video_kling(prompt=body[:800], aspect_ratio="9:16", duration_s=5..10)`
- **Long video**: same but `aspect_ratio="16:9"`. Single call maxes ~10s — chain calls + ffmpeg-stitch for longer outputs

### 3-B. Audio for video pieces (fish.audio narration + fal music)

```python
from lib.fish import synthesize as fish_synthesize
from lib.fal import generate_music

narration_path, narr_cost = fish_synthesize(text=narration_script, out_path="/tmp/narr.mp3", reference_id=os.getenv("ZEUS_FISH_VOICE_DEFAULT"))
music_url, music_cost = generate_music(prompt="tense, modern, news-broadcast", duration_s=10)
piece.add_cost("fish-s1", narr_cost, kind="tts")
piece.add_cost("cassetteai-music", music_cost, kind="music")
```

Then mix with ffmpeg (narration at 1.5x, music at 0.3x) and merge into the video.

### 4. Download every asset locally

Always `download(url, dest_path)` after generation — fal output URLs can expire and the only reason we are now archiving is because the previous pipeline lost everything.

### 5. Archive to Notion (BEFORE posting)

```python
archive = NotionArchive()  # auto-discovers archive DB under ZEUS_NOTION_HUB_PAGE_ID
archive.archive(piece)     # creates page; piece.notion_page_id is set
```

The archive writer reads the DB schema once and only sets properties that exist — works whether the user's archive DB has Title/Status/Cost or a different subset. Body, images, and video are also rendered as page block content as a fallback.

### 6. Upload media to Publer

```
POST https://app.publer.com/api/v1/media
Headers: Authorization: Bearer-API <key>, Publer-Workspace-Id: <id>
Body: multipart file=<bytes>
```

### 7. Schedule posts (one platform at a time)

`POST /api/v1/posts/schedule`. For images use `type: "photo"`. For videos use `type: "video"` everywhere except Instagram which requires `type: "reel"` (type "video" silently fails on IG).

After scheduling, give Publer ~8s, then `GET /api/v1/posts?limit=30` and pluck `post_link` for each account_id — that's the permalink that ends up in the email and Notion.

### 8. Update Notion + ledger + email

```python
archive.update_status(piece)   # Status: Posted, Posted At, Job IDs
ledger_append(piece)           # always-on cost ledger
send_pipeline_summary(piece)   # email with post links + run cost + 24h/7d/30d/all-time totals
```

## Pitfalls

### Pitfall: Stale or hallucinated prices in finance content
**Solution**: Step 0 is mandatory. Fetch live prices, pass into prompts. Verify final numbers in image/post match Step 0.

### Pitfall: Wrong Publer base URL or auth
**Solution**: Base is `https://app.publer.com/api/v1` (NOT `.io` — `app.publer.io` returns Cloudflare 1010). Auth is `Bearer-API` (NOT plain `Bearer`). Always include `Accept: application/json`, `User-Agent: Mozilla/5.0`, and `Origin: https://app.publer.com` headers.

### Pitfall: Publer multi-account bulk posting bug
**Solution**: Post one platform at a time. The bulk endpoint returns "composer is in a bad state" errors.

### Pitfall: Publer job_status "complete" doesn't mean the post was created
**Solution**: job_status "complete" only means the scheduling job processed. Verify by fetching `/api/v1/posts?limit=30` and confirming a post for that account exists with `state: "published"` and non-null `post_link`. Missing = reschedule.

### Pitfall: Instagram video posts fail silently with type="video"
**Solution**: Instagram Reels must use type `"reel"`. Type `"video"` silently fails — job reports complete but no post appears.

### Pitfall: YouTube community (image/text) posts not supported by Publer
**Solution**: Publer's YouTube integration only handles video. Image/text community posts return "YouTube requires a video attached." So YouTube is in the article/carousel target list ONLY for the long-form video type. For article/carousel, skip YouTube.

### Pitfall: AgentMail inbox ID is the full email
**Solution**: Inbox ID is the full email address (e.g. `your-inbox@agentmail.to`), not just the local part. Short name returns 404.

### Pitfall: Twitter blocks duplicate text across accounts/posts
**Solution**: Vary the text per platform. The variant generation call already does this; never copy-paste the article verbatim across platforms.

### Pitfall: Notion 2025-09-03 API silently drops properties
**Solution**: Use `Notion-Version: 2022-06-28` for all database operations. The `lib/notion.py` module already enforces this.

### Pitfall: Notion archive DB schema doesn't match the property names we write
**Solution**: `NotionArchive._build_properties` checks each candidate property against the DB schema and only sends fields that exist with the matching type. Adding new property names in the user's Notion template just works on the next run; renaming an existing one means we silently skip until that name is back. If a write seems to be missing fields, run the discovery cycle: delete `~/.hermes/notion_ids.json` and rerun once to re-fetch the schema.

### Pitfall: Title is just a date
**Solution**: The article prompt explicitly forbids dates as titles. Extract first line; if it parses as a date, regenerate.

### Pitfall: Image has article title or date burned in
**Solution**: Image prompt forbids titles/dates. Image conveys data only — title lives in caption.

### Pitfall: Twitter article over 480 chars posted as a single (truncated) tweet
**Solution**: `needs_thread(body)` is True at 480+ chars. The variants call requests a `twitter_thread` array. Notion archives the full thread; for now `pipeline_test.py` posts the lead tweet only via Publer (full chained-tweet posting is a TODO).

### Pitfall: Silent video (no audio track)
**Solution**: Every video MUST have audio. Use fish.audio for narration + fal music + ffmpeg merge. Verify with `ffprobe` before posting that the output has both video and audio streams.

### Pitfall: Narration longer than the video clip
**Solution**: Match script length to video duration (~15 words per 5s for normal pace). Trim narration with `ffmpeg -t <duration>` before mixing.

### Pitfall: fal output URLs expire
**Solution**: Always `download(url, dest_path)` immediately after generation. The local path is what Publer uploads from and what survives if the fal CDN rotates.

## Verification Checklist

- [ ] Step 0 (finance only): real-time prices fetched
- [ ] Article generated with title (first line) AND body cleared per-type length budget
- [ ] Variants generated; `validate_lengths` returned no errors; `needs_thread` triggered the thread variant when applicable
- [ ] Media generated for the chosen type:
  - Article: exactly 1 image
  - Carousel: 3-5 images
  - Short video: Kling 9:16, duration 5-10s, 1080x1920
  - Long video: Kling 16:9, duration 5-10s, 1920x1080
- [ ] All assets `download()`'d to local paths
- [ ] For video: fish.audio narration + fal music + ffmpeg merge done; `ffprobe` confirms audio stream
- [ ] `archive.archive(piece)` returned a Notion page id BEFORE any Publer call
- [ ] Publer media upload returned ids
- [ ] Each platform post scheduled one at a time; job_status complete; `/api/v1/posts` confirms `post_link`
- [ ] `archive.update_status(piece)` set Posted + Job IDs
- [ ] `ledger_append(piece)` added a row to `~/.hermes/zeus_cost_ledger.jsonl`
- [ ] `send_pipeline_summary(piece)` returned a non-`file` backend (or, if `file`, user has been told to configure RESEND/AGENTMAIL/GMAIL)
