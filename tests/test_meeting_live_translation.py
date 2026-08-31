"""Realtime bilingual captions via DashScope LiveTranslate.

Covers the full backend chain for per-meeting live translation:
  1. TranscriptSegment.translation field
  2. LiveTranslateEventReducer — pairs source transcription and translation
     by item_id / previous_item_id (the "双语字幕能对齐吗" contract)
  3. dashscope_livetranslate_realtime adapter wiring (fake SDK classes)
  4. RealtimeTranscriptionProvider.start() accepts translation_target
  5. meeting_service realtime-translation provider resolution
  6. /realtime-transcribe?translation_target=… WS flow
  7. active-provider-info capability flag

Run: pytest tests/test_meeting_live_translation.py -v --tb=short
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ════════════════════════ 1. models ════════════════════════


def test_transcript_segment_translation_field():
    from src.meeting.models import TranscriptSegment

    seg = TranscriptSegment(start=0.0, end=1.0, text="hello", translation="你好")
    assert seg.translation == "你好"
    # Optional — plain ASR segments keep working unchanged.
    assert TranscriptSegment(start=0.0, end=1.0, text="hi").translation is None


# ════════════════════════ 2. reducer ════════════════════════


class _Collector:
    def __init__(self):
        self.events: list[tuple] = []

    def __call__(self, segment, is_final, key):
        self.events.append((segment, is_final, key))

    def last(self):
        assert self.events, "no on_segment emission"
        return self.events[-1]


def _reducer():
    from src.meeting.transcription.livetranslate_events import (
        LiveTranslateEventReducer,
    )

    out = _Collector()
    return LiveTranslateEventReducer(out), out


def _feed(reducer, *events):
    for ev in events:
        reducer.handle(ev if isinstance(ev, dict) else json.loads(ev))


def _turn1_events():
    """One VAD turn: ASR item `asr_1`, translation output item `resp_1`."""
    return [
        {"type": "input_audio_buffer.speech_started", "audio_start_ms": 1000},
        {
            "type": "conversation.item.created",
            "previous_item_id": None,
            "item": {"id": "asr_1", "type": "message"},
        },
        {
            "type": "conversation.item.created",
            "previous_item_id": "asr_1",
            "item": {"id": "resp_1", "type": "message"},
        },
    ]


def test_reducer_pairs_source_and_translation_by_item_id():
    reducer, out = _reducer()
    _feed(
        reducer,
        *_turn1_events(),
        # streaming source partial: confirmed "hel" + tentative "lo"
        {
            "type": "conversation.item.input_audio_transcription.text",
            "item_id": "asr_1",
            "text": "hel",
            "stash": "lo",
        },
        {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 3000},
        # source finalized
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "asr_1",
            "transcript": "hello",
        },
        # streaming translation partial (confirmed 你 + tentative 好)
        {"type": "response.text.text", "item_id": "resp_1", "text": "你", "stash": "好"},
        {"type": "response.text.done", "item_id": "resp_1", "text": "你好"},
    )

    # partial source: text+stash concatenated, not final, no translation yet
    seg, is_final, key = out.events[0]
    assert (seg.text, is_final, key) == ("hello", False, "asr_1")
    assert seg.translation is None
    assert seg.start == pytest.approx(1.0)

    # translation partial reuses the SAME key (bilingual block updates in place)
    seg, is_final, key = out.events[-2]
    assert key == "asr_1"
    assert seg.translation == "你好"
    assert is_final is False

    # final emission: source + translation locked together
    seg, is_final, key = out.events[-1]
    assert (seg.text, seg.translation, is_final, key) == (
        "hello",
        "你好",
        True,
        "asr_1",
    )
    assert seg.start == pytest.approx(1.0)
    assert seg.end == pytest.approx(3.0)
    assert seg.speaker_id is None


def test_reducer_translation_before_source_completed_stays_partial():
    """SI means translation may finish while ASR is still streaming."""
    reducer, out = _reducer()
    _feed(
        reducer,
        *_turn1_events(),
        {"type": "response.text.text", "item_id": "resp_1", "text": "你", "stash": "好"},
        {"type": "response.text.done", "item_id": "resp_1", "text": "你好"},
    )
    seg, is_final, key = out.events[-1]
    assert (key, is_final) == ("asr_1", False)

    _feed(
        reducer,
        {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 2500},
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "asr_1",
            "transcript": "hello",
        },
    )
    seg, is_final, key = out.events[-1]
    assert (seg.text, seg.translation, is_final) == ("hello", "你好", True)


def test_reducer_separate_keys_per_turn():
    reducer, out = _reducer()
    _feed(
        reducer,
        *_turn1_events(),
        {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 3000},
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "asr_1",
            "transcript": "hello",
        },
        {"type": "response.text.done", "item_id": "resp_1", "text": "你好"},
        # ── second turn ──
        {"type": "input_audio_buffer.speech_started", "audio_start_ms": 5000},
        {
            "type": "conversation.item.created",
            "previous_item_id": "asr_1",
            "item": {"id": "asr_2"},
        },
        {
            "type": "conversation.item.created",
            "previous_item_id": "asr_2",
            "item": {"id": "resp_2"},
        },
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "asr_2",
            "transcript": "world",
        },
        {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 6500},
        {"type": "response.text.done", "item_id": "resp_2", "text": "世界"},
    )
    seg, is_final, key = out.events[-1]
    assert (seg.text, seg.translation, is_final, key) == ("world", "世界", True, "asr_2")
    assert seg.start == pytest.approx(5.0)
    assert seg.end == pytest.approx(6.5)
    # first turn untouched
    seg1 = next(s for s, f, k in out.events if k == "asr_1" and f)
    assert seg1.text == "hello"


def test_reducer_ignores_unrelated_events():
    reducer, out = _reducer()
    _feed(
        reducer,
        {"type": "session.created", "session_id": "s"},
        {"type": "response.created", "response": {"id": "r"}},
        {"type": "input_audio_buffer.speech_started"},  # no audio_start_ms
        {"type": "conversation.item.input_audio_transcription.failed", "item_id": "x"},
    )
    assert out.events == []


# ════════════════════════ 3. adapter ════════════════════════


class _FakeModality:
    TEXT = "text"
    AUDIO = "audio"


class _FakeTranslationParams:
    def __init__(self, language=None, corpus=None):
        self.language = language
        self.corpus = corpus


class _FakeConversation:
    def __init__(self, model=None, callback=None, url=None, api_key=None, workspace=None, **kw):
        self.model = model
        self.callback = callback
        self.url = url
        self.api_key = api_key
        self.workspace = workspace
        self.update_kwargs = None
        self.appended: list[str] = []
        self.ended = False
        self.closed = False

    def connect(self):
        pass

    def update_session(self, **kwargs):
        self.update_kwargs = kwargs

    def append_audio(self, audio_b64):
        self.appended.append(audio_b64)

    def end_session(self, timeout=20):
        self.ended = True

    def close(self):
        self.closed = True


def _make_adapter(monkeypatch) -> tuple:
    import src.meeting.transcription.dashscope_livetranslate_realtime as mod
    from src.config import TranscriptionProviderConfig

    monkeypatch.setattr(mod, "_require_dashscope", lambda: None)
    monkeypatch.setattr(mod, "OmniRealtimeConversation", _FakeConversation, raising=False)
    monkeypatch.setattr(mod, "MultiModality", _FakeModality, raising=False)
    monkeypatch.setattr(mod, "TranslationParams", _FakeTranslationParams, raising=False)
    cfg = TranscriptionProviderConfig(
        id="lt-test",
        name="LT",
        adapter="dashscope_livetranslate_realtime",
        api_key="sk-test",
    )
    return mod.DashScopeLiveTranslateRealtime(cfg), mod


def test_livetranslate_adapter_registered():
    from src.meeting.transcription.registry import realtime_transcription_registry

    entry = realtime_transcription_registry.get("dashscope_livetranslate_realtime")
    assert entry is not None
    assert entry.cls.supports_hot_words is False


def test_adapter_start_wires_session_and_events(monkeypatch):
    import asyncio

    adapter, mod = _make_adapter(monkeypatch)
    assert mod._DEFAULT_LIVE_TRANSLATE_MODEL in str(adapter._model) or adapter._model

    out = _Collector()
    asyncio.run(
        adapter.start(out, hot_words=None, language_hints=None, translation_target="zh")
    )
    conv = _FakeConversation.instances[-1] if hasattr(_FakeConversation, "instances") else None
    # adapter exposes its conversation for send_frame/stop
    conv = adapter._conversation
    assert conv.api_key == "sk-test"
    kw = conv.update_kwargs
    assert kw["output_modalities"] == [_FakeModality.TEXT]
    assert kw["enable_input_audio_transcription"] is True
    assert kw["input_audio_transcription_model"]
    assert kw["translation_params"].language == "zh"
    # The server rejects voice=null on the first speech-triggered response
    # ("Voice 'null' is not supported") even in text-only mode.
    assert kw["voice"] == "Tina"

    # SDK callback (JSON string) → reducer → on_segment
    conv.callback.on_event(
        json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.text",
                "item_id": "asr_9",
                "text": "hi",
                "stash": "",
            }
        )
    )
    seg, is_final, key = out.last()
    assert (seg.text, is_final, key) == ("hi", False, "asr_9")


def test_adapter_send_frame_and_stop(monkeypatch):
    import asyncio
    import base64

    adapter, _ = _make_adapter(monkeypatch)
    out = _Collector()
    asyncio.run(adapter.start(out))

    asyncio.run(adapter.send_frame(b"\x01\x02"))
    conv = adapter._conversation
    assert conv.appended == [base64.b64encode(b"\x01\x02").decode()]

    asyncio.run(adapter.stop())
    assert conv.ended is True
    assert conv.closed is True
    assert adapter._conversation is None


def test_adapter_default_model_is_qwen35():
    import src.meeting.transcription.dashscope_livetranslate_realtime as mod

    assert "qwen3.5-livetranslate" in mod._DEFAULT_LIVE_TRANSLATE_MODEL


# ════════════════════════ 4. ABC signature ════════════════════════


def test_realtime_start_signature_accepts_translation_target():
    import inspect

    from src.meeting.transcription.base import RealtimeTranscriptionProvider
    from src.meeting.transcription.dashscope_realtime import (
        DashScopeRealtimeTranscription,
    )
    from src.meeting.transcription.dashscope_livetranslate_realtime import (
        DashScopeLiveTranslateRealtime,
    )

    for cls in (
        RealtimeTranscriptionProvider,
        DashScopeRealtimeTranscription,
        DashScopeLiveTranslateRealtime,
    ):
        sig = inspect.signature(cls.start)
        assert "translation_target" in sig.parameters, cls.__name__
        assert sig.parameters["translation_target"].default is None


# ════════════════════════ 5. service resolution ════════════════════════


def _svc():
    from src.meeting import service as service_mod

    return service_mod.meeting_service


def _patch_config(monkeypatch, realtime_providers, active):
    from src.config import TranscriptionProviderConfig, TranscriptionConfig

    cfg = SimpleNamespace(
        transcription=SimpleNamespace(
            realtime_providers=realtime_providers,
            active_realtime_provider=active,
        )
    )
    import src.meeting.service as service_mod

    monkeypatch.setattr(service_mod, "get_config", lambda: cfg)
    return cfg


def _no_dashscope_required(monkeypatch):
    import src.meeting.transcription.dashscope_livetranslate_realtime as mod

    monkeypatch.setattr(mod, "_require_dashscope", lambda: None)


def test_service_translation_provider_synthesized_from_dashscope_key(monkeypatch):
    from src.config import TranscriptionProviderConfig

    _no_dashscope_required(monkeypatch)
    dash = TranscriptionProviderConfig(
        id="dash-rt",
        name="Dash",
        adapter="dashscope_funasr_realtime",
        api_key="sk-live",
        is_active=True,
    )
    _patch_config(monkeypatch, [dash], dash)

    svc = _svc()
    provider = svc.get_realtime_translation_provider()
    assert type(provider).__name__ == "DashScopeLiveTranslateRealtime"
    assert provider._api_key == "sk-live"

    meta = svc.get_realtime_translation_provider_meta()
    assert meta["adapter"] == "dashscope_livetranslate_realtime"
    assert meta["supports_realtime_translation"] is True


def test_service_translation_provider_cache_drops_on_model_change(monkeypatch):
    """Editing the LiveTranslate provider (e.g. model) must not serve a stale
    cached instance — the config PUT invalidates `rt_trans:<id>`, not the
    translation cache key, so the service guards staleness itself."""
    from src.config import TranscriptionProviderConfig

    _no_dashscope_required(monkeypatch)
    explicit = TranscriptionProviderConfig(
        id="lt-stale",
        name="LT",
        adapter="dashscope_livetranslate_realtime",
        api_key="sk-same",
        is_active=True,
    )
    holder = {"cfg": explicit}

    import src.meeting.service as service_mod

    monkeypatch.setattr(
        service_mod, "get_config", lambda: SimpleNamespace(
            transcription=SimpleNamespace(
                realtime_providers=[holder["cfg"]],
                active_realtime_provider=holder["cfg"],
            )
        ),
    )
    svc = _svc()
    p1 = svc.get_realtime_translation_provider()
    assert p1._model == "qwen3.5-livetranslate-flash-realtime"

    holder["cfg"] = TranscriptionProviderConfig(
        id="lt-stale",
        name="LT",
        adapter="dashscope_livetranslate_realtime",
        api_key="sk-same",
        model="qwen3-livetranslate-flash-realtime",
        is_active=True,
    )
    p2 = svc.get_realtime_translation_provider()
    assert p2 is not p1
    assert p2._model == "qwen3-livetranslate-flash-realtime"


def test_service_translation_provider_prefers_explicit(monkeypatch):
    from src.config import TranscriptionProviderConfig

    _no_dashscope_required(monkeypatch)
    explicit = TranscriptionProviderConfig(
        id="lt-explicit",
        name="LT explicit",
        adapter="dashscope_livetranslate_realtime",
        api_key="sk-lt",
        model="qwen3-livetranslate-flash-realtime",
        is_active=False,
    )
    dash = TranscriptionProviderConfig(
        id="dash-rt2",
        name="Dash",
        adapter="dashscope_funasr_realtime",
        api_key="sk-other",
        is_active=True,
    )
    _patch_config(monkeypatch, [explicit, dash], dash)

    provider = _svc().get_realtime_translation_provider()
    assert provider._api_key == "sk-lt"
    assert provider._model == "qwen3-livetranslate-flash-realtime"


def test_service_translation_provider_requires_dashscope(monkeypatch):
    from src.config import TranscriptionProviderConfig

    local = TranscriptionProviderConfig(
        id="local-rt",
        name="Local",
        adapter="funasr_onnx_realtime",
        is_active=True,
    )
    _patch_config(monkeypatch, [local], local)
    with pytest.raises(ValueError, match="[Dd]ashScope"):
        _svc().get_realtime_translation_provider()


# ════════════════════════ 6. WS route ════════════════════════


class _FakeTranslateProvider:
    def __init__(self):
        self.started_kwargs = None

    async def start(self, on_segment, hot_words=None, language_hints=None, translation_target=None):
        self.started_kwargs = translation_target
        seg = SimpleNamespace(
            start=0.0, end=1.0, text="hello", speaker_id=None, translation="你好"
        )
        on_segment(seg, True, "k1")

    async def send_frame(self, data):
        pass

    async def stop(self):
        pass


def _ws_setup(monkeypatch, tmp_path, provider):
    import src.meeting.store as meeting_store

    monkeypatch.setattr(meeting_store, "MEETINGS_DIR", tmp_path)
    meeting = meeting_store.create_meeting("live translation ws")

    fake_service = MagicMock()
    fake_service.get_active_realtime_provider_meta.return_value = {
        "id": "x", "adapter": "fake", "name": "Fake", "model": "m",
    }
    fake_service.get_realtime_translation_provider_meta.return_value = {
        "id": "lt",
        "adapter": "dashscope_livetranslate_realtime",
        "name": "LiveTranslate",
        "model": "qwen3.5-livetranslate-flash-realtime",
        "supports_realtime_translation": True,
    }
    fake_service.get_realtime_translation_provider.return_value = provider
    monkeypatch.setattr("src.meeting.routes.meeting_service", fake_service)
    return meeting


def test_ws_translation_target_flow(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from src.main import app

    fake = _FakeTranslateProvider()
    meeting = _ws_setup(monkeypatch, tmp_path, fake)
    client = TestClient(app)
    with client.websocket_connect(
        f"/api/meetings/{meeting.id}/realtime-transcribe?translation_target=zh"
    ) as ws:
        provider_msg = ws.receive_json()
        assert provider_msg["type"] == "provider"
        assert provider_msg["adapter"] == "dashscope_livetranslate_realtime"

        seen = {}
        for _ in range(10):
            m = ws.receive_json()
            seen[m.get("type")] = m
            if "ready" in seen and "transcript" in seen:
                break
        assert "ready" in seen
        transcript = seen["transcript"]
        assert transcript["text"] == "hello"
        assert transcript["translation"] == "你好"
        assert fake.started_kwargs == "zh"

        ws.send_json({"action": "stop"})


def test_ws_translation_unavailable_sends_error(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from src.main import app

    def _boom():
        raise ValueError("Realtime translation requires a DashScope API key")

    fake_service = MagicMock()
    fake_service.get_realtime_translation_provider_meta.return_value = {
        "id": None, "adapter": "dashscope_livetranslate_realtime",
        "name": None, "model": None, "supports_realtime_translation": False,
    }
    fake_service.get_realtime_translation_provider.side_effect = _boom
    monkeypatch.setattr("src.meeting.routes.meeting_service", fake_service)

    import src.meeting.store as meeting_store

    monkeypatch.setattr(meeting_store, "MEETINGS_DIR", tmp_path)
    meeting = meeting_store.create_meeting("lt err")

    client = TestClient(app)
    with client.websocket_connect(
        f"/api/meetings/{meeting.id}/realtime-transcribe?translation_target=zh"
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "provider"
        err = ws.receive_json()
        assert "error" in err
        assert "DashScope" in err["error"]


# ════════════════════════ 7. provider info ════════════════════════


def test_active_provider_info_reports_translation_support(monkeypatch):
    from src.config import TranscriptionProviderConfig
    import src.config as config_mod
    from src.main import app
    from fastapi.testclient import TestClient

    dash = TranscriptionProviderConfig(
        id="dash-rt3",
        name="Dash",
        adapter="dashscope_funasr_realtime",
        api_key="sk-x",
        is_active=True,
    )
    fake_cfg = SimpleNamespace(
        transcription=SimpleNamespace(
            realtime_providers=[dash],
            active_realtime_provider=dash,
            active_file_provider=None,
            get_local_file_provider=lambda: None,
            get_local_realtime_provider=lambda: None,
        )
    )
    monkeypatch.setattr(config_mod, "get_config", lambda: fake_cfg)

    resp = TestClient(app).get("/api/transcription/active-provider-info")
    assert resp.status_code == 200
    assert resp.json()["realtime"]["supports_realtime_translation"] is True

    # local-only → no translation capability
    local = TranscriptionProviderConfig(
        id="l", name="L", adapter="funasr_onnx_realtime", is_active=True
    )
    fake_cfg.transcription.active_realtime_provider = local
    fake_cfg.transcription.realtime_providers = [local]
    resp = TestClient(app).get("/api/transcription/active-provider-info")
    assert resp.json()["realtime"]["supports_realtime_translation"] is False
