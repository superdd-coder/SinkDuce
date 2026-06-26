from __future__ import annotations

import logging
import re
from typing import Generator

import httpx
from openai import OpenAI

from src.config import LLMProviderConfig
from src.providers.base import LLMProvider
from src.providers.registry import llm_registry

logger = logging.getLogger(__name__)

_DEFAULT_TEMPERATURE = 0.1
_THINK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.DOTALL)


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
        self._default_max_tokens = getattr(config, "max_tokens", 0) or 0

    def _resolve_temperature(self, temperature: float | None) -> float:
        return temperature if temperature is not None else _DEFAULT_TEMPERATURE

    def _resolve_max_tokens(self, max_tokens: int | None) -> int:
        if max_tokens is not None and max_tokens > 0:
            return max_tokens
        if self._default_max_tokens > 0:
            return self._default_max_tokens
        return 0

    def generate(self, prompt: str, system: str = "", temperature: float | None = None, max_tokens: int | None = None, response_format: dict | None = None, thinking: bool | None = None) -> str:
        logger.info("LLM generate: model=%s prompt_len=%d max_tokens=%s thinking=%s json_mode=%s",
                    self._model, len(prompt),
                    max_tokens if max_tokens else (self._default_max_tokens or "none"),
                    thinking, bool(response_format))
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = dict(
            model=self._model,
            messages=messages,
            temperature=self._resolve_temperature(temperature),
        )
        resolved_mt = self._resolve_max_tokens(max_tokens)
        if resolved_mt > 0:
            kwargs["max_tokens"] = resolved_mt
        if response_format:
            kwargs["response_format"] = response_format
        if thinking is not None:
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}

        response = self._client.chat.completions.create(**kwargs)
        if not response.choices:
            return ""
        return _strip_think(response.choices[0].message.content or "")

    # Default visual description prompt — used when caller doesn't provide one
    _DEFAULT_VISUAL_PROMPT = (
        "Analyze this image and describe it concisely in 2-5 sentences of plain text — no markdown, no bullet points, no headings. "
        "Cover what is shown (photo, chart, diagram, etc.), key elements and their relationships, any visible text transcribed exactly, "
        "and notable data like numbers, labels, or axes. Be objective and factual, no speculation. "
        "Match the language of visible text, or use English if none. Omit purely decorative or background elements."
    )

    def describe_image(self, image_base64: str, image_mime: str = "image/png", prompt: str = "") -> str:
        """Generate a text description of an image using Vision API.

        Args:
            image_base64: Base64-encoded image data (without data URI prefix)
            image_mime: MIME type of the image (default image/png)
            prompt: Custom prompt; uses _DEFAULT_VISUAL_PROMPT if empty

        Returns:
            Generated description string
        """
        logger.info("LLM describe_image: model=%s mime=%s", self._model, image_mime)
        text_prompt = prompt or self._DEFAULT_VISUAL_PROMPT
        data_uri = f"data:{image_mime};base64,{image_base64}"
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text_prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri},
                    },
                ],
            }
        ]
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0.1,
            max_tokens=1024,
        )
        if not response.choices:
            return ""
        return _strip_think(response.choices[0].message.content or "")

    def generate_stream(self, prompt: str, system: str = "", temperature: float | None = None, max_tokens: int | None = None, response_format: dict | None = None) -> Generator[str, None, None]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = dict(
            model=self._model,
            messages=messages,
            temperature=self._resolve_temperature(temperature),
            stream=True,
        )
        resolved_mt = self._resolve_max_tokens(max_tokens)
        if resolved_mt > 0:
            kwargs["max_tokens"] = resolved_mt
        if response_format:
            kwargs["response_format"] = response_format
        if thinking is not None:
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}

        stream = self._client.chat.completions.create(**kwargs)
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

    # ── Batch API (for enrichment context generation) ────────────────────

    def batch_submit(self, requests: list[dict]) -> str:
        """Submit a batch of generation requests via OpenAI-compatible Batch API.

        Each request: {"prompt": str, "system": str, "temperature": float | None, "max_tokens": int | None}
        Returns batch_id string.
        """
        import json
        import tempfile
        import os

        # Build JSONL — one request object per line
        lines = []
        for i, req in enumerate(requests):
            body = {
                "model": self._model,
                "messages": [],
                "temperature": req.get("temperature") or _DEFAULT_TEMPERATURE,
            }
            mt = req.get("max_tokens") or self._default_max_tokens
            if mt > 0:
                body["max_tokens"] = mt
            if req.get("system"):
                body["messages"].append({"role": "system", "content": req["system"]})
            body["messages"].append({"role": "user", "content": req.get("prompt", "")})

            lines.append(json.dumps({
                "custom_id": str(i),
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": body,
            }, ensure_ascii=False))

        # Write to temp file
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
        try:
            tmp.write("\n".join(lines))
            tmp.close()

            # Upload file
            with open(tmp.name, "rb") as f:
                uploaded = self._client.files.create(file=f, purpose="batch")
            logger.info("Batch: uploaded file %s (%d requests)", uploaded.id, len(requests))

            # Create batch
            batch = self._client.batches.create(
                input_file_id=uploaded.id,
                endpoint="/v1/chat/completions",
                completion_window="24h",
            )
            logger.info("Batch: created batch %s", batch.id)
            return batch.id
        finally:
            os.unlink(tmp.name)

    def batch_poll(self, batch_id: str) -> list[str] | None:
        """Poll batch status. Returns list of generated texts when complete, None if still running."""
        try:
            batch = self._client.batches.retrieve(batch_id)
        except Exception as e:
            logger.error("Batch: failed to retrieve %s: %s", batch_id, e)
            return ["[batch error]"]

        status = batch.status
        logger.info("Batch: %s status=%s", batch_id, status)

        if status in ("validating", "in_progress", "finalizing"):
            return None  # still running

        if status == "completed":
            # Download results
            output = self._client.files.content(batch.output_file_id).text
            results = [""] * (batch.request_counts.total or 0)
            for line in output.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    import json
                    item = json.loads(line)
                    idx = int(item.get("custom_id", -1))
                    if idx >= 0 and idx < len(results):
                        resp = item.get("response", {})
                        body = resp.get("body", {})
                        choices = body.get("choices", [])
                        if choices:
                            text = choices[0].get("message", {}).get("content", "")
                            results[idx] = _strip_think(text or "")
                except Exception:
                    continue
            return results

        # failed, expired, cancelled
        error_info = ""
        if hasattr(batch, 'errors') and batch.errors:
            error_info = f", errors={batch.errors}"
        logger.error("Batch: %s ended with status=%s%s", batch_id, status, error_info)
        raise RuntimeError(f"Batch {batch_id} failed with status={status}{error_info}")
