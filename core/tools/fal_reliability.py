"""Shared fal.ai reliability layer — queue submission, retry, timeout, download.

Used by video_generation_tool, music_generation_tool, and any future fal-based
tools. The image_generation_tool predates this module and has its own submit
path (including managed-gateway support); it can migrate here later.

Reliability features:
- Queue API submission (async-safe, persistent)
- Idempotency keys per request
- X-Fal-Request-Timeout header (fail-fast if no runner picks up)
- Exponential backoff retry (skips safety rejections)
- Immediate result download (fal URLs are temporary)
"""

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import fal_client
import httpx

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 2.0
BACKOFF_MAX = 30.0
NON_RETRYABLE_STATUS = {400, 401, 403, 422}


def submit_fal_job(
    model: str,
    arguments: Dict[str, Any],
    *,
    timeout_seconds: Optional[int] = None,
    webhook_url: Optional[str] = None,
) -> Any:
    """Submit a job to the fal queue with idempotency and optional timeout.

    Returns a request handle whose ``.get()`` blocks until completion.
    """
    if not os.getenv("FAL_KEY"):
        raise ValueError(
            "FAL_KEY environment variable not set. "
            "Get a key at https://fal.ai/ and export FAL_KEY=your-key"
        )

    headers: Dict[str, str] = {"x-idempotency-key": str(uuid.uuid4())}
    if timeout_seconds is not None:
        headers["X-Fal-Request-Timeout"] = str(timeout_seconds)

    kwargs: Dict[str, Any] = {"arguments": arguments, "headers": headers}
    if webhook_url is not None:
        kwargs["webhook_url"] = webhook_url

    return fal_client.submit(model, **kwargs)


def wait_for_result(
    handle: Any,
    *,
    max_retries: int = MAX_RETRIES,
    backoff_base: float = BACKOFF_BASE,
) -> Dict[str, Any]:
    """Poll a fal queue handle with exponential-backoff retry on transient errors.

    Safety rejections (400/401/403/422) are raised immediately — they won't
    resolve on retry.
    """
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return handle.get()
        except Exception as exc:
            last_error = exc
            status = _extract_http_status(exc)
            if status is not None and status in NON_RETRYABLE_STATUS:
                raise
            if attempt < max_retries:
                delay = min(backoff_base * (2 ** attempt), BACKOFF_MAX)
                logger.warning(
                    "fal job attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt + 1, max_retries + 1, exc, delay,
                )
                time.sleep(delay)
    raise last_error  # type: ignore[misc]


def download_result(
    url: str,
    dest_dir: Path,
    *,
    filename: Optional[str] = None,
) -> Path:
    """Download a fal result file immediately — URLs are temporary."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        ext = _guess_extension(url)
        filename = f"{uuid.uuid4().hex[:12]}{ext}"
    dest = dest_dir / filename
    with httpx.stream("GET", url, follow_redirects=True, timeout=180) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)
    logger.info("Downloaded fal result → %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
    return dest


def check_fal_available() -> bool:
    """True when FAL_KEY is set and fal_client is importable."""
    if not os.getenv("FAL_KEY"):
        return False
    try:
        import fal_client as _fc  # noqa: F401
        return True
    except ImportError:
        return False


def _extract_http_status(exc: BaseException) -> Optional[int]:
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _guess_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".mp4", ".webm", ".mp3", ".wav", ".ogg", ".png", ".jpg", ".jpeg", ".webp"):
        if path.endswith(ext):
            return ext
    return ""
