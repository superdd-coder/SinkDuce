"""Shared utilities for per-collection embedding, parent-child retrieval, and context building."""

from __future__ import annotations

import logging
from concurrent.futures import Future
from threading import Lock

logger = logging.getLogger(__name__)

from src.config import EmbeddingBinding, EmbeddingConfig, EmbeddingProviderConfig
from src.providers.base import EmbeddingProvider, RerankerProvider
from src.providers.embedding import create_embedding_provider
from src.providers.reranker import create_reranker_provider
from src.rag.retriever import RetrievedChunk


def _find_provider_by_id(providers: list, provider_id: str):
    """Find a provider config by ID from a list."""
    return next((p for p in providers if p.id == provider_id), None)


def collection_binding(config: dict, settings: EmbeddingConfig, dimensions: int | None = None) -> EmbeddingBinding | None:
    """Read a pin, or reconstruct a pre-upgrade index from the upgrade snapshot."""
    if config.get("embedding_binding"):
        return EmbeddingBinding.model_validate(config["embedding_binding"])
    provider_id = config.get("embedding_provider_id")
    old = settings.legacy_default
    if provider_id:
        old = next((p for p in settings.legacy_providers or [] if p.provider_id == provider_id), None)
    elif config.get("embedding_model"):
        provider = EmbeddingProviderConfig(
            provider=config.get("embedding_provider") or "openai_compatible",
            model=config["embedding_model"], base_url=config.get("embedding_base_url") or "",
        )
        old = EmbeddingBinding.from_provider(provider)
        if not provider.base_url and settings.legacy_default and old.model == settings.legacy_default.model:
            old = settings.legacy_default
    if old:
        return old.model_copy(update={"dimensions": dimensions or config.get("dimensions") or old.dimensions})
    return None


def new_collection_provider(
    provider_id: str | None = None, *, model: str | None = None,
    base_url: str | None = None, provider: str | None = None,
) -> EmbeddingProviderConfig:
    from src.services import services

    settings = getattr(services.config, "embedding", None)
    if settings is None:
        raise ValueError("No embedding model is configured.")
    choices = [p for p in settings.providers if not provider_id or p.id == provider_id]
    if not choices:
        raise ValueError("Choose a configured embedding model before creating a collection.")
    hints = {k: v for k, v in (("model", model), ("base_url", base_url), ("provider", provider)) if v}
    for choice in sorted(choices, key=lambda p: not p.is_default):
        # Legacy clients can name a registered model inline. The central
        # connection owns the credentials throughout the collection's life.
        if not hints or EmbeddingBinding.from_provider(choice.model_copy(update=hints)).resolve([choice]):
            return choice
    raise ValueError("The embedding settings do not match a configured model.")


def get_collection_embedding_config(
    col_config: dict, collection: str = ""
) -> EmbeddingProviderConfig | None:
    from src.services import services

    settings = services.config.embedding
    size = services.db.get_vector_size(collection) if collection and not col_config.get("embedding_binding") else None
    binding = collection_binding(col_config, settings, size)
    return binding.resolve(settings.providers) if binding else None


def embedding_status(col_config: dict, collection: str) -> dict:
    from src.services import services

    settings = services.config.embedding
    size = services.db.get_vector_size(collection) if not col_config.get("embedding_binding") else None
    binding = collection_binding(col_config, settings, size)
    provider = binding.resolve(settings.providers) if binding else None
    return {"embedding_model": binding.model if binding else col_config.get("embedding_model", ""),
            "embedding_provider_id": provider.id if provider else None,
            "embedding_disconnected": provider is None,
            "dimensions": binding.dimensions if binding else size}


def get_collection_embedding(col_config: dict, collection: str = "") -> EmbeddingProvider | None:
    config = get_collection_embedding_config(col_config, collection)
    provider = create_embedding_provider(config) if config else None
    if provider is not None:
        provider.index_binding = EmbeddingBinding.from_provider(config)
    return provider


class EmbeddingDisconnected(RuntimeError):
    pass


class _UnavailableEmbedding:
    def embed_query(self, text):
        raise EmbeddingDisconnected("The collection's embedding model is unavailable.")


UNAVAILABLE_EMBEDDING = _UnavailableEmbedding()


class _QueryEmbedding(EmbeddingProvider):
    """Share a query vector between collections using the same connection and dimensions."""
    def __init__(self, provider, binding):
        self.provider = provider
        self.index_binding = binding
        self._queries: dict[str, Future] = {}
        self._lock = Lock()

    @property
    def dimensions(self):
        return self.provider.dimensions

    def embed_texts(self, texts):
        return self.provider.embed_texts(texts)

    def embed_query(self, text):
        with self._lock:
            future = self._queries.get(text)
            if future is None:
                future = self._queries[text] = Future()
                encode = True
            else:
                encode = False
        if encode:
            try:
                future.set_result(self.provider.embed_query(text))
            except Exception as exc:
                future.set_exception(exc)
        return future.result()


def get_collection_reranker(col_config: dict) -> RerankerProvider | None:
    """Create a reranker provider for a specific collection, falling back to global."""
    from src.services import services

    # 1. Check for per-collection provider ID reference
    provider_id = col_config.get("rerank_provider_id")
    if provider_id:
        provider_cfg = _find_provider_by_id(services.config.rerank.providers, provider_id)
        if provider_cfg:
            return create_reranker_provider(provider_cfg)

    # 2. Fall back to global default
    global_default = services.config.rerank.default
    if global_default:
        return create_reranker_provider(global_default)

    return None


def get_embedding_overrides(collections: list[str]) -> dict[str, EmbeddingProvider | None]:
    """Build per-collection embedding providers for a list of collections.

    When services are not initialized (unit tests / early boot), returns
    ``None`` overrides so callers can fall back to the default retriever
    embedding instead of raising.
    """
    from src.services import services

    overrides: dict[str, EmbeddingProvider | None] = {}
    shared = {}
    for col in collections:
        try:
            if services.db is None or services.config is None:
                overrides[col] = None
                continue
            cc = services.db.get_collection_config(col) or {}
            config = get_collection_embedding_config(cc, col)
            if not config:
                overrides[col] = UNAVAILABLE_EMBEDDING
                continue
            binding = EmbeddingBinding.from_provider(config)
            key = (binding.provider_id, binding.source, binding.model, binding.dimensions)
            if key not in shared:
                provider = create_embedding_provider(config)
                shared[key] = _QueryEmbedding(provider, binding) if provider else UNAVAILABLE_EMBEDDING
            overrides[col] = shared[key]
        except Exception as exc:
            logger.warning(
                "embedding override failed for col=%r: %s", col, exc
            )
            overrides[col] = UNAVAILABLE_EMBEDDING
    return overrides


def _resolve_collection_name(col_id: str) -> str:
    """Resolve a collection ID to its display name. Falls back to ID if not found."""
    from src.collections import store as cs
    meta = cs.get_collection_meta(col_id)
    if meta:
        return meta.get("name", col_id)
    return col_id


# build_context → migrated to src.rag.context_builder.build_context()
# retrieve_standard → migrated to DirectQueryModule._retrieve_normal
# retrieve_parent_child → migrated to DirectQueryModule._retrieve_parent_child
# retrieve_parent_child_multi → migrated to DirectQueryModule.retrieve
