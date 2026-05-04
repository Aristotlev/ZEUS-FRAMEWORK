# Publer API Reference — Full Debugging Trail

## Quick Reference (THE CORRECT VALUES)

| Property      | Value                                                                              |
|---------------|------------------------------------------------------------------------------------|
| Base URL      | `https://app.publer.io` (NOT `.com`, NOT `api.publer.io`)                         |
| Create Post   | `POST https://app.publer.io/posts` (NO `/api/v1` prefix)                          |
| Auth Header   | `Authorization: Bearer-API <token>` (NOT plain `Bearer`)                          |
| Content-Type  | `application/json`                                                                 |
| Accept        | `application/json` (otherwise returns HTML login page even on 200)                |
| Plan Required | Business or Enterprise ($??/month) — **API locked on free/pro plans**             |

## DNS & Domain Quirks (critical for WSL)

- `publer.io` → 301 redirect to `publer.com` (the marketing site)
- `app.publer.io` → RESOLVES, is the actual app/API domain
- `api.publer.io` → DOES NOT RESOLVE (NXDOMAIN)
- `docs.publer.io` → DOES NOT RESOLVE (NXDOMAIN)
- `app.publer.com` → EXISTS but is login-only; `/api/v1/*` paths return 404 HTML

**The API docs (GitBook) at `help.publer.com` show `app.publer.com/api/v1` as the base URL, but that path DOES NOT EXIST — all requests return 404 Rails HTML. The actual working domain is `app.publer.io` without the `/api/v1` prefix.**

## Authentication

```
Authorization: Bearer-API <api_token>
Content-Type: application/json
Accept: application/json
```

Without `Accept: application/json`, the API returns 200 with HTML login page instead of 401/error JSON.

## Token Management

Generate/regenerate tokens in Publer Dashboard → Settings → Access & Login → API Keys.

**Token format**: hex string, e.g. `c97597e68c613a7e1101dcc9c41070cf5b11d400ed5dd8d8`

**Plan requirement**: API access is available exclusively to **Business and Enterprise** customers. Free/Professional plans CANNOT use the API — all requests return 401 regardless of valid tokens.

## Endpoints Tested

### Create Post (working endpoint)
```
POST https://app.publer.io/posts
Authorization: Bearer-API <token>
Content-Type: application/json
Accept: application/json

{
  "text": "Post content",
  "photo_url": "https://...",   // optional
  "status": "schedule" | "draft" | "publish",
  "utc": false,
  "shorten_links": true
}
```

## All Failed Attempts (do not retry)

These all return 404 or 401:
- `https://app.publer.com/api/v1/me` → 404 Rails HTML (path doesn't exist)
- `https://app.publer.com/api/v1/posts` → 404 Rails HTML
- `https://app.publer.io/api/v1/posts` → 404 JSON `{"status":404,"error":"Not Found"}`
- `https://app.publer.io/api/v1/post/create` → 404
- `https://app.publer.io/api/v1/schedule` → 404
- `https://app.publer.io/posts` with `Accept: application/json` but no plan → 401 `{"error":""}`
- `https://app.publer.io/posts` without `Accept: application/json` → 200 but body is HTML login page

## Auth Method Variations Tested (all failed without Business plan)

| Method                                    | Result |
|-------------------------------------------|--------|
| `Bearer-API <token>`                     | 401    |
| `Bearer <token>`                          | 401    |
| `Token <token>`                           | 401    |
| Raw token (no scheme)                     | 401    |
| `X-API-Key: <token>`                     | 401    |
| `Publer-Workspace-Id: default` header    | 401    |

## Documentation Sources

- GitBook API docs: accessed via `help.publer.com` → search "API" → click "Does Publer have a Public API?" → click "here" link
- Docs show `app.publer.com/api/v1` but this path is non-functional
- Postman collection available for download from docs
- Contact: `support@publer.com` for API issues
