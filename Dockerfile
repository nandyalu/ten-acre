FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY TradingAgents/ ./TradingAgents/
# --locked fails the build if uv.lock is out of sync with pyproject.toml,
# instead of silently installing a stale resolution. Non-editable path
# dependency (see pyproject.toml's [tool.uv.sources]): this is a production
# image, and an editable install would leave tradingagents pointing back at
# this build-stage path, which doesn't exist in the final stage below.
# --no-install-project keeps the app itself out of the venv for the same
# reason: the image runs it from /app/backend, copied below, and an editable
# install of it would point at /build.
RUN uv sync --locked --no-dev --no-install-project

# Docs are built in their own stage so zensical and its dependencies never
# reach the runtime image — only the static site/ output does.
FROM python:3.14-slim AS docsbuilder

RUN pip install --no-cache-dir zensical

WORKDIR /docs-build
COPY zensical.toml ./
# Three pages under docs/ are symlinks to files at the repo root. Without
# their targets the build skips them, and /docs has no journey, contributing
# or AI policy page.
COPY JOURNEY.md CONTRIBUTING.md AI_POLICY.md ./
COPY docs/ ./docs/
RUN python -m zensical build

# Same idea as docsbuilder: Node/npm and the whole Angular toolchain never
# reach the runtime image, only the static production build output does.
FROM node:22-slim AS frontendbuilder

WORKDIR /frontend-build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents
WORKDIR /app

COPY --chown=appuser:appuser backend/ ./backend/
# Static docs site, served at /docs by backend/app.py. zensical.toml's site_dir
# puts the build at backend/site, inside the package, where an installed copy
# has it too.
COPY --from=docsbuilder --chown=appuser:appuser /docs-build/backend/site ./backend/site/
# Angular production build, served at the container's web root by
# backend/app.py (with an index.html fallback for client-side routes).
# angular.json's outputPath is ../backend/web, so from that stage's
# /frontend-build it lands at /backend/web.
COPY --from=frontendbuilder --chown=appuser:appuser /backend/web ./backend/web/
# Owned by appuser *before* the VOLUME instruction so a freshly created named
# volume inherits appuser ownership instead of defaulting to root.
# TEN_ACRE_DATA_DIR names it for the app; without it backend/paths.py would
# fall back to the per-user data home, because /app holds no pyproject.toml.
RUN mkdir -p /app/data && chown appuser:appuser /app/data
ENV TEN_ACRE_DATA_DIR=/app/data
VOLUME ["/app/data"]

USER appuser

# main() runs the alembic upgrade before uvicorn. Idempotent: every start or
# redeploy only confirms the DB is already at head.
ENTRYPOINT ["python", "-m", "backend.main"]
