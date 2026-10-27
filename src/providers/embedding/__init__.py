from __future__ import annotations

from src.config import EmbeddingProviderConfig
from src.providers.base import EmbeddingProvider
from src.providers.registry import embedding_registry

# Import adapter modules to trigger @register decorators.
# Adding a new adapter requires: (1) write a new module with @embedding_registry.register("name"),
# (2) add the import here. Nothing else.
from src.providers.embedding import openai_compat  # noqa: F401


def create_embedding_provider(config: EmbeddingProviderConfig) -> EmbeddingProvider | None:
    if not config.provider or config.provider == "none":
        return None
    return embedding_registry.create(config.provider, config)
