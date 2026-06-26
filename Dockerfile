# Stage 1: Build frontend
FROM node:20-slim AS frontend
RUN npm install -g pnpm@10
WORKDIR /app/frontend
COPY frontend/pnpm-lock.yaml frontend/package.json ./
RUN pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm run build

# Stage 2: Python app
FROM python:3.11-slim

# Use uv (fast Rust-based pip replacement) for Python deps
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

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
