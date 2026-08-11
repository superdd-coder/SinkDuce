from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from src.meeting.models import TranscriptSegment, TranscriptionResult


class FileTranscriptionProvider(ABC):
    """Transcribe an audio file to text with speaker diarization."""

    # Supported language hints declared per adapter.
    # Each entry: {"code": "zh", "label": "中文"}
    SUPPORTED_LANGUAGE_HINTS: list[dict[str, str]] = []
    # Class flag — read by registry / active-provider-info (do not use @property).
    supports_hot_words: bool = False

    @abstractmethod
    async def transcribe(
        self,
        file_path: str,
        language_hints: list[str] | None = None,
        hot_words: list | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file.

        Args:
            file_path: Local file path or HTTP(S) URL to the audio file.
            language_hints: Optional language hints (e.g. ["zh", "en"]).
            hot_words: Optional list of HotWordItem dicts for domain-specific
                       vocabulary. Adapters that don't support hot words will
                       silently ignore this parameter.

        Returns:
            TranscriptionResult with segments and optional speaker IDs.
        """
        ...


class RealtimeTranscriptionProvider(ABC):
    """Real-time streaming transcription via WebSocket."""

    SUPPORTED_LANGUAGE_HINTS: list[dict[str, str]] = []
    # Class flag — read by registry / active-provider-info (do not use @property).
    supports_hot_words: bool = False

    @abstractmethod
    async def start(
        self,
        on_segment: Callable[[TranscriptSegment, bool, Any], None],
        hot_words: list | None = None,
        language_hints: list[str] | None = None,
    ) -> None:
        """Start the realtime transcription session.

        Args:
            on_segment: Callback invoked as on_segment(segment, is_final, key)
                        for each recognized segment. ``key`` is a stable
                        identifier (e.g. sentence_id) callers can use to
                        deduplicate updates for the same sentence.
            hot_words: Optional list of HotWordItem dicts.
            language_hints: Optional language hints (e.g. ["zh", "en"]).
        """
        ...

    @abstractmethod
    async def send_frame(self, audio_data: bytes) -> None:
        """Send a raw PCM audio frame to the transcription service."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transcription session and release resources."""
        ...
