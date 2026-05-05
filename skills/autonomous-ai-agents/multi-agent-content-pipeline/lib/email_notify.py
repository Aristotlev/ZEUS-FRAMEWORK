"""
Post-run email notifications for Zeus content pipeline.

User mandate (2026-05-04): every run sends an email to the agent's master with
all social-media post links AND a comprehensive, always-on cost analysis (this
run, today, last 7 days, last 30 days, all time).

Backend selection (first one configured wins):
  1. Resend       (RESEND_API_KEY)         — preferred for delivery reliability
  2. AgentMail    (AGENTMAIL_API_KEY)      — for @agentmail.to identity
  3. Gmail SMTP   (HERMES_GMAIL_APP_PASSWORD + HERMES_GMAIL_USER) — fallback
  4. File         (always)                 — last resort: writes the email body to a local file so nothing is lost

Recipient defaults to ZEUS_NOTIFY_EMAIL or `ariscsc@gmail.com`.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import requests

from .content_types import ContentPiece, ContentType
from .ledger import summary as ledger_summary

log = logging.getLogger("zeus.email")

DEFAULT_RECIPIENT = os.getenv("ZEUS_NOTIFY_EMAIL", "ariscsc@gmail.com")
FROM_NAME = os.getenv("ZEUS_NOTIFY_FROM_NAME", "Zeus Pipeline")
FROM_EMAIL_FALLBACK = os.getenv("ZEUS_NOTIFY_FROM_EMAIL", "hermesomni@agentmail.to")
LOCAL_INBOX = Path(os.path.expanduser("~/.hermes/zeus_email_outbox"))


def send_pipeline_summary(piece: ContentPiece, recipient: Optional[str] = None) -> str:
    """Send the post-run summary. Returns the backend that handled it ('resend'|'agentmail'|'smtp'|'file')."""
    to_addr = recipient or DEFAULT_RECIPIENT
    subject = _subject(piece)
    html = _html_body(piece)
    text = _text_body(piece)

    backend = _pick_backend()
    log.info(f"email backend: {backend} -> {to_addr}")
    try:
        if backend == "resend":
            _send_resend(to_addr, subject, html, text)
        elif backend == "agentmail":
            _send_agentmail(to_addr, subject, html, text)
        elif backend == "smtp":
            _send_gmail_smtp(to_addr, subject, html, text)
        else:
            _save_file(to_addr, subject, html, text)
            backend = "file"
    except Exception as e:
        log.error(f"email backend {backend} failed: {e}; falling back to file")
        _save_file(to_addr, subject, html, text)
        backend = "file"
    return backend


def _pick_backend() -> str:
    if _real(os.getenv("RESEND_API_KEY")):
        return "resend"
    if _real(os.getenv("AGENTMAIL_API_KEY")):
        return "agentmail"
    if _real(os.getenv("HERMES_GMAIL_APP_PASSWORD")) and _real(os.getenv("HERMES_GMAIL_USER")):
        return "smtp"
    return "file"


def _real(v: Optional[str]) -> bool:
    return bool(v) and not v.startswith("REPLACE_WITH") and v.strip() != ""


# ---- formatting ----------------------------------------------------------

_TYPE_ICON = {
    ContentType.ARTICLE: "📄",
    ContentType.CAROUSEL: "🎠",
    ContentType.SHORT_VIDEO: "🎬",
    ContentType.LONG_VIDEO: "🎥",
}


def _subject(p: ContentPiece) -> str:
    icon = _TYPE_ICON.get(p.content_type, "•")
    return f"{icon} {p.content_type.value}: {p.title or p.topic}".strip()[:120]


def _post_links_table(p: ContentPiece) -> list[tuple[str, str]]:
    """Return [(platform, link_or_status), ...] for the email."""
    rows: list[tuple[str, str]] = []
    for platform in p.target_platforms:
        job_id = p.publer_job_ids.get(platform, "")
        if not job_id:
            rows.append((platform, "not posted"))
        elif job_id.startswith("FAILED"):
            rows.append((platform, job_id))
        else:
            link = p.publer_job_ids.get(f"{platform}_url") or f"job={job_id}"
            rows.append((platform, link))
    return rows


def _ledger_block_text() -> str:
    today = ledger_summary(window_days=1)
    week = ledger_summary(window_days=7)
    month = ledger_summary(window_days=30)
    all_time = ledger_summary(window_days=None)
    lines = [
        "Cost Ledger:",
        f"  Last 24h:  ${today['total_cost_usd']:.4f}  ({today['runs']} runs)",
        f"  Last 7d:   ${week['total_cost_usd']:.4f}  ({week['runs']} runs)",
        f"  Last 30d:  ${month['total_cost_usd']:.4f}  ({month['runs']} runs)",
        f"  All time:  ${all_time['total_cost_usd']:.4f}  ({all_time['runs']} runs)",
    ]
    by_model = month.get("by_model") or {}
    if by_model:
        lines.append("  Last-30d top models:")
        for m, v in list(by_model.items())[:5]:
            lines.append(f"    {m}: ${v:.4f}")
    return "\n".join(lines)


def _ledger_block_html() -> str:
    today = ledger_summary(window_days=1)
    week = ledger_summary(window_days=7)
    month = ledger_summary(window_days=30)
    all_time = ledger_summary(window_days=None)
    rows = [
        ("Last 24h", today),
        ("Last 7d", week),
        ("Last 30d", month),
        ("All time", all_time),
    ]
    body = "".join(
        f"<tr><td>{label}</td><td style='text-align:right'>${s['total_cost_usd']:.4f}</td><td style='text-align:right'>{s['runs']}</td></tr>"
        for label, s in rows
    )
    by_model = month.get("by_model") or {}
    model_rows = "".join(f"<tr><td>{m}</td><td style='text-align:right'>${v:.4f}</td></tr>" for m, v in list(by_model.items())[:5])
    return (
        "<h3>Cost Ledger (always-on)</h3>"
        "<table style='border-collapse:collapse' cellpadding='4'>"
        "<tr><th align='left'>Window</th><th align='right'>Cost</th><th align='right'>Runs</th></tr>"
        f"{body}"
        "</table>"
        + (
            "<h4>Last-30d top models</h4>"
            "<table style='border-collapse:collapse' cellpadding='4'>"
            "<tr><th align='left'>Model</th><th align='right'>Spend</th></tr>"
            f"{model_rows}"
            "</table>"
            if model_rows
            else ""
        )
    )


def _text_body(p: ContentPiece) -> str:
    parts = [
        f"Title: {p.title}",
        f"Type:  {p.content_type.value}",
        f"Topic: {p.topic}",
        f"Status: {p.status}",
        "",
        "Posts:",
    ]
    for platform, link in _post_links_table(p):
        parts.append(f"  - {platform:10s} {link}")
    parts.append("")
    parts.append("This run cost breakdown:")
    for k, v in p.cost_breakdown.items():
        parts.append(f"  {k}: ${v:.4f}")
    parts.append(f"This run total: ${p.total_cost:.4f}")
    parts.append("")
    parts.append(_ledger_block_text())
    if p.notion_page_id:
        parts.append("")
        parts.append(f"Notion archive page: {p.notion_page_id}")
    return "\n".join(parts)


def _html_body(p: ContentPiece) -> str:
    posts_html = "".join(
        f"<tr><td><b>{platform}</b></td><td>{link}</td></tr>"
        for platform, link in _post_links_table(p)
    )
    cost_html = "".join(
        f"<tr><td>{k}</td><td style='text-align:right'>${v:.4f}</td></tr>"
        for k, v in p.cost_breakdown.items()
    )
    notion_html = (
        f"<p>Notion archive: <code>{p.notion_page_id}</code></p>" if p.notion_page_id else ""
    )
    return f"""<html><body style="font-family: -apple-system, sans-serif; color:#111">
