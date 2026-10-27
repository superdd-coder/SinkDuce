#!/usr/bin/env bash
# Bundle a full CPython prefix (bin + libpython) + deps + app tree.
# A uv venv cannot be copied into a .app: its python is a symlink/stub
# that looks for libpython next to itself.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY_PREFIX="$ROOT/desktop/.cache/python"
APP_DIR="$ROOT/desktop/.cache/app"
SINKDUCE_EDITION="${SINKDUCE_EDITION:-community}"
if [[ "$SINKDUCE_EDITION" != "community" && "$SINKDUCE_EDITION" != "enterprise" ]]; then
  echo "unknown SINKDUCE_EDITION: $SINKDUCE_EDITION" >&2
  exit 1
fi

mkdir -p "$APP_DIR"

# Prefer uv's standalone CPython, not the project .venv (uv python find
# from the repo root resolves to .venv).
UV_PY_ROOT="${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}"
STANDALONE="$(ls -d "$UV_PY_ROOT"/cpython-3.12*-macos-aarch64-none 2>/dev/null | tail -n 1 || true)"
if [[ -z "$STANDALONE" || ! -x "$STANDALONE/bin/python3.12" ]]; then
  echo "→ installing standalone CPython 3.12 via uv"
  uv python install 3.12
  STANDALONE="$(ls -d "$UV_PY_ROOT"/cpython-3.12*-macos-aarch64-none | tail -n 1)"
fi
if [[ ! -x "$STANDALONE/bin/python3.12" ]]; then
  echo "could not find uv standalone CPython 3.12 under $UV_PY_ROOT" >&2
  exit 1
fi

echo "→ copying standalone CPython from $STANDALONE"
# Replace the old tree atomically: a Finder window sitting on this path can
# re-create .DS_Store mid-delete and break plain `rm -rf` with
# "Directory not empty". mv is instantaneous, so the build never depends on
# deletion finishing; the old tree is disposed of in the background.
rm -rf "${PY_PREFIX}.trash" 2>/dev/null || true
if [[ -e "$PY_PREFIX" ]]; then
  mv "$PY_PREFIX" "${PY_PREFIX}.trash"
  rm -rf "${PY_PREFIX}.trash" &
fi
mkdir -p "$PY_PREFIX"
rsync -a --exclude='.DS_Store' "$STANDALONE/" "$PY_PREFIX/"
# Copied prefix is ours; drop uv's "don't pip install here" guard.
rm -f "$PY_PREFIX/lib/python3.12/EXTERNALLY-MANAGED" \
      "$PY_PREFIX"/lib/python3.*/EXTERNALLY-MANAGED
# Ensure a stable name for the launcher.
if [[ ! -e "$PY_PREFIX/bin/python" ]]; then
  ln -s python3.12 "$PY_PREFIX/bin/python"
fi

PY="$PY_PREFIX/bin/python3.12"
if [[ ! -f "$PY_PREFIX/lib/libpython3.12.dylib" ]]; then
  echo "standalone python is missing lib/libpython3.12.dylib" >&2
  ls -la "$PY_PREFIX/lib" >&2 || true
  exit 1
fi

if [[ ! -f "$ROOT/pyproject.toml" ]]; then
  echo "missing $ROOT/pyproject.toml (restore it, then re-run)" >&2
  exit 1
fi

echo "→ installing SinkDuce [asr-onnx] into bundled CPython"
# This is a copied standalone prefix, not uv's managed install. --system
# writes into its lib/python3.12/site-packages.
# Install from ROOT with ".[asr-onnx]" so uv does not depend on path case
# (Lab/SinkDuce vs lab/sinkDuce) or bracket extras on the absolute path.
uv pip install --python "$PY" --system --upgrade pip
(
  cd "$ROOT"
  uv pip install --python "$PY" --system \
    --index-strategy unsafe-best-match \
    ".[asr-onnx]"
)

echo "→ copying src + frontend/dist"
rm -rf "$APP_DIR/src" "$APP_DIR/frontend" "$APP_DIR/ee"
mkdir -p "$APP_DIR/frontend"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '.DS_Store' \
  "$ROOT/src/" "$APP_DIR/src/"
if [[ ! -f "$ROOT/ee/LICENSE" ]]; then
  echo "management implementation and license are required for the full application" >&2
  exit 1
fi
rsync -a --exclude 'frontend/' --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  "$ROOT/ee/" "$APP_DIR/ee/"
if [[ ! -d "$ROOT/frontend/dist" ]]; then
  echo "frontend/dist missing; run pnpm build first" >&2
  exit 1
fi
rsync -a --delete --exclude '.DS_Store' "$ROOT/frontend/dist/" "$APP_DIR/frontend/dist/"
printf '{"edition":"%s","features":["permission_management"]}\n' "$SINKDUCE_EDITION" > "$APP_DIR/edition.json"
if [[ -f "$ROOT/config.yaml.template" ]]; then
  cp "$ROOT/config.yaml.template" "$APP_DIR/config.yaml.template"
fi
if [[ -f "$ROOT/pyproject.toml" ]]; then
  cp "$ROOT/pyproject.toml" "$APP_DIR/pyproject.toml"
fi

echo "python payload ready: $PY_PREFIX"
echo "app payload ready:    $APP_DIR"
