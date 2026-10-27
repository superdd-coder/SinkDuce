"""Realtime vocabulary cache: cloud tables are reused across connections.

DashScope caps accounts at 10 vocabulary tables — the old create-per-connect
flow burned quota AND added ~3s (create + poll + settle) to every connect,
translation toggle, and page-refresh reconnect. Tables are now cached per
(model, words, api_key), LRU-capped on disk, and only eviction deletes.

Run: pytest tests/test_dashscope_vocab_cleanup.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


class _FakeVocabService:
    """Counts cloud calls; returns stable ids; records deletions."""

    created: list[dict] = []
    deleted: list[str] = []
    fail_create: bool = False

    def __init__(self, api_key=None):
        self.api_key = api_key

    def create_vocabulary(self, target_model, prefix, vocabulary):
        if _FakeVocabService.fail_create:
            raise RuntimeError("quota")
        _FakeVocabService.created.append(
            {"model": target_model, "prefix": prefix, "words": vocabulary}
        )
        return f"vocab-{len(_FakeVocabService.created)}"

    def query_vocabulary(self, vocab_id):
        return [{"status": "OK"}]

    def delete_vocabulary(self, vocab_id):
        _FakeVocabService.deleted.append(vocab_id)


class _OkRecognition:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _OkRecognition.instances.append(self)

    instances: list = []
    fail_first_start: bool = False

    def start(self, **kwargs):
        # A stale (deleted-out-of-band) vocabulary_id fails at start(), not
        # at construction — mirror the real SDK.
        if _OkRecognition.fail_first_start:
            _OkRecognition.fail_first_start = False
            raise RuntimeError("stale vocabulary_id")

    def stop(self):
        pass

    def send_audio_frame(self, data):
        pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import src.meeting.transcription.dashscope_realtime as mod
    from src.config import TranscriptionProviderConfig

    _FakeVocabService.created.clear()
    _FakeVocabService.deleted.clear()
    _OkRecognition.instances.clear()
    _OkRecognition.fail_first_start = False
    monkeypatch.setattr(mod, "_require_dashscope", lambda: None)
    monkeypatch.setattr(mod, "VocabularyService", _FakeVocabService, raising=False)
    monkeypatch.setattr(mod, "Recognition", _OkRecognition, raising=False)
    monkeypatch.setattr(
        mod, "dashscope", SimpleNamespace(api_key=None, base_websocket_api_url=None),
        raising=False,
    )
    monkeypatch.setattr("src.config.DATA_DIR", tmp_path)
    with mod._vocab_cache_lock:
        mod._vocab_cache.clear()
        mod._vocab_cache_loaded = False

    cfg = TranscriptionProviderConfig(
        id="x", name="x", adapter="dashscope_realtime",
        api_key="k", model="fun-asr-realtime",
    )
    hot_words = [{"text": "SinkDuce", "weight": 5}]

    def make():
        return mod.DashScopeRealtimeTranscription(cfg)

    def start(p, words):
        asyncio.run(
            p.start(lambda *a, **k: None, hot_words=words, language_hints=None)
        )
        # asyncio.run() clears the thread's current loop on py3.10; restore
        # one so later implicit get_event_loop() callers still work.
        asyncio.set_event_loop(asyncio.new_event_loop())

    yield SimpleNamespace(mod=mod, make=make, hot_words=hot_words, start=start)

    with mod._vocab_cache_lock:
        mod._vocab_cache.clear()
        mod._vocab_cache_loaded = False


def test_second_connection_reuses_cached_vocabulary(env):
    p1 = env.make()
    env.start(p1, env.hot_words)
    assert len(_FakeVocabService.created) == 1

    p2 = env.make()
    env.start(p2, env.hot_words)  # fresh instance, same words → cache hit
    assert len(_FakeVocabService.created) == 1, "cache miss on identical words"
    assert p2._vocab_id == p1._vocab_id


def test_changed_words_create_new_table(env):
    env.start(env.make(), env.hot_words)
    env.start(env.make(), [{"text": "Other", "weight": 3}])
    assert len(_FakeVocabService.created) == 2


def test_stop_keeps_cached_vocabulary_alive(env):
    p = env.make()
    env.start(p, env.hot_words)
    vid = p._vocab_id
    asyncio.run(p.stop())
    asyncio.set_event_loop(asyncio.new_event_loop())
    assert _FakeVocabService.deleted == [], "stop() must not delete cached tables"
    assert vid  # and the table remains reusable


def test_cache_eviction_deletes_oldest_table(env):
    for i in range(5):  # cap is 4 → the first table must be evicted+deleted
        env.start(env.make(), [{"text": f"word-{i}", "weight": 5}])
    assert len(_FakeVocabService.created) == 5
    assert _FakeVocabService.deleted == ["vocab-1"]
    with env.mod._vocab_cache_lock:
        assert len(env.mod._vocab_cache) == 4


def test_cache_survives_restart_via_disk(env, tmp_path):
    env.start(env.make(), env.hot_words)
    cache_file = tmp_path / "hot_words" / "realtime_vocab_cache.json"
    assert cache_file.exists()
    with env.mod._vocab_cache_lock:
        env.mod._vocab_cache.clear()
        env.mod._vocab_cache_loaded = False
    env.start(env.make(), env.hot_words)  # "restarted" process
    assert len(_FakeVocabService.created) == 1, "disk cache not reused after restart"


def test_stale_cached_vocabulary_retries_with_fresh_table(env):
    p = env.make()
    env.start(p, env.hot_words)
    first_id = p._vocab_id

    # Simulate the table being deleted out-of-band (quota cleanup): the next
    # start() fails on the cached id, drops it, and retries with a fresh one.
    _OkRecognition.fail_first_start = True
    env.start(env.make(), env.hot_words)
    assert len(_FakeVocabService.created) == 2
    assert _FakeVocabService.deleted == [first_id]
    with env.mod._vocab_cache_lock:
        assert len(env.mod._vocab_cache) == 1


def test_cache_file_shape(env, tmp_path):
    env.start(env.make(), env.hot_words)
    data = json.loads(
        (tmp_path / "hot_words" / "realtime_vocab_cache.json").read_text()
    )
    assert isinstance(data.get("entries"), list) and len(data["entries"]) == 1
    entry = data["entries"][0]
    assert (
        entry["vocab_id"]
        and entry["api_key"] == "k"
        and entry["model"] == "fun-asr-realtime"
    )
