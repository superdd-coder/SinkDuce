"""Thinking-posture classification for OpenAI-compatible LLM providers.

Model families disagree on how "disable thinking" is expressed:
  - toggle  — enable_thinking / thinking.type=disabled works (qwen3.7 …)
  - always  — the model always thinks; the API rejects thinking-off with a
    400 (DashScope code 1210 "该模型始终思考，不支持关闭思考") and expects
    reasoning_effort (low/high/max) instead (百炼第三方智谱, qwen3.8 …)
  - none    — the endpoint rejects the thinking parameters entirely

`probe_thinking_mode` sends one minimal thinking-OFF request (max_tokens=1)
to classify a model. It runs at provider save time so real tasks never pay
the trial-and-error cost; `is_always_think_error` also powers a one-shot
runtime retry for models the probe could not classify (auth/network issues).
"""

from __future__ import annotations

import logging

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = httpx.Timeout(15, connect=6)

# Postures learned at runtime (fallback retry) or at save time (probe), keyed
# by (base_url, model). Slot-specific LLM instances are created per request —
# this keeps the adaptation alive across them for the process lifetime.
# Per-model entries here beat the provider-level `thinking_mode` config field
# (which is probed for the provider's default model only).
_LEARNED_MODES: dict[tuple[str, str], str] = {}


def _mode_key(base_url: str, model: str) -> tuple[str, str]:
    return ((base_url or "").strip().rstrip("/").lower(), (model or "").strip())


def remember_thinking_mode(base_url: str, model: str, mode: str) -> None:
    if mode:
        _LEARNED_MODES[_mode_key(base_url, model)] = mode


def learned_thinking_mode(base_url: str, model: str) -> str:
    return _LEARNED_MODES.get(_mode_key(base_url, model), "")


def _thinking_off_extra_body(is_dashscope: bool) -> dict:
    # Mirror of openai_compat.build_thinking_extra_body(…, thinking=False);
    # kept local to avoid an import cycle (openai_compat imports this module).
    if is_dashscope:
        return {"enable_thinking": False}
    return {"thinking": {"type": "disabled"}}


def is_always_think_error(err: BaseException) -> bool:
    """True when the 400 is the "this model always thinks" rejection."""
    msg = str(err)
    low = msg.lower()
    if "不支持关闭思考" in msg or "始终思考" in msg:
        return True
    if "always think" in low or "always-thinking" in low:
        return True
    if "'code': '1210'" in msg or '"code": "1210"' in msg or "'code': 1210" in msg:
        return True
    return False


_PARAM_REJECT_MARKERS = (
    "unrecognized request argument",
    "unknown parameter",
    "unknown field",
    "extra fields not permitted",
    "unexpected keyword",
    "not permitted",
    "not allowed",
)


def is_thinking_param_unsupported(err: BaseException) -> bool:
    """True when the 400 rejects the thinking parameter name itself."""
    msg = str(err)
    low = msg.lower()
    if "enable_thinking" not in low and "thinking" not in low:
        return False
    return any(marker in low for marker in _PARAM_REJECT_MARKERS)


def probe_thinking_mode(
    base_url: str,
    api_key: str,
    model: str,
    is_dashscope: bool | None = None,
) -> str:
    """Classify a model's thinking posture with one minimal request.

    Returns "toggle" | "always" | "none", or "" when the probe is
    inconclusive (auth/network/other errors) — callers keep the previous
    value in that case.
    """
    if is_dashscope is None:
        is_dashscope = "dashscope.aliyuncs.com" in (base_url or "")
    extra = _thinking_off_extra_body(is_dashscope)
    client = OpenAI(
        base_url=base_url,
        api_key=(api_key or "").strip(),
        timeout=_PROBE_TIMEOUT,
    )
    try:
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            extra_body=extra,
        )
        remember_thinking_mode(base_url, model, "toggle")
        return "toggle"
    except Exception as e:
        if is_always_think_error(e):
            logger.info("Thinking probe: %s is always-thinking", model)
            remember_thinking_mode(base_url, model, "always")
            return "always"
        if is_thinking_param_unsupported(e):
            logger.info("Thinking probe: %s rejects thinking params", model)
            remember_thinking_mode(base_url, model, "none")
            return "none"
        logger.info("Thinking probe inconclusive for %s: %s", model, e)
        return ""
