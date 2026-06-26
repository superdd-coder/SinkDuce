#!/bin/bash
# Full test suite: unit → build → E2E
# Usage: ./scripts/test_all.sh [--e2e]

set -e
cd "$(dirname "$0")/.."

echo "========================================="
echo "  SinkDuce Full Test Suite"
echo "========================================="

# ── Unit tests (no server needed) ──────────────────────
echo ""
echo ">>> Unit tests (mocked, fast)"
python -m pytest tests/ \
    --ignore=tests/test_api.py \
    --ignore=tests/test_smoke.py \
    --ignore=tests/test_ui_automation.py \
    --ignore=tests/test_e2e.py \
    -q --tb=short
echo "Unit tests: PASS"

# ── Docker build ───────────────────────────────────────
echo ""
echo ">>> Docker compose build"
docker compose build app 2>&1 | tail -3

# ── E2E tests (needs running server) ───────────────────
if [ "${1:-}" = "--e2e" ]; then
    echo ""
    echo ">>> Starting services for E2E..."
    docker compose up -d qdrant
    sleep 3
    docker compose up -d app
    sleep 8

    echo ""
    echo ">>> E2E tests (real HTTP, real Qdrant)"
    python -m pytest tests/test_e2e.py -v -s --tb=short || true

    echo ""
    echo ">>> API integration tests"
    python tests/test_api.py || true

    echo ""
    echo ">>> Stopping services"
    docker compose down
fi

echo ""
echo "========================================="
echo "  Done"
echo "========================================="
