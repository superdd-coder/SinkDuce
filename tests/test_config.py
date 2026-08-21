import tempfile
from src.config import load_config, AppConfig, ParsingConfig


def test_load_config_defaults():
    config_content = """
llm:
  providers:
    - id: test-id
      name: test
      provider: openai_compatible
      model: deepseek-chat
      base_url: https://api.deepseek.com/v1
      api_key: sk-test
      is_default: true
embedding:
  provider: local
  model: BAAI/bge-small-zh-v1.5
  dimensions: 512
rerank:
  provider: local
  model: BAAI/bge-reranker-base
rag:
  top_k: 10
  rerank_top_k: 5
qdrant:
  host: qdrant
  port: 6333
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()
        config = load_config(f.name)
        assert isinstance(config, AppConfig)
        assert len(config.llm.providers) == 1
        assert config.llm.providers[0].model == "deepseek-chat"
        assert config.llm.providers[0].api_key == "sk-test"
        assert config.embedding.providers[0].provider == "local"


def test_load_config_old_format_compat():
    """Old single-provider LLM format is converted to providers list."""
    config_content = """
llm:
  provider: openai_compatible
  model: test-model
  base_url: http://localhost
  api_key: sk-old-format
embedding:
  provider: local
  model: test
  dimensions: 128
rerank:
  provider: local
  model: test
qdrant:
  host: localhost
  port: 6333
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()
        config = load_config(f.name)
        assert len(config.llm.providers) == 1
        assert config.llm.providers[0].model == "test-model"
        assert config.llm.providers[0].api_key == "sk-old-format"


def test_parsing_config_defaults():
    cfg = ParsingConfig()
    assert cfg.default_chunk_size == 512


def test_app_config_has_parsing():
    cfg = AppConfig()
    assert isinstance(cfg.parsing, ParsingConfig)
    assert cfg.parsing.default_chunk_size == 512


def test_app_config_locale_defaults_en():
    cfg = AppConfig()
    assert cfg.locale == "en"


def test_load_config_locale_missing_defaults_en():
    config_content = """
llm:
  providers: []
embedding:
  providers: []
rerank:
  providers: []
qdrant:
  host: localhost
  port: 6333
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()
        config = load_config(f.name)
        assert config.locale == "en"


def test_load_config_locale_zh_cn():
    config_content = """
locale: zh-CN
llm:
  providers: []
embedding:
  providers: []
rerank:
  providers: []
qdrant:
  host: localhost
  port: 6333
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()
        config = load_config(f.name)
        assert config.locale == "zh-CN"


def test_app_config_rejects_unknown_locale():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AppConfig(locale="fr")


# ── "remote" alias backward-compat ──────────────────────


def test_load_config_with_remote_provider_alias():
    """Old yaml files using provider: "remote" must still load."""
    config_content = """
llm:
  providers:
    - id: test-id
      name: test
      provider: openai_compatible
      model: test-model
      base_url: http://localhost
      api_key: sk-test
embedding:
  provider: remote
  model: test-embedding
  base_url: http://localhost
  api_key: sk-test
  dimensions: 512
rerank:
  provider: local
  model: test
qdrant:
  host: localhost
  port: 6333
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()
        config = load_config(f.name)
        # Config layer just stores the string — registry does the alias mapping.
        assert config.embedding.providers[0].provider == "remote"


def test_remote_alias_creates_openai_compat_embedding():
    """The 'remote' alias must resolve to OpenAICompatEmbedding in the factory."""
    from src.config import EmbeddingProviderConfig
    from src.providers.embedding import create_embedding_provider
    from src.providers.embedding.openai_compat import OpenAICompatEmbedding

    cfg = EmbeddingProviderConfig(
        provider="remote",
        model="text-embedding-3-small",
        base_url="http://localhost:9999",
        api_key="sk-test",
        dimensions=512,
        batch_size=10,
    )
    provider = create_embedding_provider(cfg)
    assert isinstance(provider, OpenAICompatEmbedding)


def test_openai_compatible_name_creates_openai_compat_embedding():
    """Canonical name 'openai_compatible' should also resolve to OpenAICompatEmbedding."""
    from src.config import EmbeddingProviderConfig
    from src.providers.embedding import create_embedding_provider
    from src.providers.embedding.openai_compat import OpenAICompatEmbedding

    cfg = EmbeddingProviderConfig(
        provider="openai_compatible",
        model="text-embedding-3-small",
        base_url="http://localhost:9999",
        api_key="sk-test",
        dimensions=512,
        batch_size=10,
    )
    provider = create_embedding_provider(cfg)
    assert isinstance(provider, OpenAICompatEmbedding)


def test_provider_types_endpoint_returns_three_sections():
    """The /config/provider-types endpoint must return embedding, reranker, llm."""
    from src.api.routes.config import list_provider_types

    result = list_provider_types()
    assert "embedding" in result
    assert "reranker" in result
    assert "llm" in result
    # "remote" alias must NOT appear in the UI-facing primary list
    embedding_names = {e["name"] for e in result["embedding"]}
    assert "openai_compatible" in embedding_names
    assert "remote" not in embedding_names
