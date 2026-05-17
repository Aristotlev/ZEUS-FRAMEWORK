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
import re
import subprocess
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
    # even with a valid session cookie. /api/v1/comment/feed (Notes) additionally
    # requires X-Requested-With — without it the WAF 403s even with a valid SID.
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
        "X-Requested-With": "XMLHttpRequest",
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


_ADMIN_USER_ID: Optional[int] = None
_NOTE_AUTHOR_HANDLE: Optional[str] = None


def _admin_user_id() -> int:
    """Return the publication's admin user id, used as the draft byline.

    Substack's /api/v1/drafts rejects with 400 `draft_bylines: Invalid value`
    when the field is missing — the web app always sends at least one byline.
    We pick the `role: admin` user from /api/v1/publication/users (cached for
    the process lifetime; the admin doesn't change between runs).
    """
    global _ADMIN_USER_ID
    if _ADMIN_USER_ID is not None:
        return _ADMIN_USER_ID
    users = _request_list("GET", "/api/v1/publication/users")
    admin = next((u for u in users if u.get("role") == "admin"), None)
    if not admin or not isinstance(admin.get("id"), int):
        raise SubstackError(f"No admin user found in /publication/users: {str(users)[:200]}")
    _ADMIN_USER_ID = admin["id"]
    return _ADMIN_USER_ID


def _note_author_handle() -> Optional[str]:
    """Return the @handle used for the public Note URL.

    Notes live at `https://substack.com/@<handle>/note/c-<id>` — the
    publication-scoped `/notes/note/c-<id>` URL we used to construct
    returns 404, so the user clicks through and sees an empty publication
    shell instead of the note body (every Note URL the pipeline emitted
    before 2026-05-13 was broken this way).

    /api/v1/publication/users does NOT carry the handle. The reliable
    source is the comment.handle field on the user's own Notes feed —
    cached per-process the first time we publish or look one up.
    """
    global _NOTE_AUTHOR_HANDLE
    if _NOTE_AUTHOR_HANDLE:
        return _NOTE_AUTHOR_HANDLE
    try:
        feed = _request("GET", "/api/v1/notes")
    except Exception as exc:
        log.warning(f"could not resolve note handle: {exc}")
        return None
    items = feed.get("items") if isinstance(feed, dict) else None
    if not isinstance(items, list):
        return None
    for it in items:
        comment = (it or {}).get("comment") or {}
        handle = comment.get("handle")
        if isinstance(handle, str) and handle.strip():
            _NOTE_AUTHOR_HANDLE = handle.strip()
            return _NOTE_AUTHOR_HANDLE
    return None


def _request_list(method: str, path: str, *, timeout: int = 30) -> list:
    """Variant of _request for endpoints that return a JSON list at the top
    level — _request() assumes a dict and would coerce the response away.
    """
    url = f"{_publication_url()}{path}"
    r = requests.request(method, url, headers=_headers(), cookies=_cookies(), timeout=timeout)
    if r.status_code in (401, 403):
        raise SubstackAuthError(
            f"Substack returned {r.status_code} on {path} — substack.sid likely expired."
        )
    if r.status_code >= 400:
        raise SubstackError(f"Substack {method} {path} failed {r.status_code}: {r.text[:300]}")
    return r.json()


_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def youtube_video_id(url: str) -> Optional[str]:
    """Extract the 11-char YouTube video id from a watch / shorts / youtu.be
    URL. Returns None if the URL isn't a recognised YouTube link.
    """
    if not url:
        return None
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else None


def youtube_thumbnail_url(video_id: str) -> str:
    """YouTube serves a max-res thumbnail at a predictable URL for every public
    video. Used as the Substack Post cover for EVENT_CLIP runs so the card
    preview isn't a generic placeholder."""
    return f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"


def _prosemirror_body(text: str, *, youtube_video_id: Optional[str] = None) -> str:
    """Convert plain prose with \\n\\n paragraph breaks to a stringified
    ProseMirror doc. Empty paragraphs are dropped so a trailing newline
    doesn't ship as an empty block.

    When `youtube_video_id` is supplied, a youtube2 embed node is prepended
    so EVENT_CLIP posts open with the player and the prose flows below. The
    URL is ALSO appended as a paragraph fallback — if Substack's renderer
    rejects the embed node shape (their schema versions silently), the reader
    still sees a clickable link instead of a blank space.
    """
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [""]
    body_nodes: list[dict] = []
    if youtube_video_id:
        body_nodes.append({
            "type": "youtube2",
            "attrs": {
                "videoId": youtube_video_id,
                "src": f"https://www.youtube.com/watch?v={youtube_video_id}",
            },
        })
    for p in paragraphs:
        body_nodes.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": p}] if p else [],
        })
    if youtube_video_id:
        fallback_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
        body_nodes.append({
            "type": "paragraph",
            "content": [{
                "type": "text",
                "text": fallback_url,
                "marks": [{"type": "link", "attrs": {"href": fallback_url}}],
            }],
        })
    doc = {"type": "doc", "content": body_nodes}
    return json.dumps(doc, ensure_ascii=False)


