from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptSegment
from src.meeting.transcription.base import RealtimeTranscriptionProvider
from src.meeting.transcription.registry import realtime_transcription_registry

try:
    import dashscope
    from dashscope.audio.asr import Recognition, VocabularyService

    _HAS_DASHSCOPE = True
except ImportError:
    _HAS_DASHSCOPE = False

logger = logging.getLogger(__name__)

_DEFAULT_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
MODEL_FUN_ASR_REALTIME = "fun-asr-realtime"
MODEL_QWEN_REALTIME = "qwen-audio-3.0-asr-flash-streaming"
ALLOWED_REALTIME_MODELS = (MODEL_FUN_ASR_REALTIME, MODEL_QWEN_REALTIME)
_DEFAULT_REALTIME_MODEL = MODEL_FUN_ASR_REALTIME


def _require_dashscope() -> None:
    if not _HAS_DASHSCOPE:
        raise ImportError(
            "dashscope package is required for DashScope transcription. "
            "Install it with: pip install dashscope"
        )


def _build_instant_vocabulary(hot_words: list | None) -> dict[str, int] | None:
    """Convert HotWordItem list to DashScope instant vocabulary dict.

    Instant hot words are ``{text: weight}`` with weight in [1, 5] (or 50 for
    super-hot). App weights are 1–10; map them into the 1–5 range used by the API.
    """
    if not hot_words:
        return None
    vocab: dict[str, int] = {}
    for hw in hot_words:
        text = hw.get("text", "") if isinstance(hw, dict) else getattr(hw, "text", "")
        weight = hw.get("weight", 4) if isinstance(hw, dict) else getattr(hw, "weight", 4)
        if not text:
            continue
        vocab[str(text)] = min(5, max(1, int(weight) // 2 + 1))
    return vocab if vocab else None


def resolve_realtime_model(model: str | None) -> str:
    """Normalize provider config model to an allowed DashScope realtime ASR id."""
    m = (model or "").strip()
    if m in ALLOWED_REALTIME_MODELS:
        return m
    if m:
        logger.warning(
            "[DashScope RT] Unknown realtime model %r; using default %s. Allowed: %s",
            m, _DEFAULT_REALTIME_MODEL, ", ".join(ALLOWED_REALTIME_MODELS),
        )
    return _DEFAULT_REALTIME_MODEL


@realtime_transcription_registry.register(
    "dashscope_funasr_realtime",
    display_name="DashScope ASR (realtime)",
)
class DashScopeRealtimeTranscription(RealtimeTranscriptionProvider):
    """DashScope Fun-ASR / Qwen-Audio real-time streaming via WebSocket.

    Uses the ``dashscope.audio.asr.Recognition`` class with a callback to
    deliver incremental transcription results.  Because the underlying SDK
    is synchronous/blocking, all blocking calls are offloaded to a thread
    via ``asyncio.to_thread``.

    Model is selected via ``config.model``:
      - ``fun-asr-realtime`` (default) — precompiled hot words via VocabularyService
      - ``qwen-audio-3.0-asr-flash-streaming`` — instant ``vocabulary`` dict

    Both share the same Recognition WebSocket API. Instant hot words are
    Qwen-only; Fun-ASR accepts one ``language_hints`` value.
    Semantic sentence segmentation is always enabled (better for meetings).
    """

    supports_hot_words = True
    SUPPORTED_LANGUAGE_HINTS = [
        {"code": "auto", "label": "Auto"},
        {"code": "zh", "label": "Chinese"},
        {"code": "en", "label": "English"},
        {"code": "ja", "label": "Japanese"},
        {"code": "ko", "label": "Korean"},
        {"code": "vi", "label": "Vietnamese"},
        {"code": "th", "label": "Thai"},
        {"code": "id", "label": "Indonesian"},
        {"code": "ms", "label": "Malay"},
    ]
    SUPPORTED_MODELS = [
        {"value": MODEL_FUN_ASR_REALTIME, "label": "fun-asr-realtime (FunASR cloud)"},
        {"value": MODEL_QWEN_REALTIME, "label": "qwen-audio-3.0-asr-flash-streaming"},
    ]

    @classmethod
    def max_language_hints(cls, model: str | None = None) -> int:
        return 4 if resolve_realtime_model(model) == MODEL_QWEN_REALTIME else 1

    def __init__(self, config: TranscriptionProviderConfig):
        _require_dashscope()
        self._api_key = config.api_key
        self._model = resolve_realtime_model(config.model)
        self._base_ws_url = _DEFAULT_WS_URL
        self._recognition: Any | None = None
        self._vocab_id: str | None = None

    def _uses_instant_vocabulary(self) -> bool:
        """Qwen supports instant vocabulary; Fun-ASR uses VocabularyService."""
        return self._model == MODEL_QWEN_REALTIME

    def _create_vocabulary(self, hot_words: list) -> str | None:
        """Create a cloud-side hot words vocabulary and return its ID (Fun-ASR)."""
        from src.meeting.transcription.dashscope_file import (
            _build_precompiled_vocabulary_items,
        )

        vocabulary = _build_precompiled_vocabulary_items(hot_words)
        if not vocabulary:
            return None

        import time

        service = VocabularyService(api_key=self._api_key)
        prefix = f"rt{int(time.time() * 1000) % 10000000:07d}"
        logger.info(
            "[DashScope RT] Creating vocabulary: prefix=%s model=%s words=%s",
            prefix, self._model, vocabulary,
        )
        try:
            vocab_id = service.create_vocabulary(
                target_model=self._model,
                prefix=prefix,
                vocabulary=vocabulary,
            )
        except Exception as exc:
            logger.error("[DashScope RT] Failed to create vocabulary: %s", exc)
            return None

        if not vocab_id:
            logger.error("[DashScope RT] Empty vocabulary_id returned")
            return None

        for _ in range(30):
            try:
                full_response = service.query_vocabulary(vocab_id)
                status = full_response[0] if isinstance(full_response, list) else full_response
                if isinstance(status, dict) and status.get("status") == "OK":
                    time.sleep(2)
                    logger.info(
                        "[DashScope RT] Vocabulary %s ready with %d hot words",
                        vocab_id, len(vocabulary),
                    )
                    return vocab_id
            except Exception as exc:
                logger.warning("[DashScope RT] Query vocabulary %s failed: %s", vocab_id, exc)
            time.sleep(0.5)

        logger.error("[DashScope RT] Vocabulary %s timed out waiting for OK status", vocab_id)
        self._delete_vocabulary(vocab_id)
        return None

    def _delete_vocabulary(self, vocab_id: str | None = None) -> None:
        vid = vocab_id or self._vocab_id
        if not vid:
            return
        try:
            VocabularyService(api_key=self._api_key).delete_vocabulary(vid)
            logger.info("[DashScope RT] Deleted vocabulary %s", vid)
        except Exception as exc:
            logger.warning("[DashScope RT] Failed to delete vocabulary %s: %s", vid, exc)
        if vid == self._vocab_id:
            self._vocab_id = None

    async def start(
        self,
        on_segment: Callable[[TranscriptSegment, bool, Any], None],
        hot_words: list | None = None,
        language_hints: list[str] | None = None,
        translation_target: str | None = None,
    ) -> None:
        """Start a real-time transcription session.

        Args:
            on_segment: Called as on_segment(segment, is_final, key) for every
                        recognized chunk. ``key`` is a stable identifier
                        (sentence_id from the SDK, or a fallback) so callers
                        can deduplicate updates for the same sentence.
            hot_words: Optional list of HotWordItem dicts.
            language_hints: Optional language hints (e.g. ["zh", "en"]).
                Fun-ASR realtime uses only the first hint.
            translation_target: Ignored — plain ASR adapter. Live translation
                is handled by ``DashScopeLiveTranslateRealtime``.
        """
        dashscope.api_key = self._api_key
        dashscope.base_websocket_api_url = self._base_ws_url

        callback = _RealtimeCallback(on_segment)

        recognition_kwargs: dict[str, Any] = {
            "model": self._model,
            "format": "pcm",
            "sample_rate": 16000,
            # Semantic segmentation: better for meeting / long-form speech
            # than pure VAD silence thresholds.
            "semantic_punctuation_enabled": True,
            "callback": callback,
        }

        vocab_note = 0
        if self._uses_instant_vocabulary():
            vocabulary = _build_instant_vocabulary(hot_words)
            if vocabulary:
                recognition_kwargs["vocabulary"] = vocabulary
                vocab_note = len(vocabulary)
                logger.info(
                    "[DashScope RT] Using instant vocabulary (%d terms) for realtime transcription",
                    vocab_note,
                )
        elif hot_words:
            vocab_id = await asyncio.to_thread(self._create_vocabulary, hot_words)
            if vocab_id:
                recognition_kwargs["vocabulary_id"] = vocab_id
                self._vocab_id = vocab_id
                vocab_note = 1

        self._recognition = Recognition(**recognition_kwargs)

        start_kwargs: dict[str, Any] = {}
        from src.meeting.transcription.base import clip_language_hints

        clipped = clip_language_hints(
            language_hints, self.max_language_hints(self._model)
        )
        if clipped:
            start_kwargs["language_hints"] = clipped

        logger.info(
            "Starting realtime transcription: model=%s semantic_punctuation=True "
            "vocab=%s start_kwargs=%s",
            self._model,
            vocab_note,
            start_kwargs,
        )
        await asyncio.to_thread(self._recognition.start, **start_kwargs)

    async def send_frame(self, audio_data: bytes) -> None:
        """Send a raw PCM audio frame to the DashScope recognition service."""
        if self._recognition is None:
            raise RuntimeError("Transcription session not started. Call start() first.")
        await asyncio.to_thread(self._recognition.send_audio_frame, audio_data)

    async def stop(self) -> None:
        """Stop the transcription session."""
        if self._recognition is not None:
            try:
                await asyncio.to_thread(self._recognition.stop)
            except Exception:
                # Client may already be gone (abrupt disconnect makes the SDK
                # raise). The vocabulary table must still be freed — accounts
                # cap at 10 tables and leaks block ALL hot words with 429.
                logger.warning(
                    "Realtime recognition.stop() raised; freeing vocabulary anyway",
                    exc_info=True,
                )
            self._recognition = None
            logger.info("Realtime transcription stopped")
        if self._vocab_id:
            await asyncio.to_thread(self._delete_vocabulary, self._vocab_id)


class _RealtimeCallback:
    """DashScope Recognition callback that forwards events to the caller.

    The SDK wraps each sentence event as:
        result.output = {"sentence": [sentence_dict]}
    where sentence_dict has: text, begin_time, end_time, speaker_id, sentence_id.
    A sentence is final when end_time is not None. The same sentence_id is
    sent multiple times as the SDK refines end_time and speaker_id.
    """

    def __init__(self, on_segment: Callable[[TranscriptSegment, bool, Any], None]):
        self._on_segment = on_segment

    def on_open(self) -> None:
        logger.debug("Realtime transcription WebSocket opened")

    def on_close(self, status_code: int = 0, status_message: str = "") -> None:
        logger.debug("Realtime transcription WebSocket closed: %s %s", status_code, status_message)

    def on_complete(self) -> None:
        logger.debug("Realtime transcription session complete")

    def on_error(self, result: Any) -> None:
        # Don't call str(result) — dashscope's __str__ can raise KeyError on
        # partial error responses. Fall back to type+repr instead.
        message = getattr(result, "message", None) or repr(result)
        logger.error("Realtime transcription error: %s", message)

    def on_event(self, result: Any) -> None:
        try:
            output = getattr(result, "output", None)
            if not output or not isinstance(output, dict):
                return

            sentence_data = output.get("sentence")
            if sentence_data is None:
                return

            # SDK wraps as {"sentence": [dict, ...]}
            sentences = sentence_data if isinstance(sentence_data, list) else [sentence_data]

            for sent in sentences:
                if not isinstance(sent, dict):
                    continue
                # Skip heartbeats
                if sent.get("heartbeat"):
                    continue

                text = (sent.get("text") or "").strip()
                if not text:
                    continue

                start_ms = float(sent.get("begin_time", 0) or 0)
                end_ms = float(sent.get("end_time", 0) or 0)
                speaker_id = sent.get("speaker_id")
                sentence_id = sent.get("sentence_id")
                # DashScope signals sentence end by having end_time set
                is_final = sent.get("end_time") is not None

                # DEBUG: log every event so we can verify the SDK actually
                # returns speaker_id when diarization is enabled.
                print(
                    f"[Realtime-Event] text={text[:60]!r} "
                    f"speaker_id={speaker_id!r} sentence_id={sentence_id!r} "
                    f"is_final={is_final} start={start_ms} end={end_ms}",
                    flush=True,
                )

                segment = TranscriptSegment(
                    start=start_ms / 1000.0,
                    end=end_ms / 1000.0,
                    text=text,
                    speaker_id=str(speaker_id) if speaker_id is not None else None,
                )
                # Use sentence_id as the unique key for this segment; falls back
                # to a hashable tuple of (start, text) when the SDK doesn't
                # provide one (older dashscope versions).
                key = sentence_id if sentence_id is not None else f"{start_ms}:{text}"
                self._on_segment(segment, is_final, key)
        except Exception:
            logger.warning("Failed to process realtime transcription event", exc_info=True)
