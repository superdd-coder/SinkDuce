"""Realtime provider must ALWAYS free its cloud vocabulary table
(DashScope caps accounts at 10 tables — leaked tables block all hot
words with Throttling.AllocationQuota).

Run: pytest tests/test_dashscope_vocab_cleanup.py -v --tb=short
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


def _make_provider(monkeypatch, fake_vocab_service_cls):
    import src.meeting.transcription.dashscope_realtime as mod
    from src.config import TranscriptionProviderConfig

    monkeypatch.setattr(mod, "_require_dashscope", lambda: None)
    monkeypatch.setattr(mod, "VocabularyService", fake_vocab_service_cls, raising=False)
    cfg = TranscriptionProviderConfig(
        id="x", name="x", adapter="dashscope_realtime", api_key="k", model="fun-asr-realtime"
    )
    return mod.DashScopeRealtimeTranscription(cfg)


class _RecordingVocabService:
    deleted: list[str] = []

    def __init__(self, api_key=None):
        pass

    def delete_vocabulary(self, vocab_id):
        _RecordingVocabService.deleted.append(vocab_id)


class _BrokenRecognition:
    def stop(self):
        raise RuntimeError("already closed")


class _OkRecognition:
    stopped = False

    def stop(self):
        _OkRecognition.stopped = True


def _run_stop(provider) -> None:
    asyncio.run(provider.stop())
    # asyncio.run() clears the thread's current loop on py3.10; restore one so
    # later test modules relying on implicit get_event_loop() still work.
    asyncio.set_event_loop(asyncio.new_event_loop())


def test_stop_deletes_vocabulary_even_when_recognition_stop_raises(monkeypatch):
    _RecordingVocabService.deleted.clear()
    provider = _make_provider(monkeypatch, _RecordingVocabService)
    provider._recognition = _BrokenRecognition()
    provider._vocab_id = "vocab-a"
    _run_stop(provider)  # must not raise
    assert _RecordingVocabService.deleted == ["vocab-a"]


def test_stop_deletes_vocabulary_on_happy_path(monkeypatch):
    _RecordingVocabService.deleted.clear()
    provider = _make_provider(monkeypatch, _RecordingVocabService)
    provider._recognition = _OkRecognition()
    provider._vocab_id = "vocab-b"
    _run_stop(provider)
    assert _OkRecognition.stopped is True
    assert _RecordingVocabService.deleted == ["vocab-b"]
    assert provider._recognition is None


def test_stop_without_recognition_still_deletes_vocabulary(monkeypatch):
    # Covers: vocabulary created, then recognition.start() failed hard
    _RecordingVocabService.deleted.clear()
    provider = _make_provider(monkeypatch, _RecordingVocabService)
    provider._recognition = None
    provider._vocab_id = "vocab-c"
    _run_stop(provider)
    assert _RecordingVocabService.deleted == ["vocab-c"]
