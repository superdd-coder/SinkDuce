from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import httpx
from openai import OpenAI

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptSegment, TranscriptionResult
from src.meeting.transcription.base import FileTranscriptionProvider
from src.meeting.transcription.registry import file_transcription_registry

logger = logging.getLogger(__name__)


@file_transcription_registry.register("openai_compatible", display_name="OpenAI-Compatible (Whisper)")
class OpenAICompatFileTranscription(FileTranscriptionProvider):
    """File transcription via OpenAI Whisper API using the OpenAI SDK."""

    SUPPORTED_LANGUAGE_HINTS = [
        {"code": "auto", "label": "Auto"},
        {"code": "zh", "label": "Chinese"},
        {"code": "en", "label": "English"},
        {"code": "ja", "label": "Japanese"},
        {"code": "ko", "label": "Korean"},
        {"code": "ms", "label": "Malay"},
        {"code": "th", "label": "Thai"},
        {"code": "id", "label": "Indonesian"},
    ]

    def __init__(self, config: TranscriptionProviderConfig):
        base_url = (config.base_url or "https://api.openai.com/v1").strip().rstrip("/")
        api_key = (config.api_key or "").strip()
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = (config.model or "whisper-1").strip()

    async def transcribe(
        self,
        file_path: str,
        language_hints: list[str] | None = None,
        hot_words: list | None = None,
    ) -> TranscriptionResult:
        local_path = file_path
        cleanup = False

        # Download remote files to a temp file first
        if file_path.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=120) as http:
                resp = await http.get(file_path)
                resp.raise_for_status()
                suffix = Path(file_path).suffix or ".wav"
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.write(resp.content)
                tmp.close()
                local_path = tmp.name
                cleanup = True

        try:
            # Build optional parameters
            extra: dict = {}
            if language_hints:
                extra["language"] = language_hints[0]
            if hot_words:
                words = []
                for hw in hot_words:
                    t = hw.get("text", "") if isinstance(hw, dict) else getattr(hw, "text", "")
                    if t:
                        words.append(t)
                if words:
                    extra["prompt"] = ", ".join(words)

            # Try OpenAI SDK (multipart) first; fall back to JSON with base64
            # — OpenRouter and some proxies reject multipart/form-data.
            result = None
            try:
                result = await asyncio.to_thread(
                    lambda: self._client.audio.transcriptions.create(
                        model=self._model,
                        file=open(local_path, "rb"),
                        response_format="verbose_json",
                        **extra,
                    )
                )
            except Exception as e:
                err_msg = str(e)
                if "invalid content-type" in err_msg or "multipart" in err_msg:
                    import base64
                    from types import SimpleNamespace
                    audio_bytes = Path(local_path).read_bytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                    json_body: dict = {"model": self._model, "file": audio_b64, "response_format": "verbose_json"}
                    json_body.update(extra)
                    base = str(self._client.base_url).rstrip("/")
                    headers = {"Authorization": f"Bearer {self._client.api_key}", "Content-Type": "application/json"}
                    async with httpx.AsyncClient(timeout=120) as _http:
                        json_resp = await _http.post(f"{base}/audio/transcriptions", json=json_body, headers=headers)
                        json_resp.raise_for_status()
                        data = json_resp.json()
                        result = SimpleNamespace(
                            text=data.get("text", ""),
                            segments=[SimpleNamespace(**s) for s in data.get("segments", [])],
                            language=data.get("language"),
                        )
                else:
                    raise

            segments = []
            for seg in getattr(result, "segments", []) or []:
                segments.append(TranscriptSegment(
                    start=getattr(seg, "start", 0.0),
                    end=getattr(seg, "end", 0.0),
                    text=(getattr(seg, "text", "") or "").strip(),
                ))

            return TranscriptionResult(
                text=getattr(result, "text", "") or "",
                segments=segments,
                language=getattr(result, "language", None),
            )
        finally:
            if cleanup and os.path.exists(local_path):
                os.unlink(local_path)
