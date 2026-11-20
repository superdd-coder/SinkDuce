from __future__ import annotations

from src.config import EmbeddingProviderConfig
from src.providers.base import EmbeddingProvider
from src.providers.openai_endpoint import is_openai_endpoint
from src.providers.registry import embedding_registry

# Import adapter modules to trigger @register decorators.
# Adding a new adapter requires: (1) write a new module with @embedding_registry.register("name"),
# (2) add the import here. Nothing else.
from src.providers.embedding import openai_compat  # noqa: F401
from src.providers.embedding import openai  # noqa: F401


def create_embedding_provider(config: EmbeddingProviderConfig) -> EmbeddingProvider | None:
    provider = config.provider
    if not provider or provider == "none":
        return None
    if provider in ("openai_compatible", "remote") and is_openai_endpoint(config.base_url):
        provider = "openai"
    return embedding_registry.create(provider, config)
