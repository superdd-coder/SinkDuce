"""OpenAI's Embeddings API for first-party models."""

from urllib.parse import urlsplit

from src.config import EmbeddingProviderConfig
from src.providers.openai_endpoint import OPENAI_API_BASE_URL, is_openai_endpoint
from src.providers.registry import embedding_registry

from .openai_compat import OpenAICompatEmbedding


_LEGACY_MODEL = "text-embedding-ada-002"
_NATIVE_DIMENSIONS = {
    _LEGACY_MODEL: 1536,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


def resolve_embedding_base_url(base_url: str, provider: str) -> str:
    base_url = base_url.strip()
    if not base_url and provider == "openai":
        return OPENAI_API_BASE_URL
    if is_openai_endpoint(base_url):
        url = urlsplit(base_url)
        if url.path in ("", "/"):
            return url._replace(path="/v1").geturl()
    return base_url


@embedding_registry.register("openai", display_name="OpenAI")
class OpenAIEmbedding(OpenAICompatEmbedding):
    def __init__(self, config: EmbeddingProviderConfig):
        self._requested_dimensions = config.dimensions
        base_url = resolve_embedding_base_url(config.base_url, "openai")
        super().__init__(config.model_copy(update={"base_url": base_url}))

    def _request_options(self) -> dict:
        options = {"encoding_format": "float"}
        if self._requested_dimensions > 0 and self._model != _LEGACY_MODEL:
            options["dimensions"] = self._requested_dimensions
        return options

    def _detect_dimensions(self) -> int:
        return _NATIVE_DIMENSIONS.get(self._model) or len(self._embed_batch(["test"])[0])

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        vectors = super()._embed_batch(batch)
        if len(vectors) != len(batch):
            raise ValueError(f"OpenAI returned {len(vectors)} embeddings for {len(batch)} inputs")
        for vector in vectors:
            if not vector or (self._dimensions > 0 and len(vector) != self._dimensions):
                raise ValueError(
                    f"{self._model} returned {len(vector)} dimensions. "
                    f"Expected {self._dimensions or 'a non-empty vector'}."
                )
        return vectors
