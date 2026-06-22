from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


async def list_collections() -> str:
    """List all knowledge bases with their document chunk counts.

    Use this first to discover available collections before querying, uploading, or managing documents.
    """
    from src.services import services

    def _run():
        names = [c for c in services.db.list_collections() if c != "__summaries__"]
        result = []
        for name in names:
            try:
                info = services.db.get_collection_info(name)
                result.append({"name": name, "points_count": info.get("points_count", 0)})
            except Exception:
                result.append({"name": name, "points_count": 0})
        return result

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _run)
    return json.dumps(data, ensure_ascii=False)


async def create_collection(
    name: str,
    dimensions: int = 1024,
    chunk_mode: str = "normal",
    parent_strategy: str = "paragraph",
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    buffer_ratio: float = 0.5,
    parent_chunk_size: int = 1024,
    parent_chunk_overlap: int = 128,
    child_chunk_size: int = 128,
    child_chunk_overlap: int = 32,
    search_mode: str = "dense",
    sparse_llm_tokenize: bool = True,
    contextual_enabled: bool = True,
    contextual_window: int = 1,
    agent_enabled: bool = True,
    agent_max_iterations: int = 3,
    embedding_provider_id: str | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key: str | None = None,
    embedding_batch_size: int | None = None,
    rerank_provider_id: str | None = None,
    rerank_provider: str | None = None,
    rerank_model: str | None = None,
    rerank_base_url: str | None = None,
    rerank_api_key: str | None = None,
    rerank_top_k: int = 5,
    allowed_file_types: list[str] | None = None,
) -> str:
    """Create a new knowledge base (collection).

    Only `name` is required — all other parameters have sensible defaults.
    Use `update_collection_config` to adjust settings after creation.
    """
    from src.services import services

    def _run():
        if services.db.collection_exists(name):
            return {"error": f"Collection '{name}' already exists"}
        if not services.embedding:
            return {"error": "Embedding provider not configured"}

        chunk_config = {
            "chunk_mode": chunk_mode,
            "parent_strategy": parent_strategy,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "buffer_ratio": buffer_ratio,
            "parent_chunk_size": parent_chunk_size,
            "parent_chunk_overlap": parent_chunk_overlap,
            "child_chunk_size": child_chunk_size,
            "child_chunk_overlap": child_chunk_overlap,
            "search_mode": search_mode,
            "sparse_llm_tokenize": sparse_llm_tokenize,
            "contextual_enabled": contextual_enabled,
            "contextual_window": contextual_window,
            "agent_enabled": agent_enabled,
            "agent_max_iterations": agent_max_iterations,
            "rerank_top_k": rerank_top_k,
        }
        if embedding_provider_id is not None:
            chunk_config["embedding_provider_id"] = embedding_provider_id
        if embedding_provider is not None:
            chunk_config["embedding_provider"] = embedding_provider
        if embedding_model is not None:
            chunk_config["embedding_model"] = embedding_model
        if embedding_base_url is not None:
            chunk_config["embedding_base_url"] = embedding_base_url
        if embedding_api_key is not None:
            chunk_config["embedding_api_key"] = embedding_api_key
        if embedding_batch_size is not None:
            chunk_config["embedding_batch_size"] = embedding_batch_size
        if rerank_provider_id is not None:
            chunk_config["rerank_provider_id"] = rerank_provider_id
        if rerank_provider is not None:
            chunk_config["rerank_provider"] = rerank_provider
        if rerank_model is not None:
            chunk_config["rerank_model"] = rerank_model
        if rerank_base_url is not None:
            chunk_config["rerank_base_url"] = rerank_base_url
        if rerank_api_key is not None:
            chunk_config["rerank_api_key"] = rerank_api_key
        if allowed_file_types is not None:
            chunk_config["allowed_file_types"] = allowed_file_types

        services.db.create_collection(name, vector_size=dimensions, chunk_config=chunk_config)
        return {"message": f"Collection '{name}' created", "dimensions": dimensions}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False)


async def get_collection_config(collection: str) -> str:
    """Get the full configuration of a collection — chunking strategy, search mode, embedding and reranker settings.

    Use this before `update_collection_config` to see current values.
    """
    from src.services import services

    def _run():
        if not services.db.collection_exists(collection):
            return {"error": f"Collection '{collection}' does not exist"}
        return services.db.get_collection_config(collection)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


async def update_collection_config(
    collection: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    buffer_ratio: float | None = None,
    parent_strategy: str | None = None,
    parent_chunk_size: int | None = None,
    parent_chunk_overlap: int | None = None,
    child_chunk_size: int | None = None,
    child_chunk_overlap: int | None = None,
    search_mode: str | None = None,
    sparse_llm_tokenize: bool | None = None,
    contextual_enabled: bool | None = None,
    contextual_window: int | None = None,
    agent_enabled: bool | None = None,
    agent_max_iterations: int | None = None,
    embedding_provider_id: str | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key: str | None = None,
    embedding_batch_size: int | None = None,
    rerank_provider_id: str | None = None,
    rerank_provider: str | None = None,
    rerank_model: str | None = None,
    rerank_base_url: str | None = None,
    rerank_api_key: str | None = None,
    rerank_top_k: int | None = None,
    allowed_file_types: list[str] | None = None,
) -> str:
    """Update a collection's configuration. Only provided fields are changed; null values are ignored.

    Use `get_collection_config` first to see current settings.
    """
    from src.services import services

    def _run():
        if not services.db.collection_exists(collection):
            return {"error": f"Collection '{collection}' does not exist"}

        updates = {}
        for k, v in {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "buffer_ratio": buffer_ratio,
            "parent_strategy": parent_strategy,
            "parent_chunk_size": parent_chunk_size,
            "parent_chunk_overlap": parent_chunk_overlap,
            "child_chunk_size": child_chunk_size,
            "child_chunk_overlap": child_chunk_overlap,
            "search_mode": search_mode,
            "sparse_llm_tokenize": sparse_llm_tokenize,
            "contextual_enabled": contextual_enabled,
            "contextual_window": contextual_window,
            "agent_enabled": agent_enabled,
            "agent_max_iterations": agent_max_iterations,
            "embedding_provider_id": embedding_provider_id,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "embedding_base_url": embedding_base_url,
            "embedding_api_key": embedding_api_key,
            "embedding_batch_size": embedding_batch_size,
            "rerank_provider_id": rerank_provider_id,
            "rerank_provider": rerank_provider,
            "rerank_model": rerank_model,
            "rerank_base_url": rerank_base_url,
            "rerank_api_key": rerank_api_key,
            "rerank_top_k": rerank_top_k,
            "allowed_file_types": allowed_file_types,
        }.items():
            if v is not None:
                updates[k] = v

        if not updates:
            return {"message": "No changes provided"}

        result = services.db.update_collection_config(collection, updates)
        if "error" in result:
            return result
        return {"message": f"Collection '{collection}' config updated", "config": result}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


async def delete_collection(collection: str) -> str:
    """Permanently delete a collection and all its documents.

    Fails if it's the only remaining collection. Consider using `delete_document` to remove specific files instead.
    """
    from src.services import services

    def _run():
        if not services.db.collection_exists(collection):
            return {"error": f"Collection '{collection}' does not exist"}
        collections = [c for c in services.db.list_collections() if c != "__summaries__"]
        if len(collections) <= 1:
            return {"error": "Cannot delete the only remaining collection"}
        services.db.delete_collection(collection)
        return {"message": f"Collection '{collection}' deleted"}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False)
