"""Smoke test: run against a live API server.
Usage: pytest tests/test_smoke.py -v   (requires docker compose up app)
       SINKDUCE_API_PORT=18905 pytest tests/test_smoke.py -v
"""

import os
import httpx
import pytest


def _port():
    """Read API_PORT from .env, env var, or default."""
    env = os.environ.get("SINKDUCE_API_PORT")
    if env:
        return env
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", ".env")) as f:
            for line in f:
                if line.strip().startswith("API_PORT="):
                    return line.strip().split("=", 1)[1].strip()
    except (OSError, IOError):
        pass
    return "18900"


BASE = f"http://localhost:{_port()}"
TIMEOUT = 30


def test_health():
    r = httpx.get(f"{BASE}/health", timeout=TIMEOUT)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_collections():
    r = httpx.get(f"{BASE}/api/collections", timeout=TIMEOUT)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
