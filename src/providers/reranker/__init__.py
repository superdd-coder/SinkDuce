from __future__ import annotations

from src.config import RerankProviderConfig
from src.providers.base import RerankerProvider
from src.providers.registry import reranker_registry

# Import adapter modules to trigger @register decorators.
# Adding a new adapter requires: (1) write a new module with @reranker_registry.register("name"),
# (2) add the import here. Nothing else.
from src.providers.reranker import cohere, qwen, openai_compat  # noqa: F401


def create_reranker_provider(config: RerankProviderConfig) -> RerankerProvider | None:
    if not config.provider or config.provider == "none":
        return None
    return reranker_registry.create(config.provider, config)
