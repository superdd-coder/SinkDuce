"""Bounded Chat Completions parameter negotiation for compatible providers."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Provider instances can be short lived. Keep learned capabilities scoped to the
# endpoint, model, and reasoning settings for the current process only.
_PARAMETER_RULES: dict[tuple[str, str, str], frozenset[str]] = {}


def _key(kwargs: dict, base_url: str) -> tuple[str, str, str]:
    extra = kwargs.get("extra_body") or {}
    posture = {
        "reasoning_effort": kwargs.get("reasoning_effort"),
        "thinking": extra.get("thinking"),
        "enable_thinking": extra.get("enable_thinking"),
        "thinking_budget": extra.get("thinking_budget"),
    }
    return (
        base_url.strip().rstrip("/"),
        str(kwargs.get("model", "")),
        json.dumps(posture, sort_keys=True),
    )


def prepare_chat_kwargs(kwargs: dict, *, base_url: str = "") -> dict:
    """Apply previously learned restrictions without mutating the request."""
    prepared = dict(kwargs)
    rules = _PARAMETER_RULES.get(_key(kwargs, base_url), frozenset())
    if "temperature" in rules:
        prepared.pop("temperature", None)
    if "max_tokens" in rules and "max_tokens" in prepared:
        limit = prepared.pop("max_tokens")
        prepared.setdefault("max_completion_tokens", limit)
    return prepared


def _rejected_parameter(error: Exception, kwargs: dict) -> str | None:
    # Auth, quota, server failures, and stream interruptions are not capability
    # signals. Only explicit request validation errors may change parameters.
    if getattr(error, "status_code", None) != 400:
        return None
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    param = detail.get("param")
    code = detail.get("code")
    message = str(detail.get("message") or error).lower()

    def mentions(name: str) -> bool:
        if param is not None:
            return param == name
        return re.search(
            rf"(?:unsupported (?:parameter|value)|unknown (?:parameter|field)|"
            rf"unrecognized request argument(?: supplied)?)\s*:\s*['\"`]?{name}\b"
            rf"|\b{name}['\"`]?\s+(?:is not supported|does not support)",
            message,
        ) is not None

    if "temperature" in kwargs and mentions("temperature") and (
        re.search(r"\bonly (?:the )?default\b", message)
        or code == "unsupported_parameter"
        or re.search(
            r"(?:unsupported parameter|unknown parameter|unrecognized request argument(?: supplied)?)"
            r"\s*:\s*['\"`]?temperature\b|\btemperature['\"`]?\s+is (?:not supported|unsupported)",
            message,
        )
    ):
        return "temperature"
    if (
        "max_tokens" in kwargs and mentions("max_tokens") and "max_completion_tokens" in message
        and (code == "unsupported_parameter" or "unsupported" in message or "not supported" in message)
    ):
        return "max_tokens"
    return None


def create_chat_completion(send: Callable[..., T], kwargs: dict[str, Any], *, base_url: str = "") -> T:
    """Negotiate two known incompatibilities before response streaming begins.

    Each field can change only once: at most three HTTP requests. Returning the
    stream untouched avoids replaying responses after any content was emitted.
    """
    key = _key(kwargs, base_url)
    current = prepare_chat_kwargs(kwargs, base_url=base_url)
    for attempt in range(3):
        try:
            return send(**current)
        except Exception as error:
            parameter = _rejected_parameter(error, current)
            if parameter is None or attempt == 2:
                raise
            _PARAMETER_RULES[key] = _PARAMETER_RULES.get(key, frozenset()) | {parameter}
            if parameter == "temperature":
                current = {k: v for k, v in current.items() if k != "temperature"}
            else:
                current = dict(current)
                limit = current.pop("max_tokens")
                current.setdefault("max_completion_tokens", limit)
            logger.info("LLM request adapted unsupported parameter: %s", parameter)
    raise AssertionError("Parameter retry budget exhausted")  # pragma: no cover
