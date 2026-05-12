"""
Substack publishing client for the Zeus content pipeline.

Substack has no public API — we drive the same private endpoints the web app
uses, authenticated with the `substack.sid` session cookie from a logged-in
browser. The cookie lasts months (default ~90 days from issue); when it
expires the next call returns 401/403 and the pipeline surfaces "FAILED:
substack.sid expired" on the run-completion email so the user knows to
re-grab it.

Cookie name note: pre-flight reading mentioned `connect.sid` (the
Express-session default), but the cookie Substack actually sets in production
is named `substack.sid`. Both share the express-session `s%3A...` prefix.

Two endpoints we use:
  POST  /api/v1/drafts                — create draft (title, subtitle, body, cover)
  POST  /api/v1/drafts/{id}/publish   — flip the draft live
  POST  /api/v1/comment/feed          — publish a Note (short-form cross-post)

Body format: Substack drafts take ProseMirror JSON as a string in `draft_body`.
The Zeus writer produces plain prose with \\n\\n paragraph breaks, so we wrap
each paragraph in a {type: paragraph} node — no Markdown/HTML conversion needed.

Env contract:
  SUBSTACK_PUBLICATION_URL   — e.g. "https://omnifolio1.substack.com" or
                               a custom-domain mapped to one. Required.
  SUBSTACK_SID               — value of the `substack.sid` cookie (paste
                               exactly what Chrome's DevTools shows — keep the
                               leading `s%3A` and trailing signature). Required.

This module raises SubstackAuthError on 401/403 so callers can distinguish
"creds went stale" from generic publish failures and emit the right alert.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

log = logging.getLogger("zeus.substack")


class SubstackError(Exception):
    """Generic Substack publish failure."""


class SubstackAuthError(SubstackError):
    """401 from Substack — the connect.sid cookie has expired. User must re-grab."""


def _publication_url() -> str:
    url = os.getenv("SUBSTACK_PUBLICATION_URL", "").strip().rstrip("/")
    if not url:
        raise SubstackError("SUBSTACK_PUBLICATION_URL not set")
    if not url.startswith("http"):
        url = f"https://{url}"
    return url


def _cookie() -> str:
    # Accept SUBSTACK_SID (the right name — matches the actual cookie
    # `substack.sid`) and fall back to the legacy SUBSTACK_CONNECT_SID
    # name so an old env file doesn't silently break the pipeline if it
    # was already set before the rename.
    sid = (os.getenv("SUBSTACK_SID") or os.getenv("SUBSTACK_CONNECT_SID") or "").strip()
    if not sid:
        raise SubstackError("SUBSTACK_SID not set")
    return sid


def _headers() -> dict:
    # Substack's anti-bot layer expects a real browser UA + an Origin matching
    # the publication. Without these, /api/v1/drafts intermittently returns 403
    # even with a valid session cookie.
    pub = _publication_url()
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin": pub,
        "Referer": f"{pub}/publish/home",
    }


def _cookies() -> dict:
    # Substack's session cookie is named `substack.sid` (not `connect.sid` —
    # that's the express-session default but Substack overrides it).
    return {"substack.sid": _cookie()}


def _request(method: str, path: str, *, json_body: Optional[dict] = None, timeout: int = 30) -> dict:
    url = f"{_publication_url()}{path}"
    r = requests.request(
        method, url, headers=_headers(), cookies=_cookies(), json=json_body, timeout=timeout,
    )
    if r.status_code == 401:
        raise SubstackAuthError(
            "Substack returned 401 — substack.sid cookie expired. "
            "Log in to Substack in a browser, copy the substack.sid cookie value, "
            "and update SUBSTACK_SID in your env."
        )
    if r.status_code == 403:
        # 403 is sometimes the same expired-session signal Substack picks
        # instead of 401 — surface it the same way so the user re-grabs the
        # cookie rather than chasing a "permissions" red herring.
        raise SubstackAuthError(
            f"Substack returned 403 — likely substack.sid expired or publication "
            f"mismatch. Body: {r.text[:200]}"
        )
    if r.status_code >= 400:
        raise SubstackError(
            f"Substack {method} {path} failed {r.status_code}: {r.text[:300]}"
        )
    try:
        return r.json()
    except ValueError:
        return {}


def _prosemirror_body(text: str) -> str:
    """Convert plain prose with \\n\\n paragraph breaks to a stringified
    ProseMirror doc. Empty paragraphs are dropped so a trailing newline
    doesn't ship as an empty block.
    """
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [""]
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": p}] if p else [],
            }
            for p in paragraphs
        ],
    }
    return json.dumps(doc, ensure_ascii=False)


def publish_post(
    *,
    title: str,
    subtitle: str = "",
    body: str,
    cover_image_url: Optional[str] = None,
    audience: str = "everyone",
) -> str:
    """Create a draft and publish it. Returns the public post URL.

    `audience` accepts "everyone" (free + paid) or "only_paid". The pipeline's
    default is "everyone" — these are SEO/news pieces, not subscriber-only.
    """
    draft_payload: dict = {
        "draft_title": title or "",
        "draft_subtitle": subtitle or "",
        "draft_body": _prosemirror_body(body),
        "audience": audience,
        "type": "newsletter",
    }
    if cover_image_url:
        # Substack stores the cover image on a separate field; the API accepts
        # either a hosted URL it ingests, or a pre-uploaded Substack media id.
        # The hosted-URL path is simpler and works for fal-hosted images that
        # are still live (we generally publish within seconds of generation).
        draft_payload["cover_image"] = cover_image_url

    draft = _request("POST", "/api/v1/drafts", json_body=draft_payload)
    draft_id = draft.get("id") or draft.get("draft_id")
    if not draft_id:
        raise SubstackError(f"Substack draft created but no id in response: {str(draft)[:200]}")

    published = _request("POST", f"/api/v1/drafts/{draft_id}/publish", json_body={
        "send": True,         # email subscribers
        "share_automatically": False,  # we do our own cross-posting via Publer
    })

    # Substack response shape varies — look for the post URL in a few places.
    for key in ("canonical_url", "url", "post_url"):
        v = published.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v

    # Fallback: build URL from slug + publication base.
    slug = published.get("slug")
    post_id = published.get("id") or draft_id
    if slug:
        return f"{_publication_url()}/p/{slug}"
    return f"{_publication_url()}/p/{post_id}"


def publish_note(body: str) -> str:
    """Publish a Substack Note (short-form cross-post). Returns the note URL.

    Notes are the Substack equivalent of a tweet — single-paragraph short
    text, no title, no subtitle. The pipeline routes ContentType.ARTICLE here
    because its body is already short-form (<480 chars).
    """
    text = (body or "").strip()
    if not text:
        raise SubstackError("publish_note called with empty body")

    payload = {
        "bodyJson": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        },
        "tabId": "for-you",
        "surface": "feed",
    }
    res = _request("POST", "/api/v1/comment/feed", json_body=payload)

    note_id = res.get("id") or (res.get("comment") or {}).get("id")
    for key in ("canonical_url", "url"):
        v = res.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    if note_id:
        return f"{_publication_url()}/notes/note/c-{note_id}"
    return _publication_url()
