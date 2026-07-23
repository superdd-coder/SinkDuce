from __future__ import annotations

import asyncio
import logging
import struct
import threading
from typing import Any, Callable

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptSegment
from src.meeting.transcription.base import RealtimeTranscriptionProvider
from src.meeting.transcription.registry import realtime_transcription_registry

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "funasr/paraformer-zh-streaming"
_HUB = "hf"
# chunk_size = [lookback, chunk_duration, lookahead] in 60ms frames
# [0, 10, 5] → 0ms lookback, 600ms chunk, 300ms lookahead
_CHUNK_SIZE = [0, 10, 5]
_SAMPLE_RATE = 16000
_BYTES_PER_SAMPLE = 2  # 16-bit PCM
_FRAME_DURATION_S = 0.06  # 60ms per frame
_CHUNK_SAMPLES = _CHUNK_SIZE[1] * int(_SAMPLE_RATE * _FRAME_DURATION_S)
_CHUNK_BYTES = _CHUNK_SAMPLES * _BYTES_PER_SAMPLE
_SILENCE_THRESHOLD = 2  # 2 consecutive empty chunks (~1.2s) triggers sentence split


@realtime_transcription_registry.register(
    "funasr_local_realtime",
    display_name="FunASR (local, realtime)",
)
class FunASRLocalRealtimeTranscription(RealtimeTranscriptionProvider):
    """Local FunASR real-time streaming transcription.

    Uses ``paraformer-zh-streaming`` with incremental ``generate()`` calls.
    Audio frames are buffered until a full chunk (600 ms) is available,
    then processed in a background thread.  Partial results are forwarded
    via the ``on_segment`` callback.

    Speaker diarization is *not* supported in streaming mode — use the
    file transcription provider (``funasr_local``) for diarization.
    """

    supports_hot_words = True
    SUPPORTED_LANGUAGE_HINTS = [
        {"code": "auto", "label": "Auto"},
        {"code": "zh", "label": "Chinese"},
        {"code": "en", "label": "English"},
    ]

    def __init__(self, config: TranscriptionProviderConfig):
        from funasr import AutoModel  # lazy, optional dependency
        from src.providers.load_state import detect_device
        from src.meeting.transcription.funasr_local import _ensure_models_downloaded

        self._model_name = config.model or _DEFAULT_MODEL
        self._device = (config.device if config.device and config.device != "auto" else detect_device())
        if self._device != "cpu":
            try:
                import torch
                t = torch.zeros(1, device=self._device)
                del t
            except Exception:
                logger.warning("Device '%s' not available, falling back to CPU", self._device)
                self._device = "cpu"

        # Verify model is downloaded — prevent FunASR auto-download
        _ensure_models_downloaded([(self._model_name, "realtime transcription")])

        model_kwargs: dict[str, Any] = {"model": self._model_name, "device": self._device, "hub": _HUB}

        logger.info("Loading local FunASR streaming model: %s", self._model_name)
        self._model = AutoModel(**model_kwargs)
        logger.info("Local FunASR streaming model loaded")

        self._on_segment: Callable | None = None
        self._cache: dict[str, Any] = {}
        self._buffer = bytearray()
        self._audio_pos_s: float = 0.0
        self._lock = threading.Lock()
        self._running = False
        # Sentence tracking — accumulate incremental text into one segment
        self._sentence_counter: int = 0
        self._accumulated_text: str = ""
        self._current_key: str = "local-1"   # first key, set once in __init__
        self._sentence_start_s: float = 0.0
        self._silence_chunks: int = 0
        self._last_text_end_s: float = 0.0  # end time of last chunk that had text

    async def start(
        self,
        on_segment: Callable[[TranscriptSegment, bool, Any], None],
        hot_words: list | None = None,
        language_hints: list[str] | None = None,
    ) -> None:
        self._on_segment = on_segment
        self._cache = {}
        self._buffer = bytearray()
        self._audio_pos_s = 0.0
        # Do NOT reset _sentence_counter / _current_key here — they must
        # persist across toggle (reconnect) cycles so that new segments
        # get unique keys that don't collide with previously finalized
        # segments in the frontend's segmentMapRef.
        self._accumulated_text = ""
        self._sentence_start_s = 0.0
        self._silence_chunks = 0
        self._last_text_end_s = 0.0
        self._running = True
        if hot_words:
            hotword_str = " ".join(
                hw.get("text", "") if isinstance(hw, dict) else getattr(hw, "text", "")
                for hw in hot_words
            ).strip()
            if hotword_str:
                self._cache["hotword"] = hotword_str
                logger.info("Applying %d hot words via FunASR local realtime hotword", len(hot_words))
        logger.info("Local FunASR realtime transcription started")

    async def send_frame(self, audio_data: bytes) -> None:
        if not self._running:
            raise RuntimeError("Transcription session not started. Call start() first.")

        with self._lock:
            self._buffer.extend(audio_data)

            while len(self._buffer) >= _CHUNK_BYTES:
                chunk = bytes(self._buffer[:_CHUNK_BYTES])
                del self._buffer[:_CHUNK_BYTES]
                await self._process_chunk(chunk)

    async def stop(self) -> None:
        self._running = False

        # Flush remaining audio in buffer
        with self._lock:
            if self._buffer:
                chunk = bytes(self._buffer)
                self._buffer = bytearray()
                await self._process_chunk(chunk, is_last=True)
            else:
                # Send empty final chunk to flush model's internal buffer
                await self._process_chunk(b"", is_last=True)

        self._cache = {}
        logger.info("Local FunASR realtime transcription stopped")

    async def _process_chunk(
        self, chunk: bytes, is_last: bool = False,
    ) -> None:
        """Run inference on one audio chunk in a background thread.

        The streaming model returns *incremental* text (new words only),
        not cumulative.  We concatenate chunks into a single running
        segment and update the frontend with the same key each time.
        """
        loop = asyncio.get_running_loop()
        chunk_start_s = self._audio_pos_s
        chunk_dur_s = len(chunk) / (_SAMPLE_RATE * _BYTES_PER_SAMPLE)
        self._audio_pos_s += chunk_dur_s

        result = await loop.run_in_executor(
            None,
            lambda: self._model.generate(
                chunk,
                cache=self._cache,
                chunk_size=_CHUNK_SIZE,
                is_final=is_last,
                encoder_chunk_look_back=12,
                decoder_chunk_look_back=4,
            ),
        )

        if not result or not self._on_segment:
            if is_last and self._accumulated_text:
                self._emit_segment(chunk_start_s + chunk_dur_s, True)
            return

        entry = result[0] if isinstance(result, list) else result
        new_text = (entry.get("text") or "").strip()

        if new_text:
            self._silence_chunks = 0
            self._last_text_end_s = chunk_start_s + chunk_dur_s
            if self._accumulated_text:
                prev_char = self._accumulated_text[-1]
                if self._is_cjk(prev_char) or self._is_cjk(new_text[0]):
                    self._accumulated_text += new_text
                else:
                    self._accumulated_text += " " + new_text
            else:
                self._accumulated_text = new_text
                self._sentence_start_s = chunk_start_s
            self._emit_segment(chunk_start_s + chunk_dur_s, False)
        else:
            self._silence_chunks += 1
            if (self._silence_chunks >= _SILENCE_THRESHOLD
                    and self._accumulated_text):
                self._emit_segment(self._last_text_end_s, True)
                self._silence_chunks = 0

        if is_last and self._accumulated_text:
            self._emit_segment(chunk_start_s + chunk_dur_s, True)

    def _emit_segment(self, end_s: float, is_final: bool) -> None:
        """Send current accumulated text as a segment update."""
        if not self._accumulated_text or not self._on_segment:
            return
        segment = TranscriptSegment(
            start=self._sentence_start_s,
            end=end_s,
            text=self._accumulated_text,
            speaker_id=None,
        )
        self._on_segment(segment, is_final, self._current_key)
        if is_final:
            self._accumulated_text = ""
            self._sentence_counter += 1
            self._current_key = f"local-{self._sentence_counter + 1}"

    @staticmethod
    def _is_cjk(char: str) -> bool:
        cp = ord(char)
        return (
            0x4E00 <= cp <= 0x9FFF    # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
            or 0x3000 <= cp <= 0x303F  # CJK Symbols and Punctuation
            or 0xFF00 <= cp <= 0xFFEF  # Fullwidth Forms
        )
