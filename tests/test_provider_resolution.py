"""Tests for per-collection embedding and temporary rerank override logic.

Verifies that:
1. embedding_provider_id in collection config resolves to the correct provider
2. rerank_provider_id in search request creates a temporary reranker
3. Fallback to global default when no override is specified
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config import (
    AppConfig, EmbeddingConfig, EmbeddingProviderConfig,
    RerankConfig, RerankProviderConfig,
)
from src.rag.collection_utils import get_collection_embedding, get_collection_reranker


# ── Helpers ───────────────────────────────────────────────


def _make_embedding_config():
    return AppConfig(
        embedding=EmbeddingConfig(providers=[
            EmbeddingProviderConfig(
                id="emb-1", name="Provider A", provider="remote",
                model="model-a", base_url="http://a.com", api_key="key-a",
                dimensions=512, batch_size=10, is_default=True,
            ),
            EmbeddingProviderConfig(
                id="emb-2", name="Provider B", provider="remote",
                model="model-b", base_url="http://b.com", api_key="key-b",
                dimensions=1024, batch_size=20, is_default=False,
            ),
        ]),
    )


def _make_rerank_config():
    return AppConfig(
        rerank=RerankConfig(providers=[
            RerankProviderConfig(
                id="rerank-1", name="Reranker A", provider="local",
                model="model-a", top_k=5, is_default=True,
            ),
            RerankProviderConfig(
                id="rerank-2", name="Reranker B", provider="cohere",
                model="model-b", base_url="http://cohere.com",
                api_key="key-b", top_k=3, is_default=False,
            ),
        ]),
    )


# ── Embedding Provider Resolution ─────────────────────────


class TestCollectionEmbeddingResolution:
    """Test that embedding_provider_id correctly resolves to a provider."""

    def test_resolve_by_provider_id(self):
        """Collection with embedding_provider_id=emb-2 uses Provider B."""
        config = _make_embedding_config()
        mock_db = MagicMock()
        mock_db.get_vector_size.return_value = 1024

        mock_services = MagicMock()
        mock_services.config = config
        mock_services.db = mock_db

        with patch("src.services.services", mock_services), \
             patch("src.rag.collection_utils.create_embedding_provider") as mock_create:
            mock_create.return_value = MagicMock(dimensions=1024)

            col_config = {"embedding_provider_id": "emb-2"}
            get_collection_embedding(col_config, "test_col")

            mock_create.assert_called_once()
            called_cfg = mock_create.call_args[0][0]
            assert called_cfg.id == "emb-2"
            assert called_cfg.model == "model-b"
            assert called_cfg.api_key == "key-b"

    def test_resolve_fallback_to_default(self):
        """Collection without embedding_provider_id uses the global default."""
        config = _make_embedding_config()
        mock_db = MagicMock()
        mock_db.get_vector_size.return_value = 512

        mock_services = MagicMock()
        mock_services.config = config
        mock_services.db = mock_db

        with patch("src.services.services", mock_services), \
             patch("src.rag.collection_utils.create_embedding_provider") as mock_create:
            mock_create.return_value = MagicMock(dimensions=512)

            col_config = {}
            get_collection_embedding(col_config, "test_col")

            mock_create.assert_called_once()
            called_cfg = mock_create.call_args[0][0]
            assert called_cfg.id == "emb-1"

    def test_resolve_nonexistent_provider_id_falls_back(self):
        """Invalid embedding_provider_id falls back to global default."""
        config = _make_embedding_config()
        mock_db = MagicMock()
        mock_db.get_vector_size.return_value = 512

        mock_services = MagicMock()
        mock_services.config = config
        mock_services.db = mock_db

        with patch("src.services.services", mock_services), \
             patch("src.rag.collection_utils.create_embedding_provider") as mock_create:
            mock_create.return_value = MagicMock(dimensions=512)

            col_config = {"embedding_provider_id": "nonexistent"}
            get_collection_embedding(col_config, "test_col")

            mock_create.assert_called_once()
            called_cfg = mock_create.call_args[0][0]
            assert called_cfg.id == "emb-1"

    def test_actual_dim_overrides_provider_dim(self):
        """Collection's actual vector size overrides provider's dimensions."""
        config = _make_embedding_config()
        mock_db = MagicMock()
        mock_db.get_vector_size.return_value = 768

        mock_services = MagicMock()
        mock_services.config = config
        mock_services.db = mock_db

        with patch("src.services.services", mock_services), \
             patch("src.rag.collection_utils.create_embedding_provider") as mock_create:
            mock_create.return_value = MagicMock(dimensions=768)

            col_config = {"embedding_provider_id": "emb-2"}
            get_collection_embedding(col_config, "test_col")

            called_cfg = mock_create.call_args[0][0]
            assert called_cfg.dimensions == 768


# ── Reranker Provider Resolution ──────────────────────────


class TestCollectionRerankerResolution:
    """Test that rerank provider resolution works per-collection."""

    def test_resolve_rerank_by_provider_id(self):
        """Collection with rerank_provider_id=rerank-2 uses Reranker B."""
        config = _make_rerank_config()

        mock_services = MagicMock()
        mock_services.config = config

        with patch("src.services.services", mock_services), \
             patch("src.rag.collection_utils.create_reranker_provider") as mock_create:
            mock_create.return_value = MagicMock()

            col_config = {"rerank_provider_id": "rerank-2"}
            get_collection_reranker(col_config)

            mock_create.assert_called_once()
            called_cfg = mock_create.call_args[0][0]
            assert called_cfg.id == "rerank-2"
            assert called_cfg.provider == "cohere"

    def test_resolve_rerank_fallback_to_default(self):
        """Collection without rerank_provider_id uses global default."""
        config = _make_rerank_config()

        mock_services = MagicMock()
        mock_services.config = config

        with patch("src.services.services", mock_services), \
             patch("src.rag.collection_utils.create_reranker_provider") as mock_create:
            mock_create.return_value = MagicMock()

            col_config = {}
            get_collection_reranker(col_config)

            mock_create.assert_called_once()
            called_cfg = mock_create.call_args[0][0]
            assert called_cfg.id == "rerank-1"


# ── Temporary Rerank Override in Recall ───────────────────


class TestRecallRerankOverride:
    """Test that rerank_provider_id in search request creates a temporary reranker."""

    def _make_services(self):
        svc = MagicMock()
        svc.config.rerank.providers = [
            RerankProviderConfig(
                id="rerank-1", name="Default Reranker", provider="local",
                model="default-model", top_k=5, is_default=True,
            ),
            RerankProviderConfig(
                id="rerank-2", name="Override Reranker", provider="cohere",
                model="cohere-model", base_url="http://cohere.com",
                api_key="key", top_k=3, is_default=False,
            ),
        ]
        svc.config.rag.rerank_top_k = 5
        svc.reranker = MagicMock()
        svc.reranker.provider = MagicMock()
        return svc

    def test_override_creates_temporary_reranker(self):
        """rerank_provider_id=rerank-2 creates a temporary reranker from that provider."""
        from src.api.routes.recall import _resolve_reranker

        svc = self._make_services()

        with patch("src.api.routes.recall.services", svc), \
             patch("src.providers.reranker.create_reranker_provider") as mock_create:
            mock_provider = MagicMock()
            mock_create.return_value = mock_provider

            reranker = _resolve_reranker("rerank-2")

            mock_create.assert_called_once()
            called_cfg = mock_create.call_args[0][0]
            assert called_cfg.id == "rerank-2"
            assert called_cfg.model == "cohere-model"

    def test_no_override_returns_global_default(self):
        """No rerank_provider_id returns the global default reranker."""
        from src.api.routes.recall import _resolve_reranker

        svc = self._make_services()

        with patch("src.api.routes.recall.services", svc):
            reranker = _resolve_reranker(None)
            assert reranker == svc.reranker

    def test_empty_string_returns_global_default(self):
        """Empty string rerank_provider_id returns the global default reranker."""
        from src.api.routes.recall import _resolve_reranker

        svc = self._make_services()

        with patch("src.api.routes.recall.services", svc):
            reranker = _resolve_reranker("")
            assert reranker == svc.reranker

    def test_nonexistent_provider_is_rejected(self):
        """An explicit unknown provider must not silently select another reranker."""
        from fastapi import HTTPException
        from src.api.routes.recall import _resolve_reranker

        svc = self._make_services()

        with patch("src.api.routes.recall.services", svc), \
             pytest.raises(HTTPException) as rejected:
            _resolve_reranker("nonexistent")
        assert rejected.value.status_code == 422


# ── Config Migration ──────────────────────────────────────


class TestConfigMigration:
    """Test backward compat migration for embedding/rerank config."""

    def test_old_embedding_format_migrated(self):
        """Old single-embedding format is migrated to providers list."""
        from src.config import load_config
        import tempfile, os

        config_content = """
