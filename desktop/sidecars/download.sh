#!/usr/bin/env bash
# Download / cache Qdrant + ffmpeg into desktop/sidecars (gitignored).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/desktop/sidecars"
CACHE="$ROOT/desktop/.cache/downloads"
QDRANT_VER="${SINKDUCE_QDRANT_VERSION:-1.13.2}"
ARCH="$(uname -m)"

mkdir -p "$DEST" "$CACHE"

if [[ "$ARCH" != "arm64" ]]; then
  echo "This packager only supports Apple Silicon (arm64). Got: $ARCH" >&2
  exit 1
fi

qdrant_url="https://github.com/qdrant/qdrant/releases/download/v${QDRANT_VER}/qdrant-aarch64-apple-darwin.tar.gz"
qdrant_tar="$CACHE/qdrant-aarch64-apple-darwin-${QDRANT_VER}.tar.gz"

if [[ ! -x "$DEST/qdrant" ]]; then
  echo "→ downloading Qdrant v${QDRANT_VER}"
  curl -fL --retry 3 -o "$qdrant_tar" "$qdrant_url"
  tmp="$(mktemp -d)"
  tar -xzf "$qdrant_tar" -C "$tmp"
  found="$(find "$tmp" -type f -name qdrant -perm +111 | head -n 1)"
  if [[ -z "$found" ]]; then
    found="$(find "$tmp" -type f -name qdrant | head -n 1)"
  fi
  if [[ -z "$found" ]]; then
    echo "qdrant binary not found in archive" >&2
    exit 1
  fi
  cp "$found" "$DEST/qdrant"
  chmod +x "$DEST/qdrant"
  rm -rf "$tmp"
fi

if [[ ! -x "$DEST/ffmpeg" ]]; then
  if command -v ffmpeg >/dev/null 2>&1; then
    echo "→ copying host ffmpeg ($(command -v ffmpeg))"
    cp "$(command -v ffmpeg)" "$DEST/ffmpeg"
    chmod +x "$DEST/ffmpeg"
  else
    echo "ffmpeg not found on PATH. Install with: brew install ffmpeg" >&2
    exit 1
  fi
fi

echo "sidecars ready: $DEST/qdrant  $DEST/ffmpeg"
