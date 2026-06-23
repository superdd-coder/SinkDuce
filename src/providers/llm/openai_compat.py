from __future__ import annotations

import logging
import re
import threading
from typing import Generator

import httpx
from openai import OpenAI

from src.config import LLMProviderConfig
from src.providers.base import LLMProvider
from src.providers.registry import llm_registry

logger = logging.getLogger(__name__)

_DEFAULT_TEMPERATURE = 0.1
_THINK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.DOTALL)
_llm_semaphore = threading.Semaphore(10)
_llm_semaphore_value = 10


def _get_llm_semaphore() -> threading.Semaphore:
    """Return a global semaphore shared across all LLM provider instances."""
    global _llm_semaphore, _llm_semaphore_value
    try:
        from src.config import get_config
        cfg = get_config()
        if cfg.llm.providers:
            p = next((p for p in cfg.llm.providers if p.is_default), cfg.llm.providers[0])
            limit = max(1, p.max_concurrent_requests)
            if limit != _llm_semaphore_value:
                _llm_semaphore = threading.Semaphore(limit)
                _llm_semaphore_value = limit
    except Exception:
        pass
    return _llm_semaphore


def _strip_think(text: str) -> str:
    """Remove `<think>...</think>` tags from LLM output."""
    return _THINK_RE.sub("", text).strip()


@llm_registry.register("openai_compatible", display_name="OpenAI-Compatible")
class OpenAICompatLLM(LLMProvider):
    def __init__(self, config: LLMProviderConfig):
        self._client = OpenAI(
            base_url=config.base_url.strip(),
            api_key=config.api_key.strip(),
            timeout=httpx.Timeout(1800, connect=30),
        )
        self._model = (config.default_model or config.model).strip()
        self._default_max_tokens = config.max_tokens

    def _resolve_temperature(self, temperature: float | None) -> float:
        return temperature if temperature is not None else _DEFAULT_TEMPERATURE

    def _resolve_max_tokens(self, max_tokens: int | None) -> int:
        return max_tokens if max_tokens is not None else self._default_max_tokens

    def generate(self, prompt: str, system: str = "", temperature: float | None = None, max_tokens: int | None = None) -> str:
        logger.info("LLM generate: model=%s prompt_len=%d", self._model, len(prompt))
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        with _get_llm_semaphore():
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=self._resolve_temperature(temperature),
                max_tokens=self._resolve_max_tokens(max_tokens),
            )
        if not response.choices:
            return ""
        return _strip_think(response.choices[0].message.content or "")

    def describe_image(self, image_base64: str, image_mime: str = "image/png") -> str:
        """Generate a text description of an image using Vision API.

        Args:
            image_base64: Base64-encoded image data (without data URI prefix)
            image_mime: MIME type of the image (default image/png)

        Returns:
            Generated description string (without the [Image Description]: prefix)
        """
        logger.info("LLM describe_image: model=%s mime=%s", self._model, image_mime)
        data_uri = f"data:{image_mime};base64,{image_base64}"
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Describe this image concisely in 2-4 sentences. Cover: what is shown, key elements, and any visible text. Be brief and objective. Match the language of visible text, or use English if none.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri},
                    },
                ],
            }
        ]
        with _get_llm_semaphore():
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.1,
                max_tokens=1024,
            )
        if not response.choices:
            return ""
        return _strip_think(response.choices[0].message.content or "")

    def generate_stream(self, prompt: str, system: str = "", temperature: float | None = None, max_tokens: int | None = None) -> Generator[str, None, None]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        with _get_llm_semaphore():
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=self._resolve_temperature(temperature),
                max_tokens=self._resolve_max_tokens(max_tokens),
                stream=True,
            )
            in_think = False
            buf = ""  # buffer for partial tag matches
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = buf + chunk.choices[0].delta.content
                    buf = ""
                    # Strip think tags from streaming output
                    if in_think:
                        end_idx = text.find("</think>")
                        if end_idx != -1:
                            text = text[end_idx + 8:]  # len("</think>") = 8
                            in_think = False
                        else:
                            # Check if text ends with partial "</think>"
                            for i in range(1, min(8, len(text) + 1)):
                                if "</think>".startswith(text[-i:]):
                                    buf = text[-i:]
                                    text = text[:-i]
                                    break
                            if not text and not buf:
                                continue  # still inside think block
                            elif not text:
                                continue
                    # Check for opening think tag
                    while "<think>" in text:
                        before, after = text.split("<think>", 1)
                        end_idx = after.find("</think>")
                        if end_idx != -1:
                            text = before + after[end_idx + 8:]
                        else:
                            text = before
                            in_think = True
                            break
                    # Buffer partial "<think>" at the end
                    if not in_think:
                        for i in range(1, min(7, len(text) + 1)):
                            if "<think>".startswith(text[-i:]):
                                buf = text[-i:]
                                text = text[:-i]
                                break
                    if text:
                        yield text