llm:
  provider: openai_compatible
  model: test
  base_url: http://localhost
  api_key: sk-test
embedding:
  provider: local
  model: BAAI/bge-small-zh-v1.5
  dimensions: 512
  batch_size: 10
rerank:
  provider: none
qdrant:
  host: localhost
  port: 6333
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()
            config = load_config(f.name)
            os.unlink(f.name)

            assert len(config.embedding.providers) == 1
            assert config.embedding.providers[0].provider == "local"
            assert config.embedding.providers[0].model == "BAAI/bge-small-zh-v1.5"
            assert config.embedding.providers[0].is_default is True

    def test_new_providers_format_preserved(self):
        """New providers list format is preserved as-is."""
        from src.config import load_config
        import tempfile, os

        config_content = """
llm:
  providers:
    - name: test
      model: test
      is_default: true
embedding:
  providers:
    - id: emb-1
      name: My Embedding
      provider: remote
      model: text-embedding-3-small
      is_default: true
rerank:
  providers:
    - id: rerank-1
      name: My Reranker
      provider: cohere
      model: rerank-v3
      is_default: true
qdrant:
  host: localhost
  port: 6333
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()
            config = load_config(f.name)
            os.unlink(f.name)

            assert len(config.embedding.providers) == 1
            assert config.embedding.providers[0].id == "emb-1"
            assert len(config.rerank.providers) == 1
            assert config.rerank.providers[0].id == "rerank-1"