<h2>{_TYPE_ICON.get(p.content_type, '')} {p.title}</h2>
<p><b>Type:</b> {p.content_type.value} &middot; <b>Topic:</b> {p.topic} &middot; <b>Status:</b> {p.status}</p>

<h3>Posts</h3>
<table style="border-collapse:collapse" cellpadding="4">{posts_html}</table>

<h3>This run</h3>
<table style="border-collapse:collapse" cellpadding="4">{cost_html}
<tr><td><b>Total</b></td><td style='text-align:right'><b>${p.total_cost:.4f}</b></td></tr>
</table>

{_ledger_block_html()}

{notion_html}
</body></html>"""


# ---- backends ------------------------------------------------------------

def _send_resend(to: str, subject: str, html: str, text: str) -> None:
    key = os.environ["RESEND_API_KEY"]
    sender = os.getenv("RESEND_FROM", FROM_EMAIL_FALLBACK)
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"from": f"{FROM_NAME} <{sender}>", "to": [to], "subject": subject, "html": html, "text": text},
        timeout=15,
    )
    r.raise_for_status()


def _send_agentmail(to: str, subject: str, html: str, text: str) -> None:
    key = os.environ["AGENTMAIL_API_KEY"]
    inbox = os.getenv("AGENTMAIL_INBOX", "hermesomni@agentmail.to")
    r = requests.post(
        f"https://api.agentmail.to/v0/inboxes/{inbox}/messages/send",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"to": [to], "subject": subject, "html": html, "text": text},
        timeout=15,
    )
    r.raise_for_status()


def _send_gmail_smtp(to: str, subject: str, html: str, text: str) -> None:
    user = os.environ["HERMES_GMAIL_USER"]
    password = os.environ["HERMES_GMAIL_APP_PASSWORD"]
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{user}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)


def _save_file(to: str, subject: str, html: str, text: str) -> Path:
    LOCAL_INBOX.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    name = datetime.utcnow().strftime("%Y%m%dT%H%M%S") + ".txt"
    path = LOCAL_INBOX / name
    payload = {"to": to, "subject": subject, "text": text, "html_path": str(path.with_suffix(".html"))}
    path.write_text(text)
    path.with_suffix(".html").write_text(html)
    path.with_suffix(".meta.json").write_text(json.dumps(payload, indent=2))
    log.warning(f"no email backend configured; wrote message to {path}")
    return path
