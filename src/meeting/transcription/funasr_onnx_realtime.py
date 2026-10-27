from __future__ import annotations

"""Local realtime ASR via funasr-onnx Paraformer-online (no torch at inference)."""

import asyncio
import logging
import threading
from typing import Any, Callable

import numpy as np

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptSegment
from src.meeting.transcription.base import RealtimeTranscriptionProvider
from src.meeting.transcription.onnx.paths import ensure_onnx_model_dir
from src.meeting.transcription.registry import realtime_transcription_registry

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "funasr/paraformer-zh-streaming"
# funasr-onnx online default is [5,10,5] (frames); keep 600ms middle
_CHUNK_SIZE = [5, 10, 5]
_SAMPLE_RATE = 16000
_BYTES_PER_SAMPLE = 2
_FRAME_MS = 60
_CHUNK_SAMPLES = _CHUNK_SIZE[1] * int(_SAMPLE_RATE * _FRAME_MS / 1000)  # 9600
_CHUNK_BYTES = _CHUNK_SAMPLES * _BYTES_PER_SAMPLE
_SILENCE_THRESHOLD = 2

# ── 共享模型缓存（2026-09-19 并发审查）───────────────────────────────
# ONNX 模型加载是秒级重活，但**模型对象本身是无状态的**（流式缓存在调用方
# 传入的 param_dict 里）。所以模型按 model_dir 全进程共享一份、推理串行；
# 而会话状态（buffer/cache/回调/running）全部留在实例上，**每个 WebSocket
# 连接新建一个适配器实例**——否则两台设备同时录制会互相踩碎对方的识别
# 状态（转写串台），这是缓存整个 provider 实例时的真实事故路径。
_shared_models: dict[str, tuple[Any, threading.Lock]] = {}
_shared_models_guard = threading.Lock()


def _get_shared_model(model_dir, quantize: bool, threads: int) -> tuple[Any, threading.Lock]:
    """Load (or fetch cached) the ONNX Paraformer-online model for *model_dir*.

    Returns ``(model, infer_lock)``。infer_lock 跨全部会话共享：ONNX 会话
    不是线程安全的，推理必须串行。
    """
    key = str(model_dir)
    with _shared_models_guard:
        entry = _shared_models.get(key)
        if entry is not None:
            return entry

        from funasr_onnx.paraformer_online_bin import Paraformer as ParaformerOnline

        model = ParaformerOnline(
            str(model_dir),
            chunk_size=list(_CHUNK_SIZE),
            quantize=quantize,
            device_id="-1",
            intra_op_num_threads=threads,
        )
        entry = (model, threading.Lock())
        _shared_models[key] = entry
        return entry


