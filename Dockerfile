# syntax=docker/dockerfile:1
# One image: the FastAPI app serving the built Next.js web app. Base images are pinned by digest (Dependabot
# keeps them current) and the app runs as a non-root user on a read-only filesystem (see docker-compose.yml).

# ---- web: the Next.js static export (built once on the build machine's own platform; the output is plain files)
FROM --platform=$BUILDPLATFORM node:26-trixie-slim@sha256:0d1e33586362bb9e608c4e751e7a9e7edb55c9ac11461ed5666af696cc461dd4 AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- python: the app and its locked dependencies in a virtualenv
FROM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS python
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/financeapp ./financeapp
RUN uv sync --frozen --no-dev --no-editable

# ---- runtime
FROM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ARG APP_VERSION=dev
LABEL org.opencontainers.image.title="Finance App" \
      org.opencontainers.image.source="https://github.com/rastogi-s/FinanceApp" \
      org.opencontainers.image.version="${APP_VERSION}"
# Security fixes released since the base image was built; pip is not needed at runtime.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y pip \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin finance
COPY --from=python /app/.venv /app/.venv
COPY --from=web /web/out /app/web
# The server files, so update.sh can refresh itself, docker-compose.yml and the systemd units from the image it just
# checked the signature of (nothing here runs inside the container).
COPY docker-compose.yml update.sh /app/server/
COPY .env.example /app/server/env.example
COPY deploy/systemd/ /app/server/
ENV PATH=/app/.venv/bin:$PATH \
    APP_VERSION=${APP_VERSION} \
    STATIC_DIR=/app/web \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"]
# Behind tailscale serve: trust X-Forwarded-Proto/For. Safe because the port is only published on 127.0.0.1.
CMD ["uvicorn", "financeapp.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header", "--no-access-log", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
