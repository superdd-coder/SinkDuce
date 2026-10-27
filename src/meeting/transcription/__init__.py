from __future__ import annotations

from src.config import TranscriptionProviderConfig
from src.meeting.transcription.base import (
    FileTranscriptionProvider,
    RealtimeTranscriptionProvider,
)
from src.meeting.transcription.registry import (
    file_transcription_registry,
    realtime_transcription_registry,
)

# Importing adapter modules triggers their @register decorators.
# Local ASR is ONNX-only (funasr-onnx + onnxruntime). No PyTorch FunASR adapters.
from src.meeting.transcription import dashscope_file  # noqa: F401
from src.meeting.transcription import dashscope_livetranslate_realtime  # noqa: F401
from src.meeting.transcription import dashscope_realtime  # noqa: F401
from src.meeting.transcription import funasr_onnx_file  # noqa: F401
from src.meeting.transcription import funasr_onnx_realtime  # noqa: F401
from src.meeting.transcription import openai_compat_file  # noqa: F401
from src.meeting.transcription import openrouter_file  # noqa: F401

# One-release compat: old config.yaml used pytorch adapter names.
_FILE_ADAPTER_ALIASES = {
    "funasr_local": "funasr_onnx",
}
_REALTIME_ADAPTER_ALIASES = {
    "funasr_local_realtime": "funasr_onnx_realtime",
}


def resolve_file_adapter(adapter: str) -> str:
    """Normalize file adapter name (legacy pytorch → ONNX)."""
    if not adapter or adapter == "none":
        return adapter
    return _FILE_ADAPTER_ALIASES.get(adapter, adapter)


def resolve_realtime_adapter(adapter: str) -> str:
    """Normalize realtime adapter name (legacy pytorch → ONNX)."""
    if not adapter or adapter == "none":
        return adapter
    return _REALTIME_ADAPTER_ALIASES.get(adapter, adapter)


def is_local_file_adapter(adapter: str) -> bool:
    """True if this is the local ONNX file ASR adapter (SenseVoice pack)."""
    return resolve_file_adapter(adapter or "") == "funasr_onnx"


def is_local_realtime_adapter(adapter: str) -> bool:
    """True if this is the local ONNX streaming ASR adapter."""
    return resolve_realtime_adapter(adapter or "") == "funasr_onnx_realtime"


def is_local_asr_adapter(adapter: str) -> bool:
    """True for any local FunASR ONNX adapter (file or realtime)."""
    return is_local_file_adapter(adapter) or is_local_realtime_adapter(adapter)


def create_file_transcription_provider(
    config: TranscriptionProviderConfig,
) -> FileTranscriptionProvider | None:
    """Create a file-based transcription provider from config.

    Returns None if no adapter is configured. Raises ValueError if the
    adapter name is unknown (typo / uninstalled plugin).
    """
    if not config.adapter or config.adapter == "none":
        return None
    adapter = resolve_file_adapter(config.adapter)
    if adapter != config.adapter:
        config = config.model_copy(update={"adapter": adapter})
    return file_transcription_registry.create(adapter, config)


def create_realtime_transcription_provider(
    config: TranscriptionProviderConfig,
) -> RealtimeTranscriptionProvider | None:
    """Create a real-time transcription provider from config.

    Returns None if no adapter is configured. Raises ValueError if the
    adapter name is unknown (typo / uninstalled plugin).
    """
    if not config.adapter or config.adapter == "none":
        return None
    adapter = resolve_realtime_adapter(config.adapter)
    if adapter != config.adapter:
        config = config.model_copy(update={"adapter": adapter})
    return realtime_transcription_registry.create(adapter, config)
