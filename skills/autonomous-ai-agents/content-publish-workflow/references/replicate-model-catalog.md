# Replicate Model Access Catalog

Discovered May 3, 2026. Standard Replicate API key (`r8_RaKmy...`).

## Accessible Models (✓ Works)

| Model | Type | Quality | Cost | Notes |
|-------|------|---------|------|-------|
| `wavespeedai/wan-2.1-t2v-720p` | Video (T2V) | Best available | ~$0.15 (5-10s, ~$0.03/s) | 9:16 or 16:9. Fast/Balanced/Off modes. |
| `minimax/video-01` | Video (T2V) | Good | ~$0.50 (6s, ~$0.08/s) | No aspect_ratio param. 3x more expensive. FALLBACK ONLY |
| `minimax/music-2.6` | Music | High | ~$0.03/gen | Instrumental or with lyrics |
| `meta/musicgen` | Music | Good | ~$0.02/gen | 3.4M runs, text-to-music |
| `openai/gpt-image-2` | Image | Best | $0.047 (medium) | 1:1, 3:2, 2:3 aspect ONLY |

## 403 Forbidden (exists but key lacks access)

These need account-level enablement on replicate.com or use fal.ai instead.

### Video (403)

| Model | Runs | Notes |
|-------|------|-------|
| `kwaivgi/kling-v3-omni-video` | 435k | Kling 3.0 Omni — native audio, multi-shot |
| `kwaivgi/kling-v3-video` | 176k | Kling 3.0 — 15s max, native audio |
| `kwaivgi/kling-v2.6` | 615k | Kling 2.6 Pro — native audio |
| `kwaivgi/kling-v2.5-turbo-pro` | 2M | Kling 2.5 Turbo Pro |
| `bytedance/seedance-2.0` | 139k | Seedance 2.0 — native audio, multimodal |
| `google/veo-3.1-lite` | 18k | Native audio, cost-efficient |
| `alibaba/happyhorse-1.0` | 3k | 3-15s, 1080p |
| `xai/grok-imagine-video` | 687k | With audio |
| `wan-video/wan-2.7-i2v` | 13k | Wan 2.7 I2V |

### TTS (403)

| Model | Runs | Notes |
|-------|------|-------|
| `jaaari/kokoro-82m` | 89.9M | Kokoro v1.0 — 82M StyleTTS2. USER WANTS THIS |
| `google/gemini-3.1-flash-tts` | 26k | 30 voices, 70+ languages |
| `xai/grok-text-to-speech` | — | 200 model info but 403 predictions |

## 404 Not Found

Suno, Ace Step, Wan 2.2 — not on Replicate at all. Probably fal.ai exclusive.

## fal.ai

Key: `33ac7535-...` — **Account locked: Exhausted balance.** Top up at fal.ai/dashboard/billing.

Presence confirmed (403 balance not 404 model-missing): `fal-ai/kling`, `fal-ai/seedance-2`, `fal-ai/kokoro`.

## Local Tools (Free, Always Available)

| Tool | Purpose | Location |
|------|---------|----------|
| `edge-tts` | Microsoft neural TTS | pip package |
| `ffmpeg/ffprobe` | Audio/video mixing | `/home/user/bin/ffmpeg` (static) |
