"""Shared config for all tests — reads ports from .env once."""

import os

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