@realtime_transcription_registry.register(
    "funasr_onnx_realtime",
    display_name="FunASR ONNX (local, realtime)",
)
class FunASROnnxRealtimeTranscription(RealtimeTranscriptionProvider):
    """Paraformer-online ONNX streaming — continuous cache, no drop, baseline UX."""

    supports_hot_words = False
    SUPPORTED_LANGUAGE_HINTS = [
        {"code": "auto", "label": "Auto"},
        {"code": "zh", "label": "Chinese"},
        {"code": "en", "label": "English"},
    ]

    def __init__(self, config: TranscriptionProviderConfig):
        self._model_name = config.model or _DEFAULT_MODEL
        quantize = True
        device_id = "-1"
        if config.device and config.device not in ("auto", "cpu"):
            # funasr-onnx uses device_id as GPU index string; leave CPU for safety
            logger.info("ONNX realtime forcing CPU (device=%s ignored)", config.device)

        model_dir = ensure_onnx_model_dir(
            self._model_name,
            streaming=True,
            quantize=quantize,
            label="realtime-paraformer",
        )

        from src.meeting.transcription.onnx.threads import (
            configure_host_math_threads,
            realtime_asr_threads,
        )

        configure_host_math_threads()
        threads = realtime_asr_threads()
        # 模型共享（首个连接承担加载），推理锁跨会话共享；下方全部 `_xxx`
        # 状态都是**本连接私有**的会话状态。
        self._model, self._infer_lock = _get_shared_model(model_dir, quantize, threads)
        logger.info(
            "FunASR ONNX realtime session bound to model %s threads=%s (shared model)",
            model_dir,
            threads,
        )

        self._on_segment: Callable | None = None
        self._param_dict: dict[str, Any] = {"cache": {}, "is_final": False}
        self._buffer = bytearray()
        self._audio_pos_s = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._sentence_counter = 0
        self._accumulated_text = ""
        self._current_key = "local-1"
        self._sentence_start_s = 0.0
        self._silence_chunks = 0
        self._last_text_end_s = 0.0
        # _infer_lock 不在这里初始化：它来自 _get_shared_model（跨会话共享），
        # 在这里再 Lock() 会把共享锁覆盖成私有锁，推理串行化就失效了。

    async def start(
        self,
        on_segment: Callable[[TranscriptSegment, bool, Any], None],
        hot_words: list | None = None,
        language_hints: list[str] | None = None,
        translation_target: str | None = None,
    ) -> None:
        # translation_target is ignored: local ASR has no translation mode.
        self._on_segment = on_segment
        self._param_dict = {"cache": {}, "is_final": False}
        self._buffer = bytearray()
        self._audio_pos_s = 0.0
        self._accumulated_text = ""
        self._sentence_start_s = 0.0
        self._silence_chunks = 0
        self._last_text_end_s = 0.0
        self._running = True
        if hot_words:
            logger.info("ONNX realtime ignores hot words (%d provided)", len(hot_words))
        logger.info(
            "FunASR ONNX realtime started (chunk=600ms, continuous cache, no-drop)"
        )

    async def send_frame(self, audio_data: bytes) -> None:
        if not self._running:
            raise RuntimeError("Transcription session not started. Call start() first.")

        to_process: list[bytes] = []
        with self._lock:
            self._buffer.extend(audio_data)
            while len(self._buffer) >= _CHUNK_BYTES:
                to_process.append(bytes(self._buffer[:_CHUNK_BYTES]))
                del self._buffer[:_CHUNK_BYTES]

        for chunk in to_process:
            await self._process_chunk(chunk, is_last=False)

    async def stop(self) -> None:
        self._running = False
        remainder = b""
        with self._lock:
            if self._buffer:
                remainder = bytes(self._buffer)
                self._buffer = bytearray()
        if remainder:
            await self._process_chunk(remainder, is_last=True)
        else:
            await self._process_chunk(b"", is_last=True)
        self._param_dict = {"cache": {}, "is_final": False}
        logger.info("FunASR ONNX realtime stopped")

    @staticmethod
    def _pcm_to_float32(chunk: bytes) -> np.ndarray:
        if not chunk:
            return np.zeros(0, dtype=np.float32)
        n = len(chunk) - (len(chunk) % 2)
        pcm = np.frombuffer(chunk[:n], dtype=np.int16)
        return (pcm.astype(np.float32) / 32768.0).copy()

    @staticmethod
    def _extract_preds(result: Any) -> str:
        if not result:
            return ""
        # online returns [{"preds": "text" or (text, tokens)}]
        item = result[0] if isinstance(result, list) else result
        if not isinstance(item, dict):
            return str(item).strip()
        preds = item.get("preds")
        if preds is None:
            return (item.get("text") or "").strip()
        if isinstance(preds, (list, tuple)):
            # sentence_postprocess may return (text, token_list)
            if preds and isinstance(preds[0], str) and len(preds) >= 1:
                # could be tokens list of strings
                if all(isinstance(p, str) and len(p) <= 4 for p in preds[:5]):
                    return "".join(preds)
                return str(preds[0])
            return "".join(str(p) for p in preds)
        return str(preds).strip()

    async def _process_chunk(self, chunk: bytes, is_last: bool = False) -> None:
        loop = asyncio.get_running_loop()
        chunk_start_s = self._audio_pos_s
        chunk_dur_s = (
            len(chunk) / (_SAMPLE_RATE * _BYTES_PER_SAMPLE) if chunk else 0.0
        )
        self._audio_pos_s += chunk_dur_s

        waveform = self._pcm_to_float32(chunk)
        param = self._param_dict
        param["is_final"] = is_last

        def _run() -> Any:
            with self._infer_lock:
                # empty final flush
                audio = waveform if waveform.size else np.zeros(0, dtype=np.float32)
                return self._model(audio, param_dict=param)

        try:
            result = await loop.run_in_executor(None, _run)
        except Exception:
            logger.exception("ONNX streaming generate failed is_final=%s", is_last)
            return

        new_text = self._extract_preds(result)
        if new_text:
            self._silence_chunks = 0
            self._last_text_end_s = chunk_start_s + chunk_dur_s
            self._append_preds(new_text, chunk_start_s)
            self._emit_segment(chunk_start_s + chunk_dur_s, False)
        else:
            self._silence_chunks += 1
            if (
                self._silence_chunks >= _SILENCE_THRESHOLD
                and self._accumulated_text
            ):
                await self._flush_sentence(self._last_text_end_s)

        if is_last and self._accumulated_text:
            await self._flush_sentence(chunk_start_s + chunk_dur_s)

    def _append_preds(self, new_text: str, chunk_start_s: float) -> None:
        if self._accumulated_text:
            prev = self._accumulated_text[-1]
            if self._is_cjk(prev) or self._is_cjk(new_text[0]):
                self._accumulated_text += new_text
            else:
                self._accumulated_text += " " + new_text
        else:
            self._accumulated_text = new_text
            self._sentence_start_s = chunk_start_s

    async def _flush_sentence(self, end_s: float) -> None:
        """Emit a finished sentence and drop streaming look-ahead so the next
        utterance does not start with the previous syllable.
        """
        extra = ""
        param = self._param_dict
        param["is_final"] = True
        loop = asyncio.get_running_loop()

        def _run() -> Any:
            with self._infer_lock:
                return self._model(np.zeros(0, dtype=np.float32), param_dict=param)

        try:
            result = await loop.run_in_executor(None, _run)
            extra = self._extract_preds(result)
        except Exception:
            logger.exception("ONNX sentence flush failed")
        if extra:
            self._append_preds(extra, self._sentence_start_s)
        self._emit_segment(end_s, True)
        self._param_dict = {"cache": {}, "is_final": False}
        self._silence_chunks = 0

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
        return 0x4E00 <= cp <= 0x9FFF
