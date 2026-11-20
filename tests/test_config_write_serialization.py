"""Concurrent provider creates must not lose updates (oneshot race).

Regression: the oneshot dialog fires six provider creates in parallel. Each
config-mutating endpoint does read-modify-write of the whole config.yaml, so
without serialization the last stale snapshot to land wiped every provider
written before it — oneshot reported success with only the LLM provider left
(embedding / rerank / transcription providers gone from Settings).
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.routes.config as config_routes
import src.config as config_module
from src.config import (
    AppConfig,
    EmbeddingProviderConfig,
    LLMProviderConfig,
    RerankProviderConfig,
    TranscriptionProviderConfig,
)


def test_llm_api_mode_round_trips_through_settings(tmp_path, monkeypatch):
    from src.providers.llm import thinking

    config_file = tmp_path / "config.yaml"
    config_module.save_config(AppConfig(), config_file)
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_file)
    monkeypatch.setattr(config_routes, "async_refresh_llm_runtime", AsyncMock())
    probes = []

    def probe(base_url, api_key, model):
        probes.append(base_url)
        return "toggle"

    monkeypatch.setattr(thinking, "probe_thinking_mode", probe)
    app = FastAPI()
    app.include_router(config_routes.router, prefix="/api")
    app.dependency_overrides[config_routes._config_write_gate] = lambda: None

    gateway = "https://gateway.example.test/v1"
    official = "https://api.openai.com/v1"
    with TestClient(app) as client:
        path = "/api/llm/providers"
        created = client.post(path, json={
            "base_url": gateway, "model": "fixture-model", "api_key": "fixture-key", "api_mode": "responses",
        }).json()
        assert created["api_mode"] == "responses"
        assert not probes

        url = f"{path}/{created['id']}"
        assert client.put(url, json={"api_mode": "chat_completions"}).json()["api_mode"] == "chat_completions"
        assert probes == [gateway]
        assert client.put(url, json={"base_url": official}).status_code == 200
        assert client.put(url, json={"api_mode": "unknown"}).status_code == 422
        assert client.get(path).json()[0]["api_mode"] == "chat_completions"
        assert config_module.load_config(config_file).llm.providers[0].api_mode == "chat_completions"

        automatic = client.post(path, json={"base_url": official, "model": "gpt-4o-mini"}).json()
        assert automatic["api_mode"] is None
        assert client.get(path).json()[1]["api_mode"] is None


def test_concurrent_provider_creates_all_persist():
    disk: list[AppConfig] = [AppConfig()]

    def fake_get_config():
        return deepcopy(disk[0])

    def fake_reload_config():
        return fake_get_config()

    def fake_save_config(cfg):
        disk[0] = cfg

    async def scenario():
        await asyncio.gather(
            config_routes.add_llm_provider(
                LLMProviderConfig(
                    name="L",
                    provider="openai_compatible",
                    model="m1",
                    base_url="https://x/v1",
                    api_key="k",
                    is_default=True,
                )
            ),
            config_routes.add_embedding_provider(
                EmbeddingProviderConfig(
                    name="E",
                    provider="openai_compatible",
                    model="m2",
                    base_url="https://x/v1",
                    api_key="k",
                    dimensions=1536,
                    is_default=True,
                )
            ),
            config_routes.add_rerank_provider(
                RerankProviderConfig(
                    name="R", provider="qwen", model="gte-rerank", api_key="k", is_default=True
                )
            ),
            config_routes.add_realtime_transcription_provider(
                TranscriptionProviderConfig(
                    name="T",
                    adapter="dashscope_funasr_realtime",
                    model="fun-asr-realtime",
                    api_key="k",
                    is_active=True,
                )
            ),
        )
        return disk[0]

    with (
        patch.object(config_routes, "get_config", fake_get_config),
        patch.object(config_routes, "reload_config", fake_reload_config),
        patch.object(config_routes, "save_config", fake_save_config),
        patch.object(config_routes, "async_reload_services", AsyncMock()),
        patch.object(config_routes, "async_refresh_llm_runtime", AsyncMock()),
        patch.object(config_routes, "_probe_provider_thinking", return_value=""),
    ):
        final = asyncio.run(scenario())

    assert len(final.llm.providers) == 1, "LLM provider lost to a concurrent write"
    assert len(final.embedding.providers) == 1, "embedding provider lost to a concurrent write"
    assert len(final.rerank.providers) == 1, "rerank provider lost to a concurrent write"
    assert len(final.transcription.realtime_providers) == 1, (
        "realtime provider lost to a concurrent write"
    )
    assert final.llm.providers[0].is_default is True
    assert final.embedding.providers[0].is_default is True
    assert final.rerank.providers[0].is_default is True
    assert final.transcription.realtime_providers[0].is_active is True
