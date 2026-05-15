"""Headless Chromium fetch API for the EVENT_CLIP source layer.

Two endpoints:

  POST /fetch
      Render `url` in a real Chromium and return HTML + any network response
      URLs matching `capture_responses_regex`. For sources that need to
      capture signed CDN URLs the JS player constructs at runtime (cspan
      m3u8, Akamai HLS), set capture_responses_regex to a pattern that
      matches the manifest URL.

  POST /fetch-binary
      Download `url` through a real Chromium browser context. Returns the
      response body as bytes. Use this when the media URL itself needs the
      cookies/session/IP fingerprint of the browser session (cspan signed
      CloudFront, Akamai HLS segments that key on Referer/Origin).

The browser is launched once and reused. A single Chromium context is
serialized via an asyncio lock — at the EVENT_CLIP cron's ~1 fetch/hour
volume, concurrency isn't needed and one context is cheap to keep warm.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    Response as PlaywrightResponse,
    async_playwright,
)
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("browser-fetch")

_state: dict = {}
_lock = asyncio.Lock()


# Realistic Chrome desktop UA. Matches what Playwright's Chromium would
# announce; explicit so callers can see what the upstream CDN sees.
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Hard wall-clock budget per fetch — sidecar callers retry on timeout.
DEFAULT_NAV_TIMEOUT_MS = 25_000
DEFAULT_BINARY_TIMEOUT_MS = 60_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Launch Chromium once at startup, tear down at shutdown."""
    pw: Playwright = await async_playwright().start()
    browser: Browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    _state["pw"] = pw
    _state["browser"] = browser
    log.info("playwright launched, chromium=%s", browser.version)
    try:
        yield
    finally:
        try:
            await browser.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass


app = FastAPI(lifespan=lifespan, title="zeus browser-fetch")


class FetchRequest(BaseModel):
    url: str
    wait_for_selector: Optional[str] = None
    wait_seconds: float = Field(default=2.0, ge=0.0, le=15.0)
    capture_responses_regex: Optional[str] = None
    wait_until: str = Field(default="networkidle")  # 'load' | 'domcontentloaded' | 'networkidle'
    referer: Optional[str] = None


class FetchResponse(BaseModel):
    status: int
    final_url: str
    html: str
    captured_urls: list[str]


class BinaryRequest(BaseModel):
    url: str
    referer: Optional[str] = None
    # If set, navigate this page first so the binary GET inherits the same
    # site cookies/session. Used for signed CDN URLs whose keys are bound
    # to the original page visit.
    prime_with_page: Optional[str] = None


async def _new_context() -> BrowserContext:
    browser: Browser = _state["browser"]
    return await browser.new_context(
        user_agent=DEFAULT_UA,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
    )


@app.get("/healthz")
async def healthz():
    browser = _state.get("browser")
    if browser is None:
        return JSONResponse({"ok": False, "reason": "browser not initialized"}, status_code=503)
    return {"ok": True, "chromium": browser.version}


@app.post("/fetch", response_model=FetchResponse)
async def fetch(req: FetchRequest) -> FetchResponse:
    async with _lock:
        ctx = await _new_context()
        try:
            page = await ctx.new_page()
            page.set_default_navigation_timeout(DEFAULT_NAV_TIMEOUT_MS)

            captured: list[str] = []
            pattern: Optional[re.Pattern] = None
            if req.capture_responses_regex:
                try:
                    pattern = re.compile(req.capture_responses_regex, re.I)
                except re.error as exc:
                    raise HTTPException(400, f"bad regex: {exc}")

                def _on_response(r: PlaywrightResponse):
                    try:
                        if pattern and pattern.search(r.url):
                            captured.append(r.url)
                    except Exception:
                        pass

                page.on("response", _on_response)

            headers = {}
            if req.referer:
                headers["Referer"] = req.referer
            if headers:
                await page.set_extra_http_headers(headers)

            try:
                resp = await page.goto(req.url, wait_until=req.wait_until)
            except Exception as exc:
                # Some pages stall on networkidle (long-poll WebSocket). Try
                # a softer condition before giving up.
                log.warning("goto(%s) failed on %s: %s — retry with domcontentloaded",
                            req.wait_until, req.url, exc)
                resp = await page.goto(req.url, wait_until="domcontentloaded")

            if req.wait_for_selector:
                try:
                    await page.wait_for_selector(req.wait_for_selector, timeout=10_000)
                except Exception as exc:
                    log.info("wait_for_selector(%s) on %s: %s",
                             req.wait_for_selector, req.url, exc)

            if req.wait_seconds > 0:
                await asyncio.sleep(req.wait_seconds)

            html = await page.content()
            status = resp.status if resp else 0
            final_url = page.url

            return FetchResponse(
                status=status,
                final_url=final_url,
                html=html,
                captured_urls=captured,
            )
        finally:
            try:
                await ctx.close()
            except Exception:
                pass


@app.post("/fetch-binary")
async def fetch_binary(req: BinaryRequest) -> Response:
    """Download `url` through a real browser context.

    The bytes come back in the response body with Content-Type set from
    the upstream response. Status code is propagated. Use this for signed
    CDN media URLs that 403 from any non-browser client.
    """
    async with _lock:
        ctx = await _new_context()
        try:
            if req.prime_with_page:
                # Visit the prime URL first so the context picks up cookies
                # and the upstream sees a referer chain.
                page = await ctx.new_page()
                try:
                    await page.goto(
                        req.prime_with_page, wait_until="domcontentloaded",
                        timeout=DEFAULT_NAV_TIMEOUT_MS,
                    )
                except Exception as exc:
                    log.info("prime_with_page failed: %s — continuing anyway", exc)
                finally:
                    await page.close()

            headers = {}
            if req.referer:
                headers["Referer"] = req.referer

            api_resp = await ctx.request.get(
                req.url, headers=headers, timeout=DEFAULT_BINARY_TIMEOUT_MS,
            )
            body = await api_resp.body()
            ct = api_resp.headers.get("content-type", "application/octet-stream")
            return Response(
                content=body,
                status_code=api_resp.status,
                media_type=ct,
            )
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
