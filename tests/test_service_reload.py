"""Settings save / set-default must not block on ASR stagger or Qdrant reconnect."""

from __future__ import annotations

from src.config import (
    AppConfig,
    LLMConfig,
    LLMProviderConfig,
    TranscriptionConfig,
    TranscriptionProviderConfig,
)


def _cloud_transcription() -> TranscriptionConfig:
    return TranscriptionConfig(
        file_providers=[
            TranscriptionProviderConfig(
                id="cloud-file", adapter="dashscope_funasr", is_active=True
            )
        ],
        realtime_providers=[
            TranscriptionProviderConfig(
                id="cloud-rt", adapter="dashscope_funasr_realtime", is_active=True
            )
        ],
    )


def _local_transcription() -> TranscriptionConfig:
    return TranscriptionConfig(
        file_providers=[
            TranscriptionProviderConfig(
                id="builtin-local-file", adapter="funasr_onnx", is_active=True
            )
        ],
        realtime_providers=[
            TranscriptionProviderConfig(
                id="builtin-local-rt", adapter="funasr_onnx_realtime", is_active=True
            )
        ],
    )


def test_preload_does_not_sleep_when_no_local_asr(monkeypatch):
    from src.services import _preload_transcription_providers

    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    _preload_transcription_providers(AppConfig(transcription=_cloud_transcription()))
    assert sleeps == []


def test_preload_does_not_sleep_when_local_asr_already_loaded(monkeypatch):
    from src.services import _preload_transcription_providers

    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("src.providers.load_state.get_state", lambda _id: "loaded")
    monkeypatch.setattr("src.services._is_builtin_model_downloaded", lambda _id: True)

    _preload_transcription_providers(AppConfig(transcription=_local_transcription()))
    assert sleeps == []


def test_preload_staggers_only_when_both_local_loads_start(monkeypatch):
    from src.services import _preload_transcription_providers

    sleeps: list[float] = []
    started: list[str] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("src.providers.load_state.get_state", lambda _id: "unloaded")
    monkeypatch.setattr("src.services._is_builtin_model_downloaded", lambda _id: True)
    monkeypatch.setattr(
        "src.services.reload_provider",
        lambda model_id, loading=False: started.append(model_id),
    )

    _preload_transcription_providers(AppConfig(transcription=_local_transcription()))
    assert started == ["builtin-local-file", "builtin-local-rt"]
    assert sleeps == [3]


def test_init_services_can_skip_transcription_preload(monkeypatch):
    from src import services as svc

    called: list[bool] = []
    monkeypatch.setattr(
        "src.services._preload_transcription_providers",
        lambda *_a, **_k: called.append(True),
    )
    monkeypatch.setattr("src.services.get_config", lambda: AppConfig())
    monkeypatch.setattr("src.services.QdrantManager", lambda **_k: object())
    monkeypatch.setattr("src.services.create_embedding_provider", lambda _c: None)
    monkeypatch.setattr("src.services.create_llm_provider", lambda _c: None)
    monkeypatch.setattr("src.services.create_reranker_provider", lambda _c: None)

    class _Store:
        def __init__(self, *_a, **_k):
            pass

    monkeypatch.setattr("src.db.sessions.SessionStore", _Store)

    svc.init_services(preload_transcription=False)
    assert called == []

    svc.init_services(preload_transcription=True)
    assert called == [True]


def test_refresh_llm_runtime_skips_qdrant_and_asr(monkeypatch):
    from src import services as svc

    qdrant_calls: list[dict] = []
    preload_calls: list[int] = []
    store_calls: list[int] = []

    monkeypatch.setattr(
        "src.services.QdrantManager",
        lambda **kw: qdrant_calls.append(kw) or object(),
    )
    monkeypatch.setattr(
        "src.services._preload_transcription_providers",
        lambda *_a, **_k: preload_calls.append(1),
    )

    class _Store:
        def __init__(self, *_a, **_k):
            store_calls.append(1)

    monkeypatch.setattr("src.db.sessions.SessionStore", _Store)
    monkeypatch.setattr(
        "src.services.get_config",
        lambda: AppConfig(
            llm=LLMConfig(
                providers=[
                    LLMProviderConfig(
                        id="p1",
                        name="one",
                        is_default=True,
                        function_call_model_ids=["m1"],
                    )
                ]
            )
        ),
    )

    class _LLM:
        _model = "m1"

    monkeypatch.setattr("src.services.create_llm_provider", lambda _c: _LLM())
    monkeypatch.setattr(
        "src.providers.llm.create_llm_for_provider", lambda _p, model=None: _LLM()
    )

    db = object()
    embedding = object()
    retriever = object()
    reranker = object()
    session_store = object()
    svc.services.db = db
    svc.services.embedding = embedding
    svc.services.retriever = retriever
    svc.services.reranker = reranker
    svc.services.session_store = session_store

    svc.refresh_llm_runtime()

    assert qdrant_calls == []
    assert preload_calls == []
    assert store_calls == []
    assert svc.services.db is db
    assert svc.services.embedding is embedding
    assert svc.services.retriever is retriever
    assert svc.services.session_store is session_store
    assert svc.services.llm is not None
    assert svc.services.agentic_query is not None
    assert svc.services.chatbox_agent is not None
