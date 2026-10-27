"""Retry helpers for provider HTTP calls."""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_RETRIES = 3
DEFAULT_BASE_DELAY = 2.0


def is_rate_limit_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    body = getattr(exc, "body", None)
    parts = [str(exc), type(exc).__name__, str(body) if body is not None else ""]
    msg = " ".join(parts).lower()
    return (
        "429" in msg
        or "throttl" in msg
        or "ratequota" in msg
        or "rate_quota" in msg
        or "rate limit" in msg
        or "too many requests" in msg
    )


def is_timeout_error(exc: BaseException) -> bool:
    """True when the HTTP/SDK call timed out — do not retry for minutes."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return (
        "timeout" in name
        or "timed out" in msg
        or "timeout" in msg
    )


def is_unretryable_image_error(exc: BaseException) -> bool:
    """True when the provider rejected the bytes (WMF/EMF, corrupt, etc.)."""
    msg = str(exc).lower()
    return (
        "image format is illegal" in msg
        or "cannot be opened" in msg
        or "invalidparameter" in msg.replace(" ", "").replace(".", "")
        or "invalid_parameter" in msg
        or ("400" in msg and "image" in msg and "invalid" in msg)
    )


def retry_delay(attempt: int, base_delay: float = DEFAULT_BASE_DELAY) -> float:
    """Seconds to wait after *attempt* (1-based) hit a rate limit."""
    return (base_delay ** attempt) + random.uniform(0.0, 0.5)


def retry_on_rate_limit(
    fn: Callable[[], T],
    *,
    retries: int = DEFAULT_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    description: str = "API call",
) -> T:
    """Call *fn*; on 429 wait 2s / 4s / 8s (plus jitter) and retry."""
    last: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if not is_rate_limit_error(exc) or attempt >= retries:
                raise
            delay = retry_delay(attempt, base_delay)
            logger.warning(
                "[Retry] rate limit on %s (attempt %d/%d), sleeping %.1fs",
                description,
                attempt,
                retries,
                delay,
            )
            time.sleep(delay)
    raise last  # pragma: no cover