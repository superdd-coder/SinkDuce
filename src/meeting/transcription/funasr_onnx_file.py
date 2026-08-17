from __future__ import annotations

"""Local file transcription: VAD + SenseVoice + CT-punc + CAM++ (all ONNX)."""

import asyncio
import logging
from typing import Any

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptionResult
from src.meeting.transcription.base import FileTranscriptionProvider
from src.meeting.transcription.onnx.pipeline import FunAsrOnnxFilePipeline
from src.meeting.transcription.registry import file_transcription_registry

logger = logging.getLogger(__name__)

_DEFAULT_ASR = "FunAudioLLM/SenseVoiceSmall"
_DEFAULT_VAD = "funasr/fsmn-vad"
_DEFAULT_PUNC = "funasr/ct-punc"
_DEFAULT_SPK = "funasr/campplus"


@file_transcription_registry.register(
    "funasr_onnx",
    display_name="FunASR ONNX (SenseVoice int8 + VAD + punc + spk)",
)
class FunASROnnxFileTranscription(FileTranscriptionProvider):
    """ORT pipeline aligned with FunASR AutoModel composition.

    SenseVoice ASR always uses 8-bit ``model_quant.onnx``.
    """

    supports_hot_words = False
    SUPPORTED_LANGUAGE_HINTS = [
        {"code": "auto", "label": "Auto"},
        {"code": "zh", "label": "Chinese"},
        {"code": "en", "label": "English"},
        {"code": "ja", "label": "Japanese"},
        {"code": "ko", "label": "Korean"},
        {"code": "yue", "label": "Cantonese"},
    ]

    def __init__(self, config: TranscriptionProviderConfig):
        asr = config.model or _DEFAULT_ASR
        vad = config.vad_model or _DEFAULT_VAD
        punc = config.punc_model if config.punc_model is not None else _DEFAULT_PUNC
        spk = config.spk_model if config.spk_model is not None else _DEFAULT_SPK
        device_id = "-1"
        logger.info(
            "Loading FunASR ONNX file pipeline asr=%s vad=%s punc=%s spk=%s",
            asr,
            vad,
            punc,
            spk,
        )
        self._pipeline = FunAsrOnnxFilePipeline(
            asr_repo=asr,
            vad_repo=vad,
            punc_repo=punc or None,
            spk_repo=spk or None,
            quantize=True,
            asr_quantize=True,  # SenseVoice int8 only
            num_threads=4,
            device_id=device_id,
        )
        logger.info("FunASR ONNX file pipeline ready (SenseVoice int8)")

    async def transcribe(
        self,
        file_path: str,
        language_hints: list[str] | None = None,
        hot_words: list | None = None,
    ) -> TranscriptionResult:
        language = "auto"
        if language_hints:
            # SenseVoice tags: zh/en/ja/ko/yue/auto
            code = (language_hints[0] or "auto").lower()
            language = {"chinese": "zh", "english": "en", "japanese": "ja", "korean": "ko"}.get(
                code, code
            )
        if hot_words:
            logger.info("ONNX file ASR ignores hot words (%d)", len(hot_words))

        return await asyncio.to_thread(
            self._pipeline.transcribe, file_path, language=language
        )
