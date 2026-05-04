     1|---
     2|name: content-publish-workflow
     3|description: "End-to-end content generation + publishing: article → image → platform variants → Publer all platforms → Notion save."
     4|triggers:
     5|  - "publish content"
     6|  - "generate and post"
     7|  - "create article and post everywhere"
     8|  - "content pipeline run"
     9|  - "post to all platforms"
    10|  - "content post"
    11|---
    12|
    13|# Content Publish Workflow
    14|
    15|TWO SEPARATE PIPELINES. Never interleave. If user wants a video, run ONLY the video pipeline. If user wants image+article, run ONLY the image pipeline. Mixing them in one run causes duplicate posts, wrong media types, and platform confusion.
    16|
    17|## CRITICAL: Pipeline Selection
    18|
    19|- **IMAGE pipeline**: Article → Image (GPT Image 2) → 4 platforms (Twitter, Instagram, LinkedIn, TikTok — NO YouTube for images) → Notion save → Email
    20|- **VIDEO pipeline**: Article → Video (Seedance 2.0/Replicate) → ALL 5 platforms with video (Twitter video, Instagram Reel, LinkedIn video, TikTok video, YouTube video) → Notion save → Email
    21|
    22|**SHORT-FORM VIDEO ALWAYS GOES TO ALL 5 PLATFORMS.** User mandate. Never post a video to only 2 platforms. Twitter gets video. Instagram gets type="reel". LinkedIn gets video. TikTok gets video. YouTube gets video. Every time.
    23|
    24|## Prerequisites
    25|
    26|- `OPENROUTER_API_KEY`, `PUBLER_API_KEY`, `REPLICATE_API_KEY`, `NOTION_API_KEY` in `~/.hermes/.env`
    27|- Notion IDs cached at `~/.hermes/notion_ids.json`
    28|- Publer workspace: your-workspace-id
    29|- **REPLICATE AUTH**: Must sign into Replicate via GitHub (replicate.com → Sign in with GitHub) to unlock premium models (Kling, Seedance, Kokoro). Without sign-in, only Wan 2.1 and basic models are accessible. Fresh API tokens generated AFTER sign-in are required.
    30|
    31|## Budget Constraints (HARD LIMITS)
    32|
    33|- **Video**: MAX $0.07/second of output. Primary model Kling 2.5 Turbo at $0.07/s hits this exactly.
    34|- **Video format**: ALL short-form videos (< 90 seconds) MUST be 1080x1920 (9:16 portrait), 1080p resolution. Mobile-native for TikTok, Reels, Shorts, YouTube Shorts.
    35|- **Cost tracking**: Include YOUR OWN LLM costs (OpenRouter) in every email cost breakdown. Format: `Agent: deepseek/deepseek-v4-pro (~$0.015/session for pipeline runs)`
    36|- **Free-first**: Always default to free solutions before paid APIs. edge-tts for TTS narration is free and high quality. Only use paid TTS (Kokoro) when voice quality requirements demand it.
    37|- **Self-upgrading**: After every fix, new finding, or smart solution, update this skill immediately. User mandate: "I want you to constantly upgrade yourself with every fix and smart solution we find."
    38|
    39|## Workflow (execute in this order)
    40|
    41|### 0. Fetch Real-Time Market Data (MANDATORY — do before any content)
    42|
    43|**CRITICAL**: Stale data in images/posts is a hard failure. Always fetch current prices before writing anything.
    44|
    45|Use execute_code (NOT terminal) for API calls — shell `&` in URLs gets interpreted as backgrounding.
    46|
    47|Determine which tickers/sectors the article will cover, then grab live prices using CoinGecko (no API key needed). For crypto:
    48|
    49|```bash
    50|python3 -c "
    51|import urllib.request, re
    52|tickers = ['NVDA', 'AAPL', 'SPY', 'QQQ', 'TSLA']  # adjust to topic
    53|for t in tickers:
    54|    url = f'https://www.google.com/finance/quote/{t}:NASDAQ' if t not in ['SPY','QQQ'] else f'https://www.google.com/finance/quote/{t}:NYSEARCA'
    55|    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    56|    html = urllib.request.urlopen(req, timeout=10).read().decode()
    57|    prices = re.findall(r'[\"\']([\d,]+\.[\d]{2})[\"\']', html)
    58|    for p in prices[:5]:
    59|        val = float(p.replace(',',''))
    60|        if 50 < val < 1000:
    61|            print(f'{t}: \${val}')
    62|            break
    63|"
    64|```
    65|
    66|Alternative if Google Finance blocks: use Yahoo Finance page scraped the same way (User-Agent header required).
    67|
    68|Collect ALL prices before proceeding. Pass them to the article prompt as CURRENT MARKET DATA.
    69|
    70|### 1. Generate Article With Title
    71|Use OpenRouter with `google/gemini-2.5-flash`. 
    72|
    73|**Prompt**: "Write a sharp financial article. First line must be a unique, punchy title (5-10 words) that captures the article's main insight. Then the body. CURRENT MARKET DATA: [paste prices from Step 0]. Use these exact current prices — do not hallucinate or estimate. Include data points, sector moves, forward-looking take. 200-300 words total. Sound like Bloomberg Terminal condensed. Never use dates as titles."
    74|
    75|**Extract the title** (first line) as the post title. Use it for the Notion page name and as the Publer post title field.
    76|
    77|### 2. Generate Image — Raw Article as Prompt
    78|Use Replicate `openai/gpt-image-2`. **VALID ASPECT RATIOS**: ONLY `1:1`, `3:2`, `2:3` — the model rejects `4:5`. Use `2:3` for portrait/vertical (closest to Instagram-native). Endpoint: `POST https://api.replicate.com/v1/models/openai/gpt-image-2/predictions`.
    79|
    80|**Prompt construction**: Feed the raw article text directly as the prompt — NO style prefix, no "Professional financial visualization" wrapper. The user wants the model to interpret the article naturally without forcing a specific visual style. Truncate to ~1000 chars if needed. Do NOT add wrapper prefixes unless the user explicitly asks for a specific style.
    81|
    82|**Critical**: GPT Image 2 does NOT accept `quality` parameter — only `prompt` + `aspect_ratio`. Gpt-image-2 only supports 1:1, 3:2, 2:3 aspect ratios.
    83|
    84|Budget fallback: if daily media spend > budget, use quality="low" ($0.012 instead of $0.047).
    85|
    86|### 2-B. VIDEO PIPELINE — Generate Video (for video posts)
    87|
    88|**VIDEO MODEL HIERARCHY** (Replicate, authenticated API key — requires GitHub sign-in):
    89|
    90|| Priority | Model | Duration | Audio | Resolution | Cost/sec | Status |
    91||----------|-------|----------|-------|------------|----------|--------|
    92|| 1st | `bytedance/seedance-2.0` | -1 to 15s (intelligent) | Native! | 480p/720p/1080p | Variable | ✓ Unlocked |
    93|| 2nd | `kwaivgi/kling-v2.5-turbo-pro` | 5 or 10s | None | 720p | $0.07/s | ✓ Unlocked |
    94|| 3rd | `kwaivgi/kling-v2.6` | Up to 15s | Native! | 1080p | ~$0.05/s | ✓ Unlocked |
    95|| Fallback | `wavespeedai/wan-2.1-t2v-720p` | 5-10s | None | 720p | ~$0.03/s | ✓ Works |
    96|| Future | `wan-2.7`, `kling-v3-omni` | 15s+ | Native | 1080p | TBD | Unlocked, untested |
    97|
    98|**PRIMARY: Seedance 2.0** — endpoint: `POST https://api.replicate.com/v1/models/bytedance/seedance-2.0/predictions`
    99|
   100|Why: Native audio (dialogue, sound effects, background music), intelligent duration (-1 = model picks best length), 1080p, 9:16 ratio, character consistency via reference images. The native audio with `generate_audio=true` means we get synced sound out of the box without separate music generation.
   101|
   102|```json
   103|{
   104|  "prompt": "News broadcast style, dramatic lighting, geopolitical tension: [article headline + visual description]. \"[Dialogue in quotes for speech in native audio]\"",
   105|  "aspect_ratio": "9:16",
   106|  "resolution": "1080p",
   107|  "duration": -1,
   108|  "generate_audio": true,
   109|  "seed": null
   110|}
   111|```
   112|
   113|Aspect ratios: `16:9`, `4:3`, `1:1`, `3:4`, `9:16`, `21:9`, `9:21`, `adaptive`. Use `9:16` for short-form social.
   114|
   115|**With native audio**, the model generates its own synced soundtrack — music, ambient sounds, and dialogue (via quoted text in prompt). For videos that need exact narration text (reading the article aloud), use Mode B (Kokoro TTS narration + Seedance video, mixed with ffmpeg). For videos that just need matching sound, use native audio directly.
   116|
   117|**BUDGET PICK: Kling 2.5 Turbo Pro** — endpoint: `POST https://api.replicate.com/v1/models/kwaivgi/kling-v2.5-turbo-pro/predictions`
   118|
   119|Exactly $0.07/second — hits the user's budget cap. No native audio — requires separate audio pipeline. Duration: 5s or 10s. Aspect ratio: `16:9`, `9:16`, `1:1`.
   120|
   121|### 2-C. AUDIO PIPELINE — Add Sound to Video (MANDATORY for video posts)
   122|
   123|User mandate: "videos need to have sound depending on context — some need narration on top of music." All videos require audio. Two modes:
   124|
   125|- **Mode A: Music only** — background soundtrack matching video mood
   126|- **Mode B: Narration + Music** — voiceover on top of background music
   127|
   128|**Pipeline**:
   129|1. Generate music (Replicate)
   130|2. Generate narration (edge-tts FREE, or Replicate TTS)
   131|3. Mix narration over music with ffmpeg
   132|4. Merge audio into video with ffmpeg
   133|
   134|#### Music Generation
   135|
   136|**PRIMARY: `minimax/music-2.6`** on Replicate — `POST https://api.replicate.com/v1/models/minimax/music-2.6/predictions`
   137|
   138|Input params:
   139|```json
   140|{
   141|  "prompt": "[mood description — tense, calm, energetic, dramatic, etc.]",
   142|  "is_instrumental": true,
   143|  "audio_format": "mp3"
   144|}
   145|```
   146|
   147|Cost: ~$0.03 per generation. Generates full instrumental track (~2 min). Trim to video duration with ffmpeg.
   148|
   149|**FALLBACK: `meta/musicgen`** — also on Replicate, 3.4M runs, text-to-music from prompt.
   150|
   151|**Future: Suno, Ace Step** — user identified these as best quality. They live on fal.ai, not Replicate. Check fal.ai when connectivity allows.
   152|
   153|#### Narration (TTS)
   154|
   155|**TTS MODEL HIERARCHY:**
   156|
   157|| Priority | Model | Platform | Voices | Cost | Status |
   158||----------|-------|----------|--------|------|--------|
   159|| 1st | `jaaari/kokoro-82m` | Replicate | 40+ (af_bella, am_adam, etc.) | ~$0.001 | ✓ Unlocked |
   160|| 2nd | `edge-tts` (Microsoft neural) | Local CLI | 20+ en-US voices | FREE | ✓ Works |
   161|| Fallback | `google/gemini-3.1-flash-tts` | Replicate | 30 voices | ~$0.01 | Untested |
   162|| ❌ | `xai/grok-text-to-speech` | Replicate | 5 voices | ~$0.01 | 403 (key lacks xAI access) |
   163|
   164|**PRIMARY: Kokoro-82m** — endpoint: `POST https://api.replicate.com/v1/models/jaaari/kokoro-82m/predictions`
   165|
   166|89.9M runs, StyleTTS2-based, highly reliable. Input params:
   167|```json
   168|{
   169|  "text": "[narration text — keep tight, match video duration]",
   170|  "voice": "af_heart",
   171|  "speed": 1.0
   172|}
   173|```
   174|
   175|**Voice selection (40+ options):**
   176|- Female authoritative: `af_heart`, `af_bella` (default), `af_nicole`
   177|- Male authoritative: `am_adam`, `am_michael`, `am_echo`
   178|- List all: available in browser at replicate.com/jaaari/kokoro-82m → voice dropdown
   179|
   180|For news-style narration use `af_heart` (female) or `am_adam` (male).
   181|
   182|**FALLBACK: edge-tts** — FREE, high quality. Use if Kokoro prediction fails:
   183|```bash
   184|edge-tts --voice "en-US-ChristopherNeural" --text "[narration]" --write-media /tmp/narration.mp3
   185|```
   186|
   187|**Voice selection for edge-tts:**
   188|- Male authoritative news: `en-US-ChristopherNeural` or `en-US-BrianNeural`
   189|- Female news: `en-US-AriaNeural`
   190|- List all: `edge-tts --list-voices | grep en-US`
   191|
   192|Keep narration short — match video duration (~15 words per 5s, ~30 words per 10s). Write tight punchy script from article headline + lead sentence.
   193|
   194|#### Audio Mixing & Merging
   195|
   196|ffmpeg static binary at `~/bin/ffmpeg` (no sudo needed). Steps:
   197|
   198|```bash
   199|# Trim narration to video duration
   200|ffmpeg -y -i narration.mp3 -t <duration> -c:a libmp3lame narration_short.mp3
   201|
   202|# Trim music to video duration  
   203|ffmpeg -y -i music.mp3 -t <duration> -c:a libmp3lame music_short.mp3
   204|
   205|# Mix: narration 1.5x volume OVER music 0.3x volume (narration-forward)
   206|ffmpeg -y -i narration_short.mp3 -i music_short.mp3 \
   207|  -filter_complex "[0:a]volume=1.5[n];[1:a]volume=0.3[m];[n][m]amix=inputs=2:duration=first" \
   208|  -c:a libmp3lame mixed_audio.mp3
   209|
   210|# Merge audio into video
   211|ffmpeg -y -i video.mp4 -i mixed_audio.mp3 -c:v copy -c:a aac -shortest final.mp4
   212|```
   213|
   214|For **music-only mode** (no narration): skip TTS step, use music at 1.0x volume directly.
   215|
   216|### 3. Generate Platform-Optimized Variants
   217|Use OpenRouter with `google/gemini-2.5-flash` + `response_format: {"type": "json_object"}`. Prompt with the full article and request:
   218|
   219|```
   220|Output ONLY valid JSON with these keys:
   221|- twitter: max 280 chars, punchy, no hashtags unless natural
   222|- instagram: visual-first caption, max 2200 chars, hashtags as footer
   223|- linkedin: professional tone, 2 paragraphs
   224|- tiktok: short punchy caption, max 150 chars, 2-3 hashtags
   225|```
   226|
   227|### 4. Upload Image to Publer
   228|```
   229|POST https://app.publer.com/api/v1/media
   230|Headers: Authorization: Bearer-API <key>, Publer-Workspace-Id: your-workspace-id
   231|Body: multipart file=<image bytes>
   232|```
   233|Use `Notion-Version: 2022-06-28` for all Notion API calls (2025-09-03 is broken for database operations).
   234|
   235|### 5. Schedule Posts — IMAGE Pipeline (4 platforms)
   236|
   237|Account IDs (image posts — NO YouTube):
   238|```
   239|twitter:    69f783d1afc106b8869cf50b
   240|instagram:  69f6511c5cf7421d7047fc4e
   241|linkedin:   69f783c63642e046435f7707
   242|tiktok:     69f783de2c63a6ec70868731
   243|```
   244|
   245|### 5-B. Schedule Posts — VIDEO Pipeline (ALL 5 platforms)
   246|
   247|**SHORT-FORM VIDEO ALWAYS GOES TO ALL 5.** Platform-specific types:
   248|
   249|| Platform | Publer type | Account ID |
   250||----------|------------|------------|
   251|| Twitter | `video` | 69f783d1afc106b8869cf50b |
   252|| Instagram | **`reel`** (NOT "video"!) | 69f6511c5cf7421d7047fc4e |
   253|| LinkedIn | `video` | 69f783c63642e046435f7707 |
   254|| TikTok | `video` | 69f783de2c63a6ec70868731 |
   255|| YouTube | `video` | your-workspace-id |
   256|
   257|Instagram must use type `"reel"` — type `"video"` silently fails (job "complete" but post doesn't appear). Post one platform at a time.
   258|
   259|Payload for IMAGE:
   260|```json
   261|{"bulk":{"state":"scheduled","posts":[{"networks":{"<provider>":{"type":"photo","text":"<text>","media":[{"id":"<img_media_id>"}]}},"accounts":[{"id":"<id>","scheduled_at":"<ISO>"}]}]}}
   262|```
   263|
   264|Payload for VIDEO:
   265|```json
   266|{"bulk":{"state":"scheduled","posts":[{"networks":{"<provider>":{"type":"<reel or video>","text":"<text>","media":[{"id":"<vid_media_id>"}]}},"accounts":[{"id":"<id>","scheduled_at":"<ISO>"}]}]}}
   267|```
   268|
   269|Account IDs for reference:
   270|```
   271|twitter:    69f783d1afc106b8869cf50b
   272|instagram:  69f6511c5cf7421d7047fc4e
   273|linkedin:   69f783c63642e046435f7707
   274|tiktok:     69f783de2c63a6ec70868731
   275|youtube:    your-workspace-id — Works with video! For image-only posts, skip YouTube (community posts not supported via API).
   276|```
   277|
   278|**YouTube**: Publer's API rejects all non-video YouTube posts ("YouTube requires a video attached" / "Post type is not valid"). Community tab text/image posts are not accessible via their API. Post to 4 platforms only — skip YouTube.
   279|
   280|Post one platform at a time (Publer bulk bug). Endpoint: `POST /api/v1/posts/schedule`. Payload per platform:
   281|```json
   282|{
   283|  "bulk": {
   284|    "state": "scheduled",
   285|    "posts": [{
   286|      "networks": {
   287|        "<provider>": {
   288|          "type": "photo",
   289|          "text": "<platform_variant>",
   290|          "media": [{"id": "<media_id>"}]
   291|        }
   292|      },
   293|      "accounts": [{
   294|        "id": "<account_id>",
   295|        "scheduled_at": "<ISO timestamp 2 min from now>"
   296|      }]
   297|    }]
   298|  }
   299|}
   300|```
   301|
   302|After each post, verify with `GET /api/v1/job_status/{job_id}` — confirm `status: "complete"` and `failures: {}`.
   303|
   304|### 6. Send Email Notification (MUST include costs)
   305|
   306|After all platforms confirmed posted, send email via AgentMail to `user@example.com`:
   307|
   308|**Subject**: "Content Post: [Article Title]"
   309|
   310|**Body format**:
   311|```
   312|TLDR: [2-3 sentence summary of the article]
   313|
   314|Posts:
   315|- Twitter: COMPLETE ✓ | <job_id>
   316|- Instagram: COMPLETE ✓ | <job_id>  
   317|- LinkedIn: COMPLETE ✓ | <job_id>
   318|- TikTok: COMPLETE ✓ | <job_id>
   319|- YouTube: <status> | <job_id>
   320|```json
   321|Costs:
   322|- Article generation: gemini-2.5-flash (~$0.001)
   323|- Image: GPT Image 2 (2:3) | ~$0.047
   324|- Agent LLM: deepseek-v4-pro (~$0.003/turn)
   325|- Total estimated: ~$0.051
   326|```
   327|
   328|**For video posts**:
   329|```json
   330|Costs:
   331|- Article generation: gemini-2.5-flash (~$0.001)
   332|- Video: Wan 2.1 T2V 720p (5s, 720x1280) | ~$0.15 (Replicate)
   333|- Music: Minimax Music 2.6 (instrumental) | ~$0.03 (Replicate)
   334|- Narration: edge-tts Microsoft (FREE)
   335|- Mixing/merge: ffmpeg local (FREE)
   336|- Agent LLM: deepseek-v4-pro (~$0.015/session for pipeline runs)
   337|- Total estimated: ~$0.196
   338|```
   339|
   340|### 7. Save to Notion Content Pipeline DB (MUST include costs)
   341|
   342|Notion-Version: `2022-06-28`. Database ID from `~/.hermes/notion_ids.json` → `pipeline_db_id`.
   343|
   344|Create one page per platform variant + one for the full article. Properties:
   345|```json
   346|{
   347|  "Title": {"title": [{"text": {"content": "[Topic] — [Platform]"}}]},
   348|  "Platform": {"select": {"name": "[Platform or All Platforms]"}},
   349|  "Status": {"select": {"name": "Posted"}},
   350|  "Content Type": {"select": {"name": "Tweet/Caption/Article"}},
   351|  "Image URL": {"url": "[replicate url]"},
   352|  "Posted At": {"date": {"start": "[ISO timestamp]"}},
   353|  "Job ID": {"rich_text": [{"text": {"content": "[publer job_id]"}}]},
   354|  "Full Text": {"rich_text": [{"text": {"content": "[text]"}}]},
   355|  "Cost": {"number": 0.047},
   356|  "Model": {"select": {"name": "GPT Image 2 / Minimax Video-01"}}
   357|}
   358|```
   359|
   360|## Session Resumption (CRITICAL — load this skill BEFORE doing anything)
   361|
   362|When a previous session was cut off mid-pipeline (video generating, posting in progress, etc.), the agent's job is to USE session_search to discover the last known state, then EXECUTE forward. DO NOT:
   363|
   364|- Re-probe files on disk to "verify" what exists
   365|- Re-gather market data that was already fetched
   366|- Check `/tmp` for video files to "see if they're still there"
   367|- Run ffprobe/file stat/diagnostic commands on existing assets
   368|- Re-generate content that was already generated in the prior session
   369|
   370|The user is coming back to FINISH, not to watch you re-do investigation. Every diagnostic command you run instead of an action command is a failure. When the user says "we were in the middle of X" — resume at X, not at "let me verify X exists."
   371|
   372|**Correct pattern**: session_search → identify last step → pick up at NEXT step → execute. One diagnostic at most.
   373|
   374|**Wrong pattern** (this session's failure): session_search → run 6+ file probes and data fetches → user rage-quits with interrupts.
   375|
   376|If the user sends multiple messages interrupting your tool calls, they are screaming "STOP INVESTIGATING AND DO SOMETHING." Heed it immediately.
   377|
   378|## Pitfalls
   379|
   380|### Pitfall: Investigation spiral on session resumption
   381|**Solution**: See Session Resumption section above. When resuming a cut-off task, execute forward — don't re-probe. The artifacts from the prior session either exist (use them) or don't (re-generate them without asking). Either way, the answer is always an action call, never a diagnostic call.
   382|
   383|### Pitfall: Replicate premium models blocked (403) — need GitHub sign-in
   384|**Solution**: Models like Kling, Seedance, Kokoro return 403 even when the model page shows they exist. Your Replicate API key must be generated AFTER signing into Replicate via GitHub. Steps: (1) Go to replicate.com, click "Sign in with GitHub" (2) Go to Account → API tokens (3) Generate fresh API token (4) Update `REPLICATE_API_KEY` in `~/.hermes/.env` with `sed -i`. Old tokens issued before sign-in remain unauthenticated. Verify by checking `curl -o /dev/null -w "%{http_code}"` on any premium model — 200 means unlocked.
   385|**Solution**: Wan 2.1 is 3x cheaper ($0.15 vs $0.50) AND better quality. Always try Wan 2.1 first. User explicitly called Minimax "more expensive and shitty compared." Only fall back to Minimax if Wan 2.1 prediction fails.
   386|
   387|### Pitfall: Silent videos (no audio track)
   388|**Solution**: ALL videos MUST have audio. User mandate: "videos need to have sound depending on context." Run the full audio pipeline — music + optional narration + ffmpeg merge. Never post silent video.
   389|
   390|### Pitfall: Narration longer than video
   391|**Solution**: Write tight narration script matching video duration (~15 words per 5s). Always trim with ffmpeg `-t` flag before mixing. edge-tts narration naturally runs ~150 words/min — plan accordingly.
   392|
   393|### Pitfall: TTS models reporting 200 on model info but failing on predictions
   394|**Solution**: A model existing on Replicate does NOT mean your API key has access. xAI Grok TTS returns 200 for model info but 403 for predictions. Suno Bark returns 200 for model info but 404 for predictions. Always verify with an actual prediction attempt before building a workflow around a model. edge-tts is the reliable free fallback.
   395|
   396|### Pitfall: Replicate search API returning garbage results
   397|**Solution**: The `?search=` and `?cursor=` query params on `GET /v1/models` return non-relevant results. Use direct model URL probing: `curl -o /dev/null -w "%{http_code}" https://api.replicate.com/v1/models/<owner>/<name>` to check availability. 200 = model exists, but still verify prediction access.
   398|
   399|### Pitfall: Stale/outdated prices in content and images
   400|**Solution**: Step 0 is MANDATORY. Fetch real-time prices from Google Finance BEFORE generating any content. Pass those exact prices into both the article prompt AND the image prompt. GPT models don't know current stock prices — they'll hallucinate training-data prices (e.g., NVDA at $134 instead of $198). Verify the numbers in the final image match Step 0.
   401|
   402|### Pitfall: Wrong Publer base URL or auth
   403|**Solution**: Base is `https://app.publer.com/api/v1` (NOT `.io` — `app.publer.io` returns Cloudflare 1010 "browser_signature_banned"). Auth is `Bearer-API` (NOT plain `Bearer`). Always include `Accept: application/json`, `User-Agent: Mozilla/5.0`, and `Origin: https://app.publer.com` headers.
   404|
   405|### Pitfall: Using terminal() for API calls with special chars
   406|**Solution**: Shell's `&` in URLs (e.g. `?ids=bitcoin&vs_currencies=usd`) gets interpreted as backgrounding. Use execute_code (Python) for all API calls — no shell escaping issues.
   407|
   408|### Pitfall: GPT Image 2 invalid aspect ratio
   409|**Solution**: Only `1:1`, `3:2`, `2:3` are valid. `4:5` returns 422. Use `2:3` for portrait.
   410|
   411|### Pitfall: YouTube community posts not supported
   412|**Solution**: Publer API only supports YouTube video uploads. Community text/image posts return "Post type is not valid" or "YouTube requires a video attached." Post to 4 platforms only (Twitter, Instagram, LinkedIn, TikTok) — skip YouTube.
   413|
   414|### Pitfall: DeepSeek V4 Pro returns null content
   415|**Solution**: Use `google/gemini-2.5-flash` via OpenRouter for all content generation.
   416|
   417|### Pitfall: Notion 2025-09-03 API creates empty databases
   418|**Solution**: Use `Notion-Version: 2022-06-28` for database operations. Newer version silently drops properties.
   419|
   420|### Pitfall: Image has article title or date burned in
   421|**Solution**: The image prompt explicitly forbids titles and dates. Include only financial data text — tickers, prices, percentages, metrics. The title lives in the post caption, not the image.
   422|
   423|### Pitfall: Title is just a date
   424|**Solution**: The article prompt explicitly requires a unique, punchy title as the first line. Extract it and use for all platforms/Notion. Never use dates as titles — titles must capture the article's main insight.
   425|
   426|### Pitfall: Publer multi-account bulk posting bug
   427|**Solution**: Post one platform at a time, not in bulk. The bulk endpoint returns "composer is in a bad state" errors.
   428|
   429|### Pitfall: Publer job_status "complete" does NOT mean post was created
   430|**Solution**: job_status "complete" only means the scheduling job processed — NOT that the post exists. Twitter/LinkedIn video posts can report "complete" but never appear in `/api/v1/posts`. After scheduling, verify by fetching `/api/v1/posts?limit=20` — confirm post with matching type/account_id exists with `state: "published"` and non-None `post_link`. Missing from list = reschedule.
   431|
   432|### Pitfall: Instagram video posts fail silently with type "video"
   433|**Solution**: Instagram Reels must use type `"reel"`. Type `"video"` silently fails — job_status "complete" but post never appears. Always use `{"instagram": {"type": "reel"}}`.
   434|
   435|### Pitfall: AgentMail inbox ID is full email
   436|**Solution**: Inbox ID is `hermesomni@agentmail.to` (full email), NOT just `hermesomni`. Short name returns 404.
   437|
   438|### Pitfall: Short-form video NOT going to all 5 platforms
   439|**Solution**: SHORT-FORM VIDEO ALWAYS→ALL 5. Twitter(video), Instagram(reel), LinkedIn(video), TikTok(video), YouTube(video). Hard rule.
   440|
   441|### Pitfall: Notion not saved
   442|**Solution**: Save to Notion in TWO phases: (1) after generation (Status:Draft), (2) after posting confirmed (Status:Posted + Job IDs + links).
   443|
   444|### Pitfall: Image+video pipelines interleaved
   445|**Solution**: SEPARATE workflows. Never run both in one pass. Article is shared, everything below diverges.
   446|
   447|## Verification Checklist
   448|- [ ] Step 0: Real-time data fetched via CoinGecko / RSS headlines
   449|- [ ] Article generated with unique title (first line) AND body ≥ 200 chars
   450|- [ ] Article prices/numbers match Step 0 data (no hallucinated data)
   451|- [ ] Image: GPT Image 2 (2:3 aspect, raw article as prompt) — status = succeeded
   452|- [ ] Video: Seedance 2.0 (9:16, 1080p, generate_audio=true, duration=-1) OR Kling 2.5 Turbo (9:16) — status = succeeded
   453|- [ ] Native audio: if using Seedance with generate_audio=true, verify audio stream exists in output
   454|- [ ] Narration (if separate): Kokoro-82m (voice: af_heart for female, am_adam for male) OR edge-tts FREE fallback
   455|- [ ] Audio: narration + music mixed with ffmpeg (narration 1.5x, music 0.3x)
   456|- [ ] Final video: audio merged into video with ffmpeg — verified via ffprobe (stream audio, video)
   457|- [ ] Media uploaded to Publer (media ID returned, validity shows all platforms true)
   458|- [ ] All 5 platforms scheduled (Twitter, Instagram, LinkedIn, TikTok, YouTube — video works on all)
   459|- [ ] All job statuses = complete, failures = {}
   460|- [ ] Email notification sent to user@example.com WITH COSTS
   461|- [ ] Notion pages saved WITH cost and model fields
   462|