# Publer API Reference (Discovered May 2026)

## Correct Endpoints
- **Base URL**: `https://app.publer.com/api/v1` (NOT `app.publer.io`, NOT `api.publer.io`)
  - `app.publer.io` returns Cloudflare 1010 "browser_signature_banned" — DO NOT USE
- **Auth**: `Authorization: Bearer-API YOUR_KEY` (NOT just `Bearer`!)
- **Required headers**:
  - `Publer-Workspace-Id: <workspace_id>`
  - `Accept: application/json`
  - `User-Agent: Mozilla/5.0`
  - `Origin: https://app.publer.com`
- **Plan required**: Business or Enterprise (API locked on free/pro trials)
- **Use execute_code (Python) for all API calls** — terminal() breaks on `&` in URLs

## Key Endpoints
| Action | Method | Path |
|--------|--------|------|
| List workspaces | GET | `/api/v1/workspaces` |
| List accounts | GET | `/api/v1/accounts` |
| Schedule posts | POST | `/api/v1/posts/schedule` |
| Upload media | POST | `/api/v1/media` (multipart, field: `file`) |
| List scheduled posts | GET | `/api/v1/posts?state=scheduled` |
| Job status | GET | `/api/v1/job_status/{job_id}` |
| Delete post | DELETE | `/api/v1/posts/{id}` |

## Schedule Post Payload Format
```json
{
  "bulk": {
    "state": "scheduled",
    "posts": [{
      "networks": {
        "<provider>": {
          "type": "photo",
          "text": "post text",
          "media": [{"id": "PUBLER_MEDIA_ID"}]
        }
      },
      "accounts": [{
        "id": "<account_id>",
        "scheduled_at": "2026-05-03T21:00:00"
      }]
    }]
  }
}
```

## Image Workflow (MANDATORY — 3 steps)
1. **Download** external image (e.g., Replicate) to bytes
2. **Upload** to Publer: `POST /api/v1/media` with multipart `file=<bytes>`
3. **Reference** in post: `"media": [{"id": "<returned_media_id>"}]`

## Critical Pitfalls
- **`"photo": "<url>"` DOES NOT WORK** — "undefined method 'count' for nil" error
- **`"photo": "<media_id>"` stream field** — same error
- Only `"media": [{"id": "<id>"}]` works for images
- **Text-only posts**: use `"type": "status"` with just `"text"` field
- **YouTube**: Publer API ONLY supports video uploads for YouTube. Community text/image posts return "Post type is not valid" or "YouTube requires a video attached" regardless of type field. FOR VIDEO POSTS: use `"type": "video"` — YouTube WORKS with video. FOR IMAGE POSTS: skip YouTube, only post to 4 platforms.
- **Video posts**: use `"type": "video"` with media id — works on ALL 5 platforms (Twitter, Instagram, LinkedIn, TikTok, YouTube).
- **Duplicate tweets**: Twitter blocks identical text across accounts. Always vary tweet text.
- **Multi-account bulk**: Publer API bug — "The composer is in a bad state for this account as it's missing the social network params." Post one account at a time for reliability.
- **Cloudflare block**: Must include `User-Agent` and `Origin` headers or Cloudflare returns 1010.

## Workspace & Account Discovery
- `GET /api/v1/workspaces` returns list with `id`, `name`, `plan`
- `GET /api/v1/accounts` returns list with `id`, `name`, `provider` (twitter/instagram/linkedin/tiktok/youtube), `type`, `status`
- Account IDs needed for `accounts[].id` in schedule payloads

## Providers
Supported: `twitter`, `instagram`, `linkedin`, `tiktok`, `youtube`, `facebook`, `pinterest`, `google`, `wordpress`, `telegram`, `mastodon`, `threads`, `bluesky`

## Observed Workspace (user)
- Email: user@example.com
- Workspace: ContentPipeline (your-workspace-id)
- Plan: business (trial)
- Connected: twitter, instagram, linkedin, tiktok, youtube
