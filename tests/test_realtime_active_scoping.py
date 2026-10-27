"""Realtime transcription vs LiveTranslate: independent is_active flags.

Both provider kinds share `transcription.realtime_providers`, but they are
separate capabilities: the realtime ASR default and the live-translation
default must never clear each other, and a LiveTranslate provider must never
be resolved as the realtime transcription provider.

Run: pytest tests/test_realtime_active_scoping.py -v --tb=short
"""

from __future__ import annotations

from types import SimpleNamespace

from src.config import TranscriptionProviderConfig, TranscriptionConfig

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


# ══════════════ property: ASR resolution never sees translation ══════════════


def test_active_realtime_provider_ignores_livetranslate():
    cfg = TranscriptionConfig(realtime_providers=[_asr("asr-1", True), _lt("lt-1", True)])
    assert cfg.active_realtime_provider is not None
    assert cfg.active_realtime_provider.id == "asr-1"


def test_active_realtime_provider_none_when_only_livetranslate_active():
    cfg = TranscriptionConfig(realtime_providers=[_lt("lt-1", True)])
    assert cfg.active_realtime_provider is None


# ══════════════ create / update sweeps are kind-scoped ══════════════


def test_create_active_sweeps_only_same_kind():
    from src.api.routes.config import _add_to_provider_list

    asr = _asr("asr-1", active=True)
    lt1 = _lt("lt-1", active=True)
    providers = [asr, lt1]

    new_lt = _lt("lt-new", active=True)
    new_lt.id = ""
    _add_to_provider_list(providers, new_lt, flag="is_active", sweep=_same_kind)

    assert asr.is_active is True, "ASR default must survive a translation create"
    assert lt1.is_active is False, "other translation candidates lose the flag"
    assert providers[-1].is_active is True


def test_update_active_sweeps_only_same_kind():
    from src.api.routes.config import _apply_provider_update

    asr = _asr("asr-1", active=True)
    lt1 = _lt("lt-1", active=True)
    providers = [asr, lt1]

    _apply_provider_update(
        providers, "asr-1", {"is_active": True},
        bool_fields={"is_active"}, exclusive_flag="is_active", sweep=_same_kind,
    )

    assert asr.is_active is True
    assert lt1.is_active is True, "translation default must survive an ASR update"


def _same_kind(a, b) -> bool:
    la = (a.adapter or "") == LT_ADAPTER
    lb = (b.adapter or "") == LT_ADAPTER
    return la == lb


# ══════════════ set-active route ══════════════


def _patch_route_config(monkeypatch, providers):
    import src.api.routes.config as config_routes

    # Real TranscriptionConfig: the builtin branch calls
    # get_local_realtime_provider() and the property must filter LT too.
    cfg = SimpleNamespace(transcription=TranscriptionConfig(realtime_providers=providers))
    monkeypatch.setattr(config_routes, "get_config", lambda: cfg)
    monkeypatch.setattr(config_routes, "save_config", lambda c: None)
    monkeypatch.setattr(config_routes, "reload_config", lambda: None)
    monkeypatch.setattr(config_routes, "_invalidate_transcription_caches", lambda **kw: None)
    monkeypatch.setattr(config_routes, "_maybe_autoload_local", lambda *a, **kw: None)
    return cfg


def test_set_active_route_scopes_exclusivity(monkeypatch):
    from fastapi.testclient import TestClient

    from src.main import app

    asr = _asr("asr-1", active=True)
    lt1 = _lt("lt-1", active=True)
    lt2 = _lt("lt-2", active=False)
    _patch_route_config(monkeypatch, [asr, lt1, lt2])

    client = TestClient(app)
    # Translation default switches lt-1 → lt-2; ASR default untouched.
    resp = client.post("/api/transcription/realtime-providers/lt-2/set-active")
    assert resp.status_code == 200
    assert lt2.is_active is True
    assert lt1.is_active is False
    assert asr.is_active is True

    # ASR default re-selected; translation default untouched.
    resp = client.post("/api/transcription/realtime-providers/asr-1/set-active")
    assert resp.status_code == 200
    assert asr.is_active is True
    assert lt2.is_active is True


def test_set_active_route_builtin_keeps_translation(monkeypatch):
    from fastapi.testclient import TestClient

    from src.main import app

    asr = _asr("asr-1", active=True)
    lt1 = _lt("lt-1", active=True)
    cfg = _patch_route_config(monkeypatch, [asr, lt1])

    client = TestClient(app)
    resp = client.post("/api/transcription/realtime-providers/builtin-local-rt/set-active")
    assert resp.status_code == 200
    assert lt1.is_active is True, "local ASR switch must keep the translation default"
    final = cfg.transcription.realtime_providers
    builtin = next(p for p in final if p.id == "builtin-local-rt")
    assert builtin.is_active is True
    # The local builtin becomes the ASR default; the cloud ASR row yields.
    assert asr.is_active is False
