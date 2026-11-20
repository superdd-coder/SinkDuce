"""Exercise OpenAI and compatible embedding requests through SDK serialization."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AuthenticationError, OpenAI

from src.api.routes import config as config_routes
from src.config import AppConfig, EmbeddingProviderConfig
from src.providers.embedding import create_embedding_provider, openai_compat
from src.providers.registry import embedding_registry


@pytest.fixture
def sdk_transport(monkeypatch):
    clients = []

    def install(handler):
        def make_client(**kwargs):
            client = OpenAI(
                **kwargs,
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                max_retries=0,
            )
            clients.append(client)
            return client

        monkeypatch.setattr(openai_compat, "OpenAI", make_client)

    yield install
    for client in clients:
        client.close()


def embeddings_response(body, dimensions):
    return httpx.Response(200, json={
        "object": "list",
        "model": body["model"],
        "usage": {"prompt_tokens": len(body["input"]), "total_tokens": len(body["input"])},
        "data": [
            {"object": "embedding", "index": index, "embedding": [float(index + 1)] * dimensions}
            for index in reversed(range(len(body["input"])))
        ],
    })


@pytest.mark.parametrize(
    "kind, base_url, model, requested_dim, expected_url, actual_dim, dimensions_field",
    [
        ("openai", "", "text-embedding-3-large", 0, "https://api.openai.com/v1/embeddings", 3072, None),
        ("openai_compatible", "https://api.openai.com/v1", "text-embedding-ada-002", 1536,
         "https://api.openai.com/v1/embeddings", 1536, None),
        ("remote", "https://api.openai.com/v1", "text-embedding-ada-002", 0,
         "https://api.openai.com/v1/embeddings", 1536, None),
        ("openai_compatible", "https://eu.api.openai.com", "text-embedding-3-small", 8,
         "https://eu.api.openai.com/v1/embeddings", 8, 8),
    ],
)
def test_official_embedding_uses_supported_parameters(
    sdk_transport, kind, base_url, model, requested_dim, expected_url, actual_dim, dimensions_field,
):
    requests = []

    def handle(request):
        assert str(request.url) == expected_url
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == model
        assert body.get("dimensions") == dimensions_field
        assert body["encoding_format"] == "float"
        assert set(body) <= {"model", "input", "dimensions", "encoding_format"}
        return embeddings_response(body, actual_dim)

    sdk_transport(handle)
    config = EmbeddingProviderConfig(
        provider=kind, base_url=base_url, api_key="fixture-key", model=model,
        batch_size=2, dimensions=requested_dim,
    )
    provider = create_embedding_provider(config)
    assert provider.dimensions == actual_dim
    assert config.provider == kind and config.base_url == base_url
    assert not requests
    assert provider.embed_texts(["first", "second", "third"]) == [
        [1.0] * actual_dim, [2.0] * actual_dim, [1.0] * actual_dim,
    ]
    assert provider.embed_query("query") == [1.0] * actual_dim
    assert [r["input"] for r in requests] == [["first", "second"], ["third"], ["query"]]


def test_third_party_embedding_keeps_compatible_parameters(sdk_transport):
    def handle(request):
        assert str(request.url) == "https://api.openai.com.example.test/v1/embeddings"
        body = json.loads(request.content)
        assert body["dimensions"] == 4
        assert body["encoding_format"] == "base64"
        return embeddings_response(body, 4)

    sdk_transport(handle)
    provider = create_embedding_provider(EmbeddingProviderConfig(
        base_url="https://api.openai.com.example.test/v1", api_key="fixture-key",
        model="text-embedding-ada-002", dimensions=4,
    ))
    assert provider.embed_query("A fictional query.") == [1.0] * 4


def test_official_embedding_rejects_mismatched_vector_dimensions(sdk_transport):
    sdk_transport(lambda request: embeddings_response(json.loads(request.content), 1536))
    provider = create_embedding_provider(EmbeddingProviderConfig(
        provider="openai", model="text-embedding-ada-002", api_key="fixture-key", dimensions=256,
    ))
    with pytest.raises(ValueError, match="1536 dimensions.*256"):
        provider.embed_query("A fictional query.")


def test_official_embedding_leaves_discovery_errors_visible(sdk_transport):
    sdk_transport(lambda _: httpx.Response(401, json={"error": {"message": "Invalid fixture key"}}))
    with pytest.raises(AuthenticationError):
        create_embedding_provider(EmbeddingProviderConfig(
            provider="openai", model="fixture-embedding-model", api_key="fixture-key",
        ))


def test_official_embedding_is_a_provider_option():
    options = {entry.name: entry.display_name for entry in embedding_registry.list_primary()}
    assert options["openai"] == "OpenAI"
    assert options["openai_compatible"] == "OpenAI-Compatible"


def test_embedding_settings_save_the_effective_openai_endpoint(monkeypatch):
    config = AppConfig()
    monkeypatch.setattr(config_routes, "get_config", lambda: config)
    monkeypatch.setattr(config_routes, "save_config", lambda _: None)
    monkeypatch.setattr(config_routes, "reload_config", lambda: config)
    monkeypatch.setattr(config_routes, "async_reload_services", AsyncMock())

    async def save():
        created = await config_routes.add_embedding_provider(EmbeddingProviderConfig(
            provider="openai", model="text-embedding-3-small", api_key="fixture-key",
        ))
        assert created["base_url"] == "https://api.openai.com/v1"
        updated = await config_routes.update_embedding_provider(created["id"], {
            "base_url": "https://eu.api.openai.com",
        })
        assert updated["base_url"] == "https://eu.api.openai.com/v1"

    asyncio.run(save())
    assert config.embedding.providers[0].base_url == "https://eu.api.openai.com/v1"


def test_embedding_model_discovery_normalizes_openai_url_and_filters_models(monkeypatch):
    import openai

    monkeypatch.setattr(config_routes, "get_config", lambda: AppConfig(embedding={"providers": [{
        "provider": "openai", "base_url": "https://api.openai.com", "api_key": "fixture-key",
    }]}))
    monkeypatch.setattr(config_routes, "_model_cache", {})
    clients = []

    def remote_models(**kwargs):
        clients.append(kwargs)
        models = [
            SimpleNamespace(id="text-embedding-3-small"),
            SimpleNamespace(id="gpt-4o-mini"),
        ]
        return SimpleNamespace(models=SimpleNamespace(list=lambda: SimpleNamespace(data=models)))

    monkeypatch.setattr(openai, "OpenAI", remote_models)
    result = asyncio.run(config_routes.get_available_models("embedding", {
        "provider": "openai", "base_url": "https://api.openai.com/v1",
    }))
    assert result["models"] == ["text-embedding-3-small"]
    assert clients == [{"base_url": "https://api.openai.com/v1", "api_key": "fixture-key"}]

    result = asyncio.run(config_routes.get_available_models("embedding", {
        "provider": "openai_compatible", "base_url": "https://gateway.example.test/v1",
    }))
    assert result["models"] == ["gpt-4o-mini", "text-embedding-3-small"]
    assert clients[-1] == {"base_url": "https://gateway.example.test/v1", "api_key": "dummy"}
