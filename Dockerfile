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

# Stage 1: Build frontend
FROM ${NODE_IMAGE} AS frontend
# Prefer Corepack (ships with Node 20) over `npm install -g pnpm`.
# Fallback: npmmirror if npmjs is unreachable.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable \
    && (corepack prepare pnpm@10.15.1 --activate \
        || npm install -g pnpm@10.15.1 --registry=https://registry.npmmirror.com)
WORKDIR /app/frontend
COPY frontend/pnpm-lock.yaml frontend/package.json ./
RUN pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm run build

# Stage 2: Python app
FROM ${PYTHON_IMAGE}

# Use uv (fast Rust-based pip replacement) for Python deps
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-eng \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
    --index-strategy unsafe-best-match \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    .[diarization]

COPY . .
COPY --from=frontend /app/frontend/dist /app/frontend/dist
# Strip CRLF line endings (Windows git clone) then make executable
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

ARG API_PORT=18900
EXPOSE ${API_PORT}

ENTRYPOINT ["./entrypoint.sh"]
