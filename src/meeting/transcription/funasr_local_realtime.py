from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

import numpy as np

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptSegment
from src.meeting.transcription.base import RealtimeTranscriptionProvider
from src.meeting.transcription.registry import realtime_transcription_registry

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "funasr/paraformer-zh-streaming"
_HUB = "hf"
# chunk_size = [0, chunk_frames, lookahead_frames] in 60ms frames
# Official demo: each input is chunk_size[1] * 960 samples @ 16 kHz = 600ms
_CHUNK_SIZE = [0, 10, 5]
_SAMPLE_RATE = 16000
_BYTES_PER_SAMPLE = 2  # 16-bit PCM from browser
_SAMPLES_PER_FRAME = 960  # 60ms @ 16kHz
_CHUNK_SAMPLES = _CHUNK_SIZE[1] * _SAMPLES_PER_FRAME  # 9600
_CHUNK_BYTES = _CHUNK_SAMPLES * _BYTES_PER_SAMPLE  # 19200
_CHUNK_DURATION_S = _CHUNK_SAMPLES / _SAMPLE_RATE  # 0.6s

# Official FunASR streaming look-back (faster on CPU than 12/4).
_ENCODER_CHUNK_LOOK_BACK = 4
_DECODER_CHUNK_LOOK_BACK = 1

# If CPU decode lags, allow a short backlog. Dropping WITHOUT resetting the
# FunASR streaming cache causes "first sentences OK, then garbage".
_MAX_PENDING_CHUNKS = 16  # ~9.6s

# Official streaming demo keeps ONE cache for the whole clip and only sets
# is_final on the last chunk — UI is pure rolling text, not sentence chips.
# For an infinite live session we still need occasional boundaries, but:
#   - empty ASR text ≠ silence (Paraformer often returns '' mid-speech)
#   - only low post-AGC energy counts as quiet
_QUIET_PEAK = 0.03  # peak after AGC; below this ≈ near silence
_QUIET_CHUNKS_TO_END = 8  # ~4.8s of quiet audio before finalize + new cache

# Soft safety valve only (continuous loud speech). Prefer energy silence.
_MAX_UTTERANCE_CHUNKS = 60  # ~36s

# Mild AGC for near-silence capture (screen-share / quiet mic).
_AGC_MIN_PEAK = 0.02
_AGC_TARGET_PEAK = 0.25
_AGC_MAX_GAIN = 20.0


