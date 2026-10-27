"""Automatic Chat image capability checks for OpenAI-compatible endpoints."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import logging
import secrets
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def cache_root() -> Path:
    from src.config import get_data_dir
    return get_data_dir() / "capabilities" / "chat_vision"


def _challenge() -> tuple[str, str]:
    token = "".join(str(secrets.randbelow(10)) for _ in range(6))
    image = Image.new("RGB", (100, 24), "white")
    ImageDraw.Draw(image).text((8, 5), token, fill="black")
    image = image.resize((800, 192), Image.Resampling.NEAREST)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(), token


def _ask(llm, image: str) -> str:
    from src.prompts import CHAT_VISION_PROBE
    probe = copy.copy(llm)
    probe._client = llm._client.with_options(timeout=12, max_retries=0)
    content = [
        {"type": "text", "text": CHAT_VISION_PROBE},
        {"type": "image_url", "image_url": {"url": image}},
    ]
    thinking = False if probe._thinking_mode in {"toggle", "always", "none"} or probe._is_dashscope else None
    try:
        return probe.generate(content, thinking=thinking, temperature=1, max_tokens=256)
    except Exception as exc:
        if "max_tokens" not in str(exc) or "max_completion_tokens" not in str(exc):
            raise
        response = probe._client.chat.completions.create(
            model=probe._model, messages=[{"role": "user", "content": content}], max_completion_tokens=512,
        )
        return response.choices[0].message.content or "" if response.choices else ""


def ensure_chat_vision(llm) -> bool:
    """Prefer declared capability, otherwise verify with a synthetic image once.

    No user file is sent during discovery. Unknown responses and transport failures
    are retried later, rather than remembered as permanent lack of image support.
    Cache keys isolate endpoint, model and credentials without persisting secrets.
    """
    if getattr(llm, "supports_vision", False) is True:
        return True
    from src.providers.llm.openai_compat import OpenAICompatLLM
    if not isinstance(llm, OpenAICompatLLM):
        return False
    identity = json.dumps([llm._base_url.rstrip("/"), llm._model, llm._client.api_key])
    key = hashlib.sha256(identity.encode()).hexdigest()
    path = cache_root() / f"{key}.json"
    with _lock:
        try:
            cached = json.loads(path.read_text())
            if cached["expires"] > time.time():
                llm.supports_vision = cached["visual"] is True
                return llm.supports_vision
        except (OSError, ValueError, KeyError, TypeError):
            pass
        visual, ttl = False, 60
        try:
            image, expected = _challenge()
            answer = _ask(llm, image)
            visual = isinstance(answer, str) and answer.strip() == expected
            if visual:
                ttl = 86400
        except Exception as exc:
            message = str(exc).lower()
            if "image" in message and any(s in message for s in ("not support", "unsupported", "text only", "text-only")):
                ttl = 86400
            logger.info("Chat image capability check inconclusive or unavailable: %s", type(exc).__name__)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"visual": visual, "expires": time.time() + ttl}))
            temporary.replace(path)
        except OSError:
            logger.debug("Could not save Chat image capability cache", exc_info=True)
        llm.supports_vision = visual
        return visual
