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
# Add new adapter imports here so the registry sees them.
from src.meeting.transcription import dashscope_file  # noqa: F401
from src.meeting.transcription import dashscope_realtime  # noqa: F401
from src.meeting.transcription import funasr_local  # noqa: F401
from src.meeting.transcription import funasr_local_realtime  # noqa: F401
from src.meeting.transcription import openai_compat_file  # noqa: F401
from src.meeting.transcription import openrouter_file  # noqa: F401



def create_file_transcription_provider(
    config: TranscriptionProviderConfig,
) -> FileTranscriptionProvider | None:
    """Create a file-based transcription provider from config.

    Returns None if no adapter is configured. Raises ValueError if the
    adapter name is unknown (typo / uninstalled plugin).
    """
    if not config.adapter or config.adapter == "none":
        return None
    return file_transcription_registry.create(config.adapter, config)


def create_realtime_transcription_provider(
    config: TranscriptionProviderConfig,
) -> RealtimeTranscriptionProvider | None:
    """Create a real-time transcription provider from config.

    Returns None if no adapter is configured. Raises ValueError if the
    adapter name is unknown (typo / uninstalled plugin).
    """
    if not config.adapter or config.adapter == "none":
        return None
    return realtime_transcription_registry.create(config.adapter, config)
