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
        self._batch_size = min(
            config.batch_size if config.batch_size > 0 else self.MAX_BATCH_SIZE,
            self.MAX_BATCH_SIZE,
        )
        self._dimensions = config.dimensions
        if self._dimensions <= 0:
            self._dimensions = self._detect_dimensions()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        all_embeddings = []
        batch_size = self._batch_size
        logger.info("Embedding %d texts in batches of %d (model=%s)", len(texts), batch_size, self._model)
        for i in range(0, len(texts), batch_size):
            batch = [_truncate(t) for t in texts[i : i + batch_size]]
            all_embeddings.extend(self._embed_batch(batch))
        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([_truncate(text)])[0]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(input=batch, model=self._model, **self._request_options())
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def _request_options(self) -> dict:
        return {"dimensions": self._dimensions} if self._dimensions > 0 else {}

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
