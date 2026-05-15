# ============================================================================
# Zeus Framework — Docker Image
# ============================================================================
# Builds the full Zeus stack on top of the Hermes Agent core:
#   - Hermes Agent (Python, uv, Node.js, Playwright, tools)
#   - Zeus skills (98+ procedural skill files)
#   - Mnemosyne L3 memory plugin (Redis + pgvector)
#   - Zeus soul persona
#
# Build context: repo root (one level above core/)
# ============================================================================

# ── Stage 1: Build Hermes core ───────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:0.11.6-python3.13-trixie@sha256:b3c543b6c4f23a5f2df22866bd7857e5d304b67a564f4feab6ac22044dde719b AS uv_source
FROM tianon/gosu:1.19-trixie@sha256:3b176695959c71e123eb390d427efc665eeb561b1540e82679c15e992006b8b9 AS gosu_source

FROM debian:13.4 AS hermes_base

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential nodejs npm python3 ripgrep ffmpeg gcc python3-dev \
        libffi-dev procps git curl && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -u 10000 -m -d /opt/data hermes

COPY --chmod=0755 --from=gosu_source /gosu /usr/local/bin/
COPY --chmod=0755 --from=uv_source /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

WORKDIR /opt/hermes

# Dependency layer (cached unless package files change)
COPY core/package.json core/package-lock.json ./
COPY core/web/package.json core/web/package-lock.json web/

RUN npm install --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    (cd web && npm install --prefer-offline --no-audit) && \
    npm cache clean --force

# Python deps layer — copy only the metadata files first so this layer is
# cached across pure source-code changes (models.py, skills, etc.)
COPY --chown=hermes:hermes core/pyproject.toml core/requirements.txt* ./
RUN chown hermes:hermes /opt/hermes
USER hermes
RUN uv venv /opt/hermes/.venv && \
    uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python ".[all]" 2>/dev/null || true
# EVENT_CLIP pipeline needs yt-dlp (apt's package is months out of date and
# breaks against YouTube's regular signature-cipher rotations). Pin from pip
# so the venv binary at /opt/hermes/.venv/bin/yt-dlp is current.
RUN uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python yt-dlp
USER root

# Hermes source (cache-bust only invalidates the install step below, not npm)
COPY --chown=hermes:hermes core/ .

# Build web dashboard
RUN cd web && npm run build

# Final editable install (fast — deps already in venv, just creates .pth link)
USER hermes
RUN uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python -e ".[all]"

# ── Stage 2: Zeus layer (adds skills, plugins, soul) ─────────────────────────
FROM hermes_base AS zeus

USER root

# Zeus skills → synced at runtime via skills_sync.py
COPY --chown=hermes:hermes skills/ /opt/zeus/skills/

# Mnemosyne plugin
COPY --chown=hermes:hermes plugins/mnemosyne/ /opt/zeus/plugins/mnemosyne/

# Zeus soul persona
COPY --chown=hermes:hermes soul/SOUL.md /opt/zeus/SOUL.md

# Memory templates
COPY --chown=hermes:hermes memory/ /opt/zeus/memory/

# Stack module (hermes_stack.py)
COPY --chown=hermes:hermes stack/ /opt/zeus/stack/

# Zeus entrypoint (overrides core entrypoint)
COPY --chown=root:root docker/zeus-entrypoint.sh /usr/local/bin/zeus-entrypoint.sh
RUN chmod +x /usr/local/bin/zeus-entrypoint.sh

ENV HERMES_HOME=/opt/data
ENV ZEUS_DIR=/opt/zeus
VOLUME ["/opt/data"]

ENTRYPOINT ["/usr/local/bin/zeus-entrypoint.sh"]
