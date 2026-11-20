"""Shared config for all tests — reads ports from .env once."""

import os
import urllib.request

_ENV_FILE = os.path.join(os.path.dirname(__file__), "..", ".env")
_ENV_CACHE: dict[str, str] | None = None


def _load_env() -> dict[str, str]:
    """Parse .env into a dict, cached after first read."""
    global _ENV_CACHE
    if _ENV_CACHE is not None:
        return _ENV_CACHE
    _ENV_CACHE = {}
    try:
        with open(_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    _ENV_CACHE[k.strip()] = v.strip()
    except (OSError, IOError):
        pass
    return _ENV_CACHE


def api_port() -> str:
    """Backend API port.  Env var SINKDUCE_API_PORT > .env API_PORT > 18900."""
    env = os.environ.get("SINKDUCE_API_PORT")
    if env:
        return env
    return _load_env().get("API_PORT", "18900")


def qdrant_http_port() -> str:
    """Qdrant HTTP port.  Env var QDRANT_HTTP_PORT > .env QDRANT_HTTP_PORT > 6343."""
    env = os.environ.get("QDRANT_HTTP_PORT")
    if env:
        return env
    return _load_env().get("QDRANT_HTTP_PORT", "6343")


def api_base() -> str:
    return f"http://localhost:{api_port()}"


def qdrant_base() -> str:
    return f"http://localhost:{qdrant_http_port()}"


# Live Hub suites create and remove fixtures, so their target must be explicit.
HUB_TEST_URL = (os.environ.get("SINKDUCE_TEST_INSTANCE_URL") or os.environ.get("SINKDUCE_HUB_TEST_URL", "")).strip().rstrip("/")
HUB_SELF_URL = os.environ.get("SINKDUCE_TEST_SELF_URL", HUB_TEST_URL).rstrip("/")


def hub_test_instance_ready() -> bool:
    if not HUB_TEST_URL:
        return False

    try:
        with urllib.request.urlopen(HUB_TEST_URL + "/health", timeout=3) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False
