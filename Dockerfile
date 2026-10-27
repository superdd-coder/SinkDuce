# syntax=docker/dockerfile:1.7
# Build args for base images.
# Defaults = Docker Hub / ghcr (required for GitHub Actions; CN mirrors often 403 there).
# Local CN mirror example:
#   docker build \
#     --build-arg NODE_IMAGE=docker.1ms.run/library/node:20-slim \
#     --build-arg PYTHON_IMAGE=docker.1ms.run/library/python:3.11-slim \
#     .
# Or set NODE_IMAGE / PYTHON_IMAGE in a local .env for docker compose.
#
# ARG before any FROM is global default for FROM lines only.

ARG NODE_IMAGE=node:20-slim
ARG PYTHON_IMAGE=python:3.11-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.6.14

# ── uv binary (COPY --from=var is unsupported; use a named stage) ──
FROM ${UV_IMAGE} AS uv

# Stage 1: dependencies shared by the two separate frontend builds.
FROM ${NODE_IMAGE} AS frontend_deps
# Prefer Corepack (ships with Node 20) over `npm install -g pnpm`.
# Fallback: npmmirror if npmjs is unreachable.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable \
    && (corepack prepare pnpm@10.15.1 --activate \
        || npm install -g pnpm@10.15.1 --registry=https://registry.npmmirror.com)
WORKDIR /app/frontend
# Deps layer keyed on the lockfile ONLY — `pnpm fetch` fills the store without
# package.json, so release version bumps (package.json) never bust this cache.
COPY frontend/pnpm-lock.yaml ./
RUN pnpm fetch
COPY frontend/ .
RUN pnpm install --frozen-lockfile --offline

FROM frontend_deps AS frontend_community
COPY ee/frontend/ /app/ee/frontend/
RUN pnpm run build

# Keep a separate enterprise assembly for future distribution splitting.
FROM frontend_deps AS frontend_enterprise
COPY ee/ /app/ee/
ENV SINKDUCE_EDITION=enterprise
RUN pnpm exec tsc -p tsconfig.enterprise.json --noEmit && pnpm run build

# Stage 2: Python app
FROM ${PYTHON_IMAGE} AS backend_base

# Use uv (fast Rust-based pip replacement) for Python deps
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libreoffice-impress \
    libreoffice-writer \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Deps layer keyed on docker/requirements-deps.txt (exported from uv.lock).
# Release version bumps touch pyproject.toml only, so this layer — and its
# ~2GB of downloads — stays cached across releases. Regenerate the file after
# dependency changes:
#   uv export --format requirements-txt --no-hashes --no-emit-project \
#     --no-dev --extra asr-onnx -o docker/requirements-deps.txt
COPY docker/requirements-deps.txt ./requirements-deps.txt
# Local ASR: ONNX runtime only (funasr-onnx + onnxruntime). No torch/funasr.
# Place pre-exported models under data/models/onnx/ (export once on a host with
# `pip install -e ".[asr-export]"` if you need to re-export).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
    --index-strategy unsafe-best-match \
    -r requirements-deps.txt

COPY . .
# Project itself — deps came from requirements-deps.txt, so this stays cheap.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps .
# Strip CRLF line endings (Windows git clone) then make executable
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

FROM backend_base AS enterprise
COPY --from=frontend_enterprise /app/frontend/dist /app/frontend/dist
RUN printf '{"edition":"enterprise","features":["permission_management"]}\n' > /app/edition.json
ENV SINKDUCE_EDITION=enterprise
ARG API_PORT=18900
EXPOSE ${API_PORT}
ENTRYPOINT ["./entrypoint.sh"]

# The default public distribution retains the complete management implementation.
FROM backend_base AS community
COPY --from=frontend_community /app/frontend/dist /app/frontend/dist
RUN printf '{"edition":"community","features":["permission_management"]}\n' > /app/edition.json
ENV SINKDUCE_EDITION=community

ARG API_PORT=18900
EXPOSE ${API_PORT}

ENTRYPOINT ["./entrypoint.sh"]
