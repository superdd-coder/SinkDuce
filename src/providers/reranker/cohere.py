from __future__ import annotations

import logging

from src.config import RerankProviderConfig
from src.providers.base import RerankerProvider
from src.providers.registry import reranker_registry

logger = logging.getLogger(__name__)


@reranker_registry.register("cohere", display_name="Cohere")
class CohereReranker(RerankerProvider):
    def __init__(self, config: RerankProviderConfig):
        import cohere

        self._client = cohere.Client(config.api_key)
        self._model = config.model or "rerank-multilingual-v3.0"

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[tuple[int, float]]:
        logger.info("Cohere rerank: %d docs, top_k=%d, model=%s", len(documents), top_k, self._model)
        response = self._client.rerank(
            query=query,
            documents=documents,
            model=self._model,
            top_n=top_k,
        )
        return [(r.index, r.relevance_score) for r in response.results]
