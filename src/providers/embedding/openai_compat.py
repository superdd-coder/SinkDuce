from __future__ import annotations

import logging

import httpx
from openai import OpenAI

from src.config import EmbeddingProviderConfig
from src.providers.base import EmbeddingProvider
from src.providers.registry import embedding_registry

logger = logging.getLogger(__name__)

# Conservative max chars per text before embedding (≈8000 tokens for DashScope 8192 limit)
_MAX_CHARS = 24000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_CHARS:
        return text
    logger.warning("Truncating chunk from %d to %d chars for embedding", len(text), _MAX_CHARS)
    return text[:_MAX_CHARS]


@embedding_registry.register("openai_compatible", "remote", display_name="OpenAI-Compatible")
class OpenAICompatEmbedding(EmbeddingProvider):
    # DashScope embedding APIs enforce a max batch size of 10
    MAX_BATCH_SIZE = 10

    def __init__(self, config: EmbeddingProviderConfig):
        self._client = OpenAI(
            base_url=config.base_url.strip(),
            api_key=config.api_key.strip(),
            timeout=httpx.Timeout(1800, connect=30),
        )
        self._model = config.model.strip()
        self._batch_size = min(config.batch_size, self.MAX_BATCH_SIZE)
        self._dimensions = config.dimensions
        if self._dimensions <= 0:
            self._dimensions = self._detect_dimensions()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        all_embeddings = []
        batch_size = min(self._batch_size, self.MAX_BATCH_SIZE)
        logger.info("Embedding %d texts in batches of %d (model=%s)", len(texts), batch_size, self._model)
        for i in range(0, len(texts), batch_size):
            batch = [_truncate(t) for t in texts[i : i + batch_size]]
            kwargs = {"input": batch, "model": self._model}
            if self._dimensions > 0:
                kwargs["dimensions"] = self._dimensions
            response = self._client.embeddings.create(**kwargs)
            # Sort by index to guarantee order matches input
            sorted_data = sorted(response.data, key=lambda d: d.index)
            all_embeddings.extend([d.embedding for d in sorted_data])
        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        kwargs = {"input": [_truncate(text)], "model": self._model}
        if self._dimensions > 0:
            kwargs["dimensions"] = self._dimensions
        response = self._client.embeddings.create(**kwargs)
        return response.data[0].embedding

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _detect_dimensions(self) -> int:
        """Call the embedding API once to detect output dimensions."""
        try:
            resp = self._client.embeddings.create(input=["test"], model=self._model)
            return len(resp.data[0].embedding)
        except Exception:
            logger.warning("Failed to auto-detect embedding dimensions, falling back to 1536")
            return 1536
