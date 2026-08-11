"""Shared provider instance cache to avoid repeated model loading."""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Any

logger = logging.getLogger(__name__)

_cache: dict[str, Any] = {}
_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _get_key_lock(key: str) -> threading.Lock:
    """Get or create a per-key lock for coordinating creation."""
    with _registry_lock:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def get_or_create(key: str, factory) -> Any:
    """Return a cached instance, or create one using factory() and cache it.

    Uses a per-key lock so that only one thread creates a given key, while
    other keys can still be created or invalidated concurrently.
    """
    # Fast path
    instance = _cache.get(key)
    if instance is not None:
        return instance

    key_lock = _get_key_lock(key)
    with key_lock:
        instance = _cache.get(key)
        if instance is not None:
            return instance

        logger.info("Creating provider instance: %s", key)
        instance = factory()
        _cache[key] = instance
        return instance


def peek(key: str) -> Any | None:
    """Return cached instance without creating one."""
    return _cache.get(key)


def has(key: str) -> bool:
    """True if a cached instance exists for key."""
    return key in _cache and _cache.get(key) is not None


def keys() -> list[str]:
    """Return current cache keys (copy)."""
    return list(_cache.keys())


def invalidate(key: str) -> None:
    """Remove a cached instance (e.g. when config changes)."""
    key_lock = _get_key_lock(key)
    with key_lock:
        _cache.pop(key, None)


def clear() -> None:
    """Clear all cached instances."""
    with _registry_lock:
        keys = list(_cache.keys())
    for key in keys:
        invalidate(key)
