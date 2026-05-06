FROM python:3.13-slim

# Install docker CLI from the upstream static release. The Debian `docker.io`
# package on bookworm-slim ships only docker-init in some build configs (the
# real CLI binary is missing), so we pin the static binary directly. ARG so
# rebuilds can bump the version without touching the rest of the file.
ARG DOCKER_CLI_VERSION=27.5.1
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates curl; \
    arch="$(uname -m)"; \
    case "$arch" in \
        x86_64)  dlarch=x86_64 ;; \
        aarch64) dlarch=aarch64 ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${dlarch}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
    tar -xzf /tmp/docker.tgz -C /tmp; \
    mv /tmp/docker/docker /usr/local/bin/docker; \
    chmod +x /usr/local/bin/docker; \
    rm -rf /tmp/docker /tmp/docker.tgz; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*; \
    docker --version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
