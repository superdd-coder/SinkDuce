"""Provider registries for the transcription module.

There are two distinct interface types here, so two separate registries:

- ``file_transcription_registry`` — for batch, file-based ASR (read a file,
  return a complete ``TranscriptionResult``). ABC: ``FileTranscriptionProvider``.
- ``realtime_transcription_registry`` — for streaming, WebSocket-style ASR
  (push audio frames in, get partial segments out). ABC:
  ``RealtimeTranscriptionProvider``.

A given backend (e.g. DashScope) can register a class in either or both,
depending on whether it supports the corresponding mode. Adapters self-register
via ``@file_transcription_registry.register(...)`` / ``@realtime_transcription_registry.register(...)``;
the factories in ``transcription/__init__.py`` look them up by name.

See ``docs/MEETING_PROVIDER_SPEC.md`` for the full adapter authoring contract.
"""

from __future__ import annotations

from typing import Any

from src.providers.registry import ProviderEntry, ProviderRegistry

file_transcription_registry = ProviderRegistry("file_transcription")
realtime_transcription_registry = ProviderRegistry("realtime_transcription")


def cls_supports_hot_words(cls: type) -> bool:
    """Read adapter class flag ``supports_hot_words`` (bool class attr)."""
    raw = getattr(cls, "supports_hot_words", False)
    if isinstance(raw, bool):
        return raw
    # Legacy @property / method on base — treat as unsupported unless bool attr
    return False


def entry_public_dict(entry: ProviderEntry) -> dict[str, Any]:
    """Serialize a transcription registry entry for the frontend."""
    return {
        "name": entry.name,
        "display_name": entry.display_name,
        "supports_hot_words": cls_supports_hot_words(entry.cls),
    }