@realtime_transcription_registry.register(
    "funasr_local_realtime",
    display_name="FunASR (local, realtime)",
)
class FunASRLocalRealtimeTranscription(RealtimeTranscriptionProvider):
    """Local FunASR real-time streaming transcription.

    Browser → PCM s16le mono @16kHz over WebSocket.
    We convert to float32, queue 600ms chunks, and decode on a worker task
    so the WS receive loop is never blocked by FunASR generate().

    Critical design (aligned with FunASR official streaming demo):
    - Keep one streaming ``cache`` and emit **rolling** partials (same key,
      ``is_final=False``) as each chunk returns incremental text.
    - Do **not** treat empty ASR text as silence — Paraformer often returns
      ``''`` mid-speech. Only low post-AGC energy ends an utterance.
    - On real quiet / lag-drop / soft max length: flush with ``is_final=True``
      and open a fresh cache (infinite live sessions need occasional reset).
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
        self._device = (
            config.device if config.device and config.device != "auto" else detect_device()
        )
        if self._device != "cpu":
            try:
                import torch

                t = torch.zeros(1, device=self._device)
                del t
            except Exception:
                logger.warning(
                    "Device '%s' not available, falling back to CPU", self._device
                )
                self._device = "cpu"

        _ensure_models_downloaded([(self._model_name, "realtime transcription")])

        model_kwargs: dict[str, Any] = {
            "model": self._model_name,
            "device": self._device,
            "hub": _HUB,
            "disable_update": True,
        }

        logger.info("Loading local FunASR streaming model: %s", self._model_name)
        self._model = AutoModel(**model_kwargs)
        logger.info("Local FunASR streaming model loaded")

        self._on_segment: Callable | None = None
        self._cache: dict[str, Any] = {}
        self._hotword: str = ""
        self._buffer = bytearray()
        self._audio_pos_s: float = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._session_id: int = 0
        self._sentence_counter: int = 0
        self._accumulated_text: str = ""
        self._current_key: str = "local-1"
        self._sentence_start_s: float = 0.0
        self._quiet_chunks: int = 0  # consecutive low-energy chunks (not empty text)
        self._last_text_end_s: float = 0.0
        self._chunks_in: int = 0
        self._chunks_in_utterance: int = 0
        self._bytes_in: int = 0
        self._dropped_chunks: int = 0
        # Reasons to open a fresh streaming cache before the next generate()
        self._reset_cache_before_next: bool = False
        self._reset_reason: str = ""

        # Async decode queue (bytes, is_last) — never block WS on generate()
        self._pending: deque[tuple[bytes, bool]] = deque()
        self._worker_task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._infer_lock = threading.Lock()  # FunASR generate not re-entrant

    async def start(
        self,
        on_segment: Callable[[TranscriptSegment, bool, Any], None],
        hot_words: list | None = None,
        language_hints: list[str] | None = None,
    ) -> None:
        await self._stop_worker()

        hotword_str = ""
        if hot_words:
            hotword_str = " ".join(
                hw.get("text", "") if isinstance(hw, dict) else getattr(hw, "text", "")
                for hw in hot_words
            ).strip()
            if hotword_str:
                logger.info(
                    "Applying %d hot words via FunASR local realtime hotword",
                    len(hot_words),
                )

        with self._lock:
            self._session_id += 1
            sid = self._session_id
            self._on_segment = on_segment
            self._cache = {}
            self._hotword = hotword_str
            self._buffer = bytearray()
            self._audio_pos_s = 0.0
            self._accumulated_text = ""
            self._sentence_start_s = 0.0
            self._quiet_chunks = 0
            self._last_text_end_s = 0.0
            self._chunks_in = 0
            self._chunks_in_utterance = 0
            self._bytes_in = 0
            self._dropped_chunks = 0
            self._reset_cache_before_next = False
            self._reset_reason = ""
            self._pending.clear()
            self._running = True

        loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._worker_task = loop.create_task(
            self._decode_worker(sid), name=f"funasr-rt-worker-{sid}"
        )

        logger.info(
            "Local FunASR realtime started session=%d device=%s model=%s "
            "(PCM s16le @%dHz, chunk=%dms, max_pending=%d, enc_lb=%d, dec_lb=%d)",
            sid,
            self._device,
            self._model_name,
            _SAMPLE_RATE,
            int(_CHUNK_DURATION_S * 1000),
            _MAX_PENDING_CHUNKS,
            _ENCODER_CHUNK_LOOK_BACK,
            _DECODER_CHUNK_LOOK_BACK,
        )

    async def send_frame(self, audio_data: bytes) -> None:
        """Buffer PCM only — never block the WebSocket on model.generate()."""
        if not self._running or not audio_data:
            return

        to_queue: list[bytes] = []
        with self._lock:
            if not self._running:
                return
            self._buffer.extend(audio_data)
            self._bytes_in += len(audio_data)
            while len(self._buffer) >= _CHUNK_BYTES:
                to_queue.append(bytes(self._buffer[:_CHUNK_BYTES]))
                del self._buffer[:_CHUNK_BYTES]

            dropped_now = 0
            for raw in to_queue:
                # Drop oldest if lagging — MUST reset streaming cache after drop.
                while len(self._pending) >= _MAX_PENDING_CHUNKS:
                    self._pending.popleft()
                    self._dropped_chunks += 1
                    dropped_now += 1
                    self._audio_pos_s += _CHUNK_DURATION_S
                self._pending.append((raw, False))
            if dropped_now:
                self._reset_cache_before_next = True
                self._reset_reason = f"lag-drop({dropped_now})"
                logger.warning(
                    "FunASR realtime lag: dropped %d chunk(s) (total_dropped=%d); "
                    "will reset streaming cache before next decode",
                    dropped_now,
                    self._dropped_chunks,
                )

        if to_queue and self._wake is not None:
            self._wake.set()

    async def stop(self) -> None:
        with self._lock:
            sid = self._session_id
            self._running = False
            remainder = bytes(self._buffer) if self._buffer else b""
            self._buffer = bytearray()
            if remainder:
                self._pending.append((remainder, True))
            else:
                self._pending.append((b"", True))

        if self._wake is not None:
            self._wake.set()

        await self._stop_worker()

        logger.info(
            "Local FunASR realtime stopped session=%d "
            "(bytes_in=%d chunks=%d dropped=%d audio_s=%.2f)",
            sid,
            self._bytes_in,
            self._chunks_in,
            self._dropped_chunks,
            self._audio_pos_s,
        )
        with self._lock:
            if self._session_id == sid:
                self._cache = {}
                self._pending.clear()

    async def _stop_worker(self) -> None:
        task = self._worker_task
        self._worker_task = None
        if task is not None:
            if self._wake is not None:
                self._wake.set()
            try:
                await asyncio.wait_for(task, timeout=120.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
        self._wake = None

    async def _decode_worker(self, session_id: int) -> None:
        """Single consumer: decode pending chunks in order without blocking receive."""
        assert self._wake is not None
        loop = asyncio.get_running_loop()

        while True:
            with self._lock:
                alive = self._running and self._session_id == session_id
                item = self._pending.popleft() if self._pending else None
                pending_left = len(self._pending)

            if item is None:
                if not alive:
                    break
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                continue

            raw, is_last = item
            try:
                await self._process_chunk(
                    raw,
                    is_last=is_last,
                    session_id=session_id,
                    pending_left=pending_left,
                    loop=loop,
                )
            except Exception:
                logger.exception("decode worker chunk failed")

            if is_last:
                break

    @staticmethod
    def _pcm_s16le_to_float32(chunk: bytes) -> np.ndarray:
        if not chunk:
            return np.zeros(0, dtype=np.float32)
        n = len(chunk) - (len(chunk) % 2)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        pcm = np.frombuffer(chunk[:n], dtype=np.int16)
        return (pcm.astype(np.float32) / 32768.0).copy()

    @staticmethod
    def _soft_agc(waveform: np.ndarray) -> tuple[np.ndarray, float, float]:
        """Boost near-silent capture so Paraformer can hear the speech."""
        if waveform.size == 0:
            return waveform, 0.0, 1.0
        peak = float(np.max(np.abs(waveform)))
        if peak < 1e-6:
            return waveform, peak, 1.0
        if peak >= _AGC_MIN_PEAK:
            return waveform, peak, 1.0
        gain = min(_AGC_TARGET_PEAK / peak, _AGC_MAX_GAIN)
        out = np.clip(waveform * gain, -1.0, 1.0)
        return out, peak, gain

    def _fresh_cache(self) -> dict[str, Any]:
        """New streaming cache. Hotword is passed separately to generate()."""
        return {}

    def _run_generate(
        self,
        waveform: np.ndarray,
        *,
        is_final: bool,
        cache: dict[str, Any],
    ) -> Any:
        kwargs: dict[str, Any] = {
            "input": waveform,
            "cache": cache,
            "chunk_size": list(_CHUNK_SIZE),
            "is_final": is_final,
            "encoder_chunk_look_back": _ENCODER_CHUNK_LOOK_BACK,
            "decoder_chunk_look_back": _DECODER_CHUNK_LOOK_BACK,
        }
        # Prefer dedicated hotword arg; never stuff it into streaming cache
        # (cache holds encoder/decoder tensors — pollution desyncs the model).
        if self._hotword:
            kwargs["hotword"] = self._hotword
        with self._infer_lock:
            try:
                return self._model.generate(**kwargs)
            except TypeError:
                # Older FunASR builds may not accept hotword=
                kwargs.pop("hotword", None)
                return self._model.generate(**kwargs)

    @staticmethod
    def _extract_text(result: Any) -> str:
        if not result:
            return ""
        entry = result[0] if isinstance(result, list) else result
        if not isinstance(entry, dict):
            return ""
        return (entry.get("text") or "").strip()

    def _append_text(self, new_text: str, chunk_start_s: float) -> None:
        """Merge incremental FunASR text into the current partial segment."""
        if not new_text:
            return
        # Guard against cumulative re-emits
        if self._accumulated_text:
            acc = self._accumulated_text
            if new_text.startswith(acc):
                new_text = new_text[len(acc) :].strip()
            elif acc.endswith(new_text):
                new_text = ""
            elif new_text in acc:
                new_text = ""
        if not new_text:
            return

        if self._accumulated_text:
            prev_char = self._accumulated_text[-1]
            if self._is_cjk(prev_char) or self._is_cjk(new_text[0]):
                self._accumulated_text += new_text
            else:
                self._accumulated_text += " " + new_text
        else:
            self._accumulated_text = new_text
            self._sentence_start_s = chunk_start_s

    async def _close_utterance(
        self,
        end_s: float,
        *,
        reason: str,
        session_id: int,
        loop: asyncio.AbstractEventLoop,
        flush_model: bool = True,
    ) -> None:
        """Flush model (is_final), emit final segment, open a fresh streaming cache.

        This is the critical fix for "first two sentences OK, then garbage":
        Paraformer streaming cache must not grow unbounded across utterances.
        """
        if session_id != self._session_id:
            return

        if flush_model and self._cache:
            try:
                empty = np.zeros(0, dtype=np.float32)
                result = await loop.run_in_executor(
                    None,
                    lambda: self._run_generate(empty, is_final=True, cache=self._cache),
                )
                leftover = self._extract_text(result)
                if leftover:
                    self._append_text(leftover, end_s)
            except Exception:
                logger.exception(
                    "FunASR utterance flush failed (reason=%s)", reason
                )

        if self._accumulated_text:
            self._emit_segment(end_s, True)

        self._cache = self._fresh_cache()
        self._quiet_chunks = 0
        self._chunks_in_utterance = 0
        self._reset_cache_before_next = False
        self._reset_reason = ""
        logger.info(
            "FunASR utterance closed reason=%s audio_pos=%.1fs",
            reason,
            end_s,
        )

    async def _process_chunk(
        self,
        chunk: bytes,
        is_last: bool = False,
        session_id: int | None = None,
        pending_left: int = 0,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if session_id is not None and session_id != self._session_id:
            return
        loop = loop or asyncio.get_running_loop()

        waveform = self._pcm_s16le_to_float32(chunk)
        waveform, peak_raw, gain = self._soft_agc(waveform)

        # Lag-drop (or other flags): seal current utterance before continuing
        if self._reset_cache_before_next and not is_last:
            reason = self._reset_reason or "reset-flag"
            await self._close_utterance(
                self._audio_pos_s,
                reason=reason,
                session_id=session_id or self._session_id,
                loop=loop,
                flush_model=bool(self._cache),
            )

        chunk_start_s = self._audio_pos_s
        chunk_dur_s = (
            float(waveform.size) / _SAMPLE_RATE
            if waveform.size
            else (0.0 if is_last else _CHUNK_DURATION_S)
        )
        self._audio_pos_s += chunk_dur_s
        self._chunks_in += 1
        self._chunks_in_utterance += 1

        if waveform.size == 0 and not is_last:
            return

        cache = self._cache
        t0 = time.perf_counter()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._run_generate(
                    waveform, is_final=is_last, cache=cache
                ),
            )
        except Exception:
            logger.exception(
                "FunASR streaming generate failed (bytes=%d samples=%d is_final=%s)",
                len(chunk),
                int(waveform.size),
                is_last,
            )
            return

        if session_id is not None and session_id != self._session_id:
            return

        infer_ms = (time.perf_counter() - t0) * 1000
        new_text = self._extract_text(result)
        preview = (new_text or "")[:48]

        # Log first 8 chunks, then every 5th, plus finals / non-empty / lag
        if (
            self._chunks_in <= 8
            or is_last
            or new_text
            or self._chunks_in % 5 == 0
            or pending_left > 4
            or self._dropped_chunks > 0
        ):
            logger.info(
                "FunASR chunk#%d samples=%d peak=%.4f gain=%.1fx infer=%.0fms "
                "pending=%d dropped=%d utt_chunks=%d text=%r",
                self._chunks_in,
                int(waveform.size),
                peak_raw,
                gain,
                infer_ms,
                pending_left,
                self._dropped_chunks,
                self._chunks_in_utterance,
                preview,
            )

        if not self._on_segment:
            return

        # Rolling partial (official style): always same key, is_final=False
        # until a real energy-quiet boundary or session stop.
        if new_text:
            self._last_text_end_s = chunk_start_s + chunk_dur_s
            self._append_text(new_text, chunk_start_s)
            if self._accumulated_text:
                self._emit_segment(chunk_start_s + chunk_dur_s, False)

        # Energy VAD — never use empty ASR text as "silence"
        post_peak = (
            float(np.max(np.abs(waveform))) if waveform.size else 0.0
        )
        if post_peak < _QUIET_PEAK:
            self._quiet_chunks += 1
        else:
            self._quiet_chunks = 0

        end_s = self._last_text_end_s or (chunk_start_s + chunk_dur_s)

        # True quiet audio long enough → finalize one rolling segment + new cache
        if (
            not is_last
            and self._quiet_chunks >= _QUIET_CHUNKS_TO_END
            and (self._accumulated_text or self._cache)
        ):
            await self._close_utterance(
                end_s,
                reason=f"quiet({self._quiet_chunks},peak={post_peak:.4f})",
                session_id=session_id or self._session_id,
                loop=loop,
                flush_model=True,
            )
            return

        # Soft cap only — continuous speech without a quiet gap
        if (
            not is_last
            and self._chunks_in_utterance >= _MAX_UTTERANCE_CHUNKS
            and self._accumulated_text
        ):
            await self._close_utterance(
                end_s,
                reason=f"max-utt({self._chunks_in_utterance})",
                session_id=session_id or self._session_id,
                loop=loop,
                flush_model=True,
            )
            return

        if is_last:
            # This chunk was already decoded with is_final=True — do not flush again.
            if self._accumulated_text:
                self._emit_segment(chunk_start_s + chunk_dur_s, True)
            self._cache = self._fresh_cache()
            self._quiet_chunks = 0
            self._chunks_in_utterance = 0
            self._reset_cache_before_next = False
            self._reset_reason = ""
            logger.info(
                "FunASR utterance closed reason=session-stop audio_pos=%.1fs",
                chunk_start_s + chunk_dur_s,
            )

    def _emit_segment(self, end_s: float, is_final: bool) -> None:
        if not self._accumulated_text or not self._on_segment:
            return
        segment = TranscriptSegment(
            start=self._sentence_start_s,
            end=end_s,
            text=self._accumulated_text,
            speaker_id=None,
        )
        try:
            self._on_segment(segment, is_final, self._current_key)
        except Exception:
            logger.exception("on_segment callback failed")
        if is_final:
            self._accumulated_text = ""
            self._sentence_counter += 1
            self._current_key = f"local-{self._sentence_counter + 1}"

    @staticmethod
    def _is_cjk(char: str) -> bool:
        cp = ord(char)
        return (
            0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0x3000 <= cp <= 0x303F
            or 0xFF00 <= cp <= 0xFFEF
        )