def publish_post(
    *,
    title: str,
    subtitle: str = "",
    body: str,
    cover_image_url: Optional[str] = None,
    audience: str = "everyone",
    youtube_embed_url: Optional[str] = None,
) -> str:
    """Create a draft and publish it. Returns the public post URL.

    `audience` accepts "everyone" (free + paid) or "only_paid". The pipeline's
    default is "everyone" — these are SEO/news pieces, not subscriber-only.

    `youtube_embed_url`: when set (EVENT_CLIP pipeline), the post opens with a
    YouTube player. If `cover_image_url` is unset, the YouTube maxres
    thumbnail is used as the card preview.
    """
    yt_id = youtube_video_id(youtube_embed_url) if youtube_embed_url else None
    if yt_id and not cover_image_url:
        cover_image_url = youtube_thumbnail_url(yt_id)
    draft_payload: dict = {
        "draft_title": title or "",
        "draft_subtitle": subtitle or "",
        "draft_body": _prosemirror_body(body, youtube_video_id=yt_id),
        "audience": audience,
        "type": "newsletter",
        "draft_bylines": [{"id": _admin_user_id()}],
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


_NODE_MODULES_PATH = "/opt/hermes/node_modules"
_NOTE_BROWSER_SCRIPT = os.path.join(os.path.dirname(__file__), "substack_note_browser.js")


def publish_note(body: str) -> str:
    """Publish a Substack Note via headless Chromium. Returns the note URL.

    Notes are the Substack equivalent of a tweet — single-paragraph short
    text, no title, no subtitle. The pipeline routes ContentType.ARTICLE here
    because its body is already short-form (<480 chars).

    Why a browser and not `requests`: as of 2026-05-14 the
    /api/v1/comment/feed POST is gated by Cloudflare bot-fight when called
    from datacenter IPs — we'd get a generic CF 403 HTML page back with a
    valid substack.sid. Routing through Playwright (Node side, already in the
    image) lets the request ride a real Chromium TLS fingerprint and inherit
    cf_clearance/__cf_bm from a warm-up navigation, which CF allows. Other
    Substack endpoints (drafts, publication users, /api/v1/notes GET) still
    work over plain requests and are left on the requests path.
    """
    text = (body or "").strip()
    if not text:
        raise SubstackError("publish_note called with empty body")

    # Split on \n\n OR \n so 3-4 line ARTICLE bodies render as separate
    # paragraphs in the Substack feed — a single text node would collapse the
    # line breaks and the note would read as one dense run-on.
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    stdin_payload = json.dumps({
        "publicationUrl": _publication_url(),
        "sid": _cookie(),
        "paragraphs": paragraphs,
    })

    # PLAYWRIGHT_BROWSERS_PATH: hardcoded fallback because Hermes execute_code
    # strips category=tool env keys from cron-fired subprocesses (same blocklist
    # that bit TAVILY). Without this, the node child falls back to
    # $HOME/.cache/ms-playwright/... where nothing is installed, and every
    # ARTICLE → Substack Note silently fails with "Executable doesn't exist".
    env = {
        **os.environ,
        "NODE_PATH": _NODE_MODULES_PATH,
        "PLAYWRIGHT_BROWSERS_PATH": os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH", "/opt/hermes/.playwright"
        ),
    }
    try:
        proc = subprocess.run(
            ["node", _NOTE_BROWSER_SCRIPT],
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise SubstackError("substack note publish timed out after 90s in headless browser")
    except FileNotFoundError:
        raise SubstackError("node binary not found — required for Substack notes browser path")

    if proc.returncode != 0:
        raise SubstackError(
            f"substack_note_browser.js exited rc={proc.returncode}: {proc.stderr[:500]}"
        )
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        raise SubstackError(f"could not parse browser output: {proc.stdout[:300]}")

    status = result.get("status", 0)
    body_data = result.get("body")

    if status in (401, 403):
        raise SubstackAuthError(
            f"Substack {status} via headless browser — likely substack.sid "
            f"expired or CF re-tightened: {str(body_data)[:200]}"
        )
    if status >= 400:
        raise SubstackError(
            f"Substack note POST failed {status} via headless browser: "
            f"{str(body_data)[:300]}"
        )

    res = body_data if isinstance(body_data, dict) else {}
    comment = res.get("comment") if isinstance(res, dict) else None
    note_id = res.get("id") or ((comment or {}).get("id"))
    # Prefer any canonical URL the API hands back.
    for key in ("canonical_url", "url"):
        v = res.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    # The POST response sometimes includes the author handle inline — use it
    # before falling back to a lookup.
    handle: Optional[str] = None
    if isinstance(comment, dict):
        h = comment.get("handle")
        if isinstance(h, str) and h.strip():
            handle = h.strip()
    if not handle:
        handle = _note_author_handle()
    if note_id and handle:
        # Public Notes URL — works without a session, no 404.
        return f"https://substack.com/@{handle}/note/c-{note_id}"
    if note_id:
        # Last-resort fallback (still 404 in the browser, but at least the id
        # lands in the email/ledger so the run is debuggable).
        log.warning(
            "substack note published (id=%s) but no @handle resolved — "
            "URL will 404; check /api/v1/notes auth", note_id,
        )
        return f"{_publication_url()}/notes/note/c-{note_id}"
    return _publication_url()
