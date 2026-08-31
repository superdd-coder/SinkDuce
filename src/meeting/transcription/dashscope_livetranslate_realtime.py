"""DashScope LiveTranslate realtime adapter — bilingual live captions.

Wraps ``qwen3.5-livetranslate-flash-realtime`` (successor of the legacy
Gummy realtime model) behind the meeting module's
``RealtimeTranscriptionProvider`` ABC. One audio stream in, two linked
outputs: the source transcript (via the in-session ASR channel) and the
simultaneous translation, paired per VAD turn by
``livetranslate_events.LiveTranslateEventReducer``.

The user-facing flow keeps plain ASR untouched: this adapter is only
used when a meeting requests a ``translation_target`` (the WS route then
swaps the active realtime provider for this one). It can also be set as
the active realtime provider directly in Settings.

Notes:
  - No speaker diarization (the model does not return speaker ids).
  - No hot-word tables: LiveTranslate takes source→translation term
    mappings instead (``corpus.phrases``), which the hot-word system
    does not model; ``supports_hot_words`` stays False.
  - ``end_session()`` performs the required ``session.finish`` handshake
    so the last utterance is flushed before close.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Callable

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptSegment
from src.meeting.transcription.base import RealtimeTranscriptionProvider
from src.meeting.transcription.livetranslate_events import LiveTranslateEventReducer
from src.meeting.transcription.registry import realtime_transcription_registry

try:
    import dashscope
    from dashscope.audio.qwen_omni import (
        MultiModality,
        OmniRealtimeCallback,  # noqa: F401  (duck-typed in _ReducerCallback)
        OmniRealtimeConversation,
    )
    from dashscope.audio.qwen_omni.omni_realtime import TranslationParams

    _HAS_DASHSCOPE = True
except ImportError:
    _HAS_DASHSCOPE = False

logger = logging.getLogger(__name__)

MODEL_QWEN_LIVE_TRANSLATE = "qwen3.5-livetranslate-flash-realtime"
MODEL_QWEN_LIVE_TRANSLATE_V3 = "qwen3-livetranslate-flash-realtime"
ALLOWED_LIVE_TRANSLATE_MODELS = (
    MODEL_QWEN_LIVE_TRANSLATE,
    MODEL_QWEN_LIVE_TRANSLATE_V3,
)
_DEFAULT_LIVE_TRANSLATE_MODEL = MODEL_QWEN_LIVE_TRANSLATE

# Source transcript channel opened inside the LiveTranslate session.
_ASR_INPUT_MODEL = "qwen3-asr-flash-realtime"
_DEFAULT_TARGET_LANGUAGE = "en"


def _require_dashscope() -> None:
    if not _HAS_DASHSCOPE:
        raise ImportError(
            "dashscope package is required for DashScope LiveTranslate. "
            "Install it with: pip install dashscope"
        )


def _default_voice_for(model: str) -> str:
    """Server-accepted default voice per model family.

    The SDK always includes ``voice`` in session.update (``null`` when
    unset) and the LiveTranslate server rejects ``null`` with
    ``InternalError.Algo.InvalidParameter: Voice 'null' is not supported``
    as soon as real speech triggers a response — even in text-only mode.
    Qwen3.5 default is Tina, Qwen3 is Cherry (per model docs).
    """
    if model == MODEL_QWEN_LIVE_TRANSLATE_V3:
        return "Cherry"
    return "Tina"


def resolve_live_translate_model(model: str | None) -> str:
    """Normalize provider config model to an allowed LiveTranslate id."""
    m = (model or "").strip()
    if m in ALLOWED_LIVE_TRANSLATE_MODELS:
        return m
    if m:
        logger.warning(
            "[LiveTranslate] Unknown model %r; using default %s. Allowed: %s",
            m, _DEFAULT_LIVE_TRANSLATE_MODEL, ", ".join(ALLOWED_LIVE_TRANSLATE_MODELS),
        )
    return _DEFAULT_LIVE_TRANSLATE_MODEL


class _ReducerCallback:
    """Duck-typed OmniRealtimeCallback: JSON-string events → reducer.

    Kept free of SDK base classes so this module imports (and tests run)
    even when the dashscope package is not installed.
    """

    def __init__(self, reducer: LiveTranslateEventReducer):
        self._reducer = reducer

    def on_open(self) -> None:
        logger.debug("[LiveTranslate] WebSocket opened")

    def on_close(self, close_status_code=None, close_msg=None) -> None:
        # INFO+: server close reasons (e.g. InvalidParameter) are the primary
        # diagnosis signal — DEBUG hides them by default.
        if close_status_code not in (None, 1000):
            logger.warning(
                "[LiveTranslate] WebSocket closed: %s %s",
                close_status_code,
                close_msg,
            )
        else:
            logger.debug(
                "[LiveTranslate] WebSocket closed: %s %s",
                close_status_code,
                close_msg,
            )

    def on_event(self, message: Any) -> None:
        self._reducer.handle(message)


@realtime_transcription_registry.register(
    "dashscope_livetranslate_realtime",
    display_name="DashScope LiveTranslate (realtime translation)",
)
class DashScopeLiveTranslateRealtime(RealtimeTranscriptionProvider):
    """Realtime speech translation via qwen3.5-livetranslate-flash-realtime."""

    supports_hot_words = False
    SUPPORTED_LANGUAGE_HINTS = [
        {"code": "auto", "label": "Auto"},
        {"code": "zh", "label": "Chinese"},
        {"code": "en", "label": "English"},
        {"code": "ja", "label": "Japanese"},
        {"code": "ko", "label": "Korean"},
        {"code": "yue", "label": "Cantonese"},
        {"code": "de", "label": "German"},
        {"code": "fr", "label": "French"},
        {"code": "ru", "label": "Russian"},
        {"code": "es", "label": "Spanish"},
        {"code": "pt", "label": "Portuguese"},
        {"code": "it", "label": "Italian"},
        {"code": "th", "label": "Thai"},
        {"code": "vi", "label": "Vietnamese"},
        {"code": "id", "label": "Indonesian"},
        {"code": "ms", "label": "Malay"},
        {"code": "ar", "label": "Arabic"},
        {"code": "tr", "label": "Turkish"},
        {"code": "hi", "label": "Hindi"},
    ]
    SUPPORTED_MODELS = [
        {
            "value": MODEL_QWEN_LIVE_TRANSLATE,
            "label": "qwen3.5-livetranslate-flash-realtime (recommended)",
        },
        {
            "value": MODEL_QWEN_LIVE_TRANSLATE_V3,
            "label": "qwen3-livetranslate-flash-realtime (legacy)",
        },
    ]
    MAX_LANGUAGE_HINTS = 1  # single source language for the ASR channel

    def __init__(self, config: TranscriptionProviderConfig):
        _require_dashscope()
        self._api_key = config.api_key
        self._model = resolve_live_translate_model(config.model)
        # Optional dedicated workspace; SDK defaults to the shared
        # dashscope endpoint when unset.
        self._workspace = (getattr(config, "workspace_id", None) or "").strip() or None
        self._url = (config.base_url or "").strip() or None
        self._conversation: Any | None = None

    async def start(
        self,
        on_segment: Callable[[TranscriptSegment, bool, Any], None],
        hot_words: list | None = None,
        language_hints: list[str] | None = None,
        translation_target: str | None = None,
    ) -> None:
        """Start a realtime translation session.

        Args:
            on_segment: Called as on_segment(segment, is_final, key) per VAD
                        turn. ``segment.translation`` carries the target text;
                        the same key updates until both streams finalize.
            hot_words: Ignored (LiveTranslate uses source→translation term
                       mappings, not ASR hot-word tables).
            language_hints: Optional single source language hint.
            translation_target: Target language code (default "en").
        """
        _require_dashscope()
        if _HAS_DASHSCOPE:
            # The conversation also receives the key directly; this keeps
            # the module-level default consistent for other SDK calls.
            dashscope.api_key = self._api_key

        reducer = LiveTranslateEventReducer(on_segment)
        callback = _ReducerCallback(reducer)
        self._conversation = OmniRealtimeConversation(
            model=self._model,
            callback=callback,
            url=self._url,
            api_key=self._api_key,
            workspace=self._workspace,
        )
        await asyncio.to_thread(self._conversation.connect)

        from src.meeting.transcription.base import clip_language_hints

        clipped = clip_language_hints(
            language_hints, self.max_language_hints(self._model)
        )
        target = (translation_target or "").strip() or _DEFAULT_TARGET_LANGUAGE

        def _configure() -> None:
            kwargs: dict[str, Any] = {
                "output_modalities": [MultiModality.TEXT],
                # See _default_voice_for: the server rejects voice=null on
                # the first speech-triggered response in text-only mode.
                "voice": _default_voice_for(self._model),
                "enable_input_audio_transcription": True,
                "input_audio_transcription_model": _ASR_INPUT_MODEL,
                "translation_params": TranslationParams(language=target),
            }
            if clipped:
                kwargs["transcription_params"] = TranslationParams(language=clipped[0])
            self._conversation.update_session(**kwargs)

        await asyncio.to_thread(_configure)
        logger.info(
            "[LiveTranslate] session started: model=%s target=%s source_hint=%s workspace=%s",
            self._model, target, clipped, self._workspace or "default",
        )

    async def send_frame(self, audio_data: bytes) -> None:
        """Send a raw PCM audio frame (16 kHz mono) to the session."""
        if self._conversation is None:
            raise RuntimeError("Translation session not started. Call start() first.")
        b64 = base64.b64encode(audio_data).decode("ascii")
        await asyncio.to_thread(self._conversation.append_audio, b64)

    async def stop(self) -> None:
        """Finish the session (flush handshake) and release resources."""
        conv = self._conversation
        if conv is None:
            return
        self._conversation = None

        def _shutdown() -> None:
            try:
                # end_session sends session.finish and waits for
                # session.finished — skipping it drops the last utterance.
                conv.end_session(timeout=5)
            except Exception:
                logger.warning(
                    "[LiveTranslate] end_session raised; closing anyway",
                    exc_info=True,
                )
            try:
                conv.close()
            except Exception:
                logger.warning("[LiveTranslate] close raised", exc_info=True)

        await asyncio.to_thread(_shutdown)
        logger.info("[LiveTranslate] session stopped")
