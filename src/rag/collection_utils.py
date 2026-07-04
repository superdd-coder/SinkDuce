"""Shared utilities for per-collection embedding, parent-child retrieval, and context building."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from src.config import EmbeddingProviderConfig
from src.providers.base import EmbeddingProvider, RerankerProvider
from src.providers.embedding import create_embedding_provider
from src.providers.reranker import create_reranker_provider
from src.rag.retriever import RetrievedChunk


def _find_provider_by_id(providers: list, provider_id: str):
    """Find a provider config by ID from a list."""
    return next((p for p in providers if p.id == provider_id), None)


def get_collection_embedding(col_config: dict, collection: str = "") -> EmbeddingProvider | None:
    """Create an embedding provider for a specific collection, falling back to global."""
    from src.services import services

    actual_dim = services.db.get_vector_size(collection) if collection else None

    # 1. Check for per-collection provider ID reference
    provider_id = col_config.get("embedding_provider_id")
    if provider_id:
        provider_cfg = _find_provider_by_id(services.config.embedding.providers, provider_id)
        if provider_cfg:
            cfg = provider_cfg.model_copy()
            if actual_dim:
                cfg.dimensions = actual_dim
            return create_embedding_provider(cfg)

    # 2. Check for old-style per-collection override fields (backward compat)
    old_provider = col_config.get("embedding_provider")
    if old_provider and old_provider != "none":
        global_default = services.config.embedding.default
        # Check if we have valid credentials for this override
        has_credentials = bool(
            col_config.get("embedding_api_key")
            or (global_default and global_default.api_key)
        )
        # Only use old-style override if we have credentials or it's a local provider
        if has_credentials or old_provider == "local":
            dim = actual_dim or col_config.get("dimensions") or (global_default.dimensions if global_default else 512)
            cfg = EmbeddingProviderConfig(
                provider=old_provider,
                model=col_config.get("embedding_model") or (global_default.model if global_default else ""),
                base_url=col_config.get("embedding_base_url") or (global_default.base_url if global_default else ""),
                api_key=col_config.get("embedding_api_key") or (global_default.api_key if global_default else ""),
                dimensions=dim,
                batch_size=col_config.get("embedding_batch_size") or (global_default.batch_size if global_default else 10),
            )
            return create_embedding_provider(cfg)

    # 3. Fall back to global default
    global_default = services.config.embedding.default
    if global_default:
        cfg = global_default.model_copy()
        if actual_dim:
            if global_default.dimensions and actual_dim != global_default.dimensions:
                logger.warning(
                    "Collection %s has %d-dim vectors but global embedding provider "
                    "(%s, %d dims) will be overridden to %d. "
                    "Ensure the model %s supports Matryoshka/truncation to %d dims.",
                    collection, actual_dim, global_default.model,
                    global_default.dimensions, actual_dim,
                    global_default.model, actual_dim,
                )
            cfg.dimensions = actual_dim
        return create_embedding_provider(cfg)

    return None


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


def get_embedding_overrides(collections: list[str]) -> dict[str, EmbeddingProvider]:
    """Build per-collection embedding providers for a list of collections."""
    from src.services import services

    overrides = {}
    for col in collections:
        cc = services.db.get_collection_config(col)
        overrides[col] = get_collection_embedding(cc, col)
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
