# Headless Chromium fetcher for the EVENT_CLIP source layer.
# Sources that hit Cloudflare/Akamai/SPA-rendered listing pages from
# the Hetzner datacenter IP get 403'd by curl/requests; a real browser
# clears those checks. This sidecar exposes a tiny POST /fetch HTTP API
# that other zeus-net services call via http://browser-fetch:8000/fetch.
#
# Image: Microsoft's official Playwright Python base. Pre-installs
# Chromium, ffmpeg, fonts, and every shared lib Chromium needs on Linux.
# It's ~1.4 GB but a one-time pull on the prod box.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8000

# Single uvicorn worker is enough — the EVENT_CLIP cron makes a handful of
# fetches per hour. Browser launch + page lifecycle is sequenced inside the
# server so we don't open more than one Chromium context at a time.
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
