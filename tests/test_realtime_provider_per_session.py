"""Cloud realtime providers must NOT be shared across WS sessions.

The realtime provider cache returns the same adapter instance to every
websocket connection. Two overlapping handlers (graceful reconnect,
page refresh, engine swap) then race on the instance's single
``self._recognition`` slot: the old handler's ``stop()`` can land after
the new handler's ``start()`` and kill the fresh session — observed as
"Transcription session not started. Call start() first." followed by a
reconnect storm.

Cloud adapters are cheap to construct (no model weights), so each WS
connection must get its own instance. The heavyweight local ONNX
adapter keeps the cache (model loading is expensive).

Run: pytest tests/test_realtime_provider_per_session.py -v --tb=short
"""

from __future__ import annotations

from types import SimpleNamespace

from src.config import TranscriptionConfig, TranscriptionProviderConfig

LT_ADAPTER = "dashscope_livetranslate_realtime"


def _asr(pid: str, active: bool = False) -> TranscriptionProviderConfig:
    return TranscriptionProviderConfig(
        id=pid, name=pid, adapter="dashscope_funasr_realtime",
        api_key="sk-asr", is_active=active,
    )


def _lt(pid: str, active: bool = False) -> TranscriptionProviderConfig:
    return TranscriptionProviderConfig(
        id=pid, name=pid, adapter=LT_ADAPTER,
        api_key="sk-lt", is_active=active,
    )


def _svc_with(monkeypatch, providers):
    from src.meeting.service import MeetingService

    # This box may not have the dashscope package; the adapters' __init__ is
    # cheap and offline apart from the _require_dashscope() guard.
    import src.meeting.transcription.dashscope_livetranslate_realtime as _lt_mod
    import src.meeting.transcription.dashscope_realtime as _asr_mod

    monkeypatch.setattr(_asr_mod, "_HAS_DASHSCOPE", True)
    monkeypatch.setattr(_lt_mod, "_HAS_DASHSCOPE", True)

    cfg = SimpleNamespace(
        transcription=TranscriptionConfig(realtime_providers=providers)
    )
    monkeypatch.setattr("src.meeting.service.get_config", lambda: cfg)
    return MeetingService()


def test_cloud_asr_provider_fresh_per_call(monkeypatch):
    svc = _svc_with(monkeypatch, [_asr("asr-1", active=True)])
    p1 = svc.get_active_realtime_provider()
    p2 = svc.get_active_realtime_provider()
    assert p1 is not None and p2 is not None
    assert p1 is not p2, (
        "cloud ASR provider instance is shared across WS sessions — "
        "overlapping handlers race on one recognition slot"
    )


def test_livetranslate_provider_fresh_per_call(monkeypatch):
    svc = _svc_with(monkeypatch, [_lt("lt-1", active=True)])
    p1 = svc.get_realtime_translation_provider()
    p2 = svc.get_realtime_translation_provider()
    assert p1 is not None and p2 is not None
    assert p1 is not p2, (
        "LiveTranslate provider instance is shared across WS sessions"
    )
