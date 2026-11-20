"""Bounded Chat Completions parameter negotiation for compatible providers."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any, TypeVar

from src.config import LLMApiMode
from src.providers.openai_endpoint import is_openai_endpoint as is_openai_endpoint

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Provider instances can be short lived. Keep learned capabilities scoped to the
# endpoint, model, and reasoning settings for the current process only.
_PARAMETER_RULES: dict[tuple[str, str, str], frozenset[str]] = {}
_VENDOR_THINKING_FIELDS = {"thinking", "enable_thinking", "thinking_budget"}


def resolve_llm_api_mode(base_url: str, selected: LLMApiMode | None = None) -> LLMApiMode:
    return selected or ("responses" if is_openai_endpoint(base_url) else "chat_completions")


def _key(kwargs: dict, base_url: str) -> tuple[str, str, str]:
    extra = {} if is_openai_endpoint(base_url) else kwargs.get("extra_body") or {}
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


def _openai_messages(messages: list[dict]) -> list[dict]:
    """Keep tool text paired with its call and send images after the full result batch."""
    from src.prompts import CHAT_TOOL_IMAGE_EVIDENCE

    result = []
    images = []
    for index, message in enumerate(messages):
        if message["role"] != "tool":
            result.append({key: value for key, value in message.items() if key != "reasoning_content"})
            continue
        content = message["content"]
        call_id = message["tool_call_id"]
        if isinstance(content, list):
            tool_images = [part for part in content if part.get("type") == "image_url"]
            content = [part for part in content if part.get("type") == "text"] or ""
            if tool_images:
                images.append({"type": "text", "text": CHAT_TOOL_IMAGE_EVIDENCE.format(tool_call_id=call_id)})
                images.extend(tool_images)
        result.append({"role": "tool", "tool_call_id": call_id, "content": content})

        at_batch_end = index + 1 == len(messages) or messages[index + 1]["role"] != "tool"
        if images and at_batch_end:
            result.append({"role": "user", "content": images})
            images = []
    return result


def prepare_chat_kwargs(kwargs: dict, *, base_url: str = "") -> dict:
    """Prepare the endpoint's wire format and apply learned restrictions."""
    prepared = dict(kwargs)
    rules = _PARAMETER_RULES.get(_key(kwargs, base_url), frozenset())
    openai_endpoint = is_openai_endpoint(base_url)
    if "temperature" in rules:
        prepared.pop("temperature", None)
    if (openai_endpoint or "max_tokens" in rules) and "max_tokens" in prepared:
        limit = prepared.pop("max_tokens")
        prepared.setdefault("max_completion_tokens", limit)
    if prepared.get("tools") == []:
        prepared.pop("tools")
    if not prepared.get("tools") and prepared.get("tool_choice") in ("auto", "none"):
        prepared.pop("tool_choice")
    if openai_endpoint:
        extra = prepared.pop("extra_body", None) or {}
        extra = {key: value for key, value in extra.items() if key not in _VENDOR_THINKING_FIELDS}
        if extra:
            prepared["extra_body"] = extra
        if "messages" in prepared:
            prepared["messages"] = _openai_messages(prepared["messages"])
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
