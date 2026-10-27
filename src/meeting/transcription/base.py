from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable

from src.meeting.models import TranscriptSegment, TranscriptionResult

logger = logging.getLogger(__name__)


class FileTranscriptionProvider(ABC):
    """Transcribe an audio file to text with speaker diarization."""

    # Supported language hints declared per adapter.
    # Each entry: {"code": "zh", "label": "中文"}
    SUPPORTED_LANGUAGE_HINTS: list[dict[str, str]] = []
    # Official DashScope / Whisper-style APIs accept one hint unless overridden.
    MAX_LANGUAGE_HINTS: int = 1
    # Class flag — read by registry / active-provider-info (do not use @property).
    supports_hot_words: bool = False

    @classmethod
    def max_language_hints(cls, model: str | None = None) -> int:
        return cls.MAX_LANGUAGE_HINTS

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
    MAX_LANGUAGE_HINTS: int = 1
    # Class flag — read by registry / active-provider-info (do not use @property).
    supports_hot_words: bool = False
    # 上游会话会不会「静默死亡」而需要客户端空闲时喂静音保活。
    # 云端 SDK（DashScope）有 23s 静默计时器，会自己杀掉会话 → True；
    # 本地模型（FunASR ONNX）没有上游可死，喂静音只是白烧推理 CPU → False。
    wants_silence_keepalive: bool = False

    # 由调用方（WS 路由）注册：上游会话自行死亡时回调（静默超时 / 网络断 /
    # provider 重启）。**不放进 start() 签名**是刻意的——那会波及全部适配器与
    # 既有测试的替身；这里用可选属性，未注册即 no-op。
    on_upstream_lost: Callable[[str], None] | None = None

    def _notify_lost(self, reason: str) -> None:
        """适配器检测到上游死亡时调用。回调异常不得影响音频链路。"""
        cb = self.on_upstream_lost
        if cb is None:
            return
        try:
            cb(reason)
        except Exception:
            logger.exception("on_upstream_lost callback failed (reason=%s)", reason)

    @classmethod
    def max_language_hints(cls, model: str | None = None) -> int:
        return cls.MAX_LANGUAGE_HINTS

    @abstractmethod
    async def start(
        self,
        on_segment: Callable[[TranscriptSegment, bool, Any], None],
        hot_words: list | None = None,
        language_hints: list[str] | None = None,
        translation_target: str | None = None,
    ) -> None:
        """Start the realtime transcription session.

        Args:
            on_segment: Callback invoked as on_segment(segment, is_final, key)
                        for each recognized segment. ``key`` is a stable
                        identifier (e.g. sentence_id) callers can use to
                        deduplicate updates for the same sentence.
            hot_words: Optional list of HotWordItem dicts.
            language_hints: Optional language hints (e.g. ["zh", "en"]).
            translation_target: Optional target language code for live
                        translation. Plain-ASR adapters ignore this; only
                        translation-capable adapters (LiveTranslate) use it.
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


def language_hint_limit(adapter_cls, model: str | None = None) -> int:
    """How many language_hints the active adapter+model accepts (official caps)."""
    fn = getattr(adapter_cls, "max_language_hints", None)
    if callable(fn):
        try:
            n = fn(model)
        except TypeError:
            n = fn()
    else:
        n = getattr(adapter_cls, "MAX_LANGUAGE_HINTS", 1)
    try:
        return max(1, int(n))
    except (TypeError, ValueError):
        return 1


def clip_language_hints(
    hints: list[str] | None,
    max_hints: int = 1,
) -> list[str] | None:
    """Drop auto / empties and trim to the model cap. None means “let the model detect”."""
    if not hints:
        return None
    cleaned = [h for h in hints if h and h != "auto"]
    if not cleaned:
        return None
    return cleaned[: max(1, int(max_hints))]
