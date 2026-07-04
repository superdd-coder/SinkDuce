from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
from pathlib import Path

import httpx

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptSegment, TranscriptionResult
from src.meeting.transcription.base import FileTranscriptionProvider
from src.meeting.transcription.registry import file_transcription_registry

logger = logging.getLogger(__name__)


@file_transcription_registry.register("openrouter", display_name="OpenRouter (Whisper)")
class OpenRouterFileTranscription(FileTranscriptionProvider):
    """File transcription via OpenRouter.

    Sends audio as base64 in JSON body (OpenRouter rejects multipart/form-data).
    """

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
        base_url = (config.base_url or "https://openrouter.ai/api/v1").strip().rstrip("/")
        self._base_url = base_url
        self._api_key = (config.api_key or "").strip()
        self._model = (config.model or "openai/whisper-large-v3-turbo").strip()

    async def transcribe(
        self,
        file_path: str,
        language_hints: list[str] | None = None,
        hot_words: list | None = None,
    ) -> TranscriptionResult:
        local_path = file_path
        cleanup = False

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
            audio_bytes = Path(local_path).read_bytes()
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

            suffix = Path(local_path).suffix.lstrip(".").lower()
            audio_format = suffix if suffix else "wav"

            body: dict = {
                "model": self._model,
                "input_audio": {
                    "data": audio_b64,
                    "format": audio_format,
                },
            }

            if language_hints:
                body["language"] = language_hints[0]
            if hot_words:
                words = []
                for hw in hot_words:
                    t = hw.get("text", "") if isinstance(hw, dict) else getattr(hw, "text", "")
                    if t:
                        words.append(t)
                if words:
                    body["prompt"] = ", ".join(words)

            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=120) as http:
                resp = await http.post(
                    f"{self._base_url}/audio/transcriptions",
                    json=body,
                    headers=headers,
                )
                if not resp.is_success:
                    logger.error(
                        "OpenRouter transcription failed [%s]: %s",
                        resp.status_code, resp.text[:500],
                    )
                resp.raise_for_status()
                data = resp.json()

            text = data.get("text", "") or ""
            segments: list[TranscriptSegment] = []
            for seg in data.get("segments", []) or []:
                segments.append(TranscriptSegment(
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=(seg.get("text", "") or "").strip(),
                ))
            # OpenRouter returns text without segments — create one segment
            if text and not segments:
                segments.append(TranscriptSegment(start=0.0, end=0.0, text=text.strip()))

            return TranscriptionResult(
                text=text,
                segments=segments,
                language=data.get("language"),
            )
        finally:
            if cleanup and os.path.exists(local_path):
                os.unlink(local_path)
