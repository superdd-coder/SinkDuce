"""MCP collection management tools.

5 atomic tools:
- :func:`list_collections` — discover collections, with rich metadata
- :func:`get_collection` — full info (metadata + config + stats + catalog definition)
- :func:`create_collection` — create new collection
- :func:`update_collection_config` — update **safe** config fields only
- :func:`delete_collection` — delete a collection (refuses if last one)

Destructive field guard
-----------------------
``update_collection_config`` rejects any attempt to change
``chunk_mode`` / ``embedding_*`` (anything that would invalidate indexed
data or require a full re-index). Use the UI for destructive changes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp.common import (
    err,
    filter_destructive_fields,
    ok,
    require_collection,
    run_sync,
    to_json,
)

logger = logging.getLogger(__name__)


# ── list_collections ─────────────────────────────────────────


async def list_collections() -> str:
    """List all collections with rich metadata for agent discovery.

    Each entry includes an ``id`` field (e.g. ``"col_abc123"`` or ``"default"``).
    **All other tools require this collection ``id``, NOT the display ``name``.**
    Call this first to discover collection IDs before any other operation.

    Each entry includes:
    - ``id`` / ``name`` / ``qdrant_name``
    - ``points_count`` — number of indexed chunks
    - ``notes_count`` — number of notes in this collection
    - ``last_active`` — ISO timestamp of last activity (most recent task)
    - ``catalog_definition`` — the 2-sentence project description (if any)
    - ``created_at`` / ``updated_at``
    """
    def _run() -> list[dict[str, Any]]:
        from src.collections import store as cstore
        from src.services import services
        from src.notes import store as nstore
        from src.tasks import task_manager
        from src.rag.summary_manager import SummaryManager

        names = [c for c in services.db.list_collections() if c != "__summaries__"]
        sm = SummaryManager(db=services.db)

        # Build notes-count map (cheap: O(notes) once, not per-collection)
        notes_by_collection: dict[str, int] = {}
        for n in nstore.list_notes():
            notes_by_collection[n.collection] = notes_by_collection.get(n.collection, 0) + 1

        # Build last-active map from task manager
        last_active_by_collection: dict[str, Any] = {}
        for tid, (ttype, _args) in task_manager._task_args.items():
            t = task_manager.tasks.get(tid)
            if not t:
                continue
            ts = t.completed_at or t.started_at or t.created_at
            cur = last_active_by_collection.get(t.collection)
            if cur is None or ts > cur:
                last_active_by_collection[t.collection] = ts

        result: list[dict[str, Any]] = []
        for cid in names:
            meta = cstore.get_collection_meta(cid) or {}
            if not meta:
                meta = {"id": cid, "name": cid, "qdrant_name": cid}
            try:
                info = services.db.get_collection_info(cid)
                points_count = info.get("points_count", 0)
            except Exception:
                points_count = 0
            try:
                desc = sm.get_project_description(cid) or ""
            except Exception:
                desc = ""
            result.append({
                **meta,
                "points_count": points_count,
                "notes_count": notes_by_collection.get(cid, 0),
                "last_active": (
                    last_active_by_collection[cid].isoformat()
                    if cid in last_active_by_collection else None
                ),
                "catalog_definition": desc,
            })
        return result

    return to_json(await run_sync(_run))


# ── get_collection ────────────────────────────────────────────


async def get_collection(collection: str) -> str:
    """Get a collection's complete info: metadata + config + stats + catalog definition.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"`` or
    ``"default"``), NOT the display name. Use :func:`list_collections` first
    to get IDs.
    """
    def _run() -> dict[str, Any]:
        from src.collections import store as cstore
        from src.services import services
        from src.notes import store as nstore
        from src.rag.summary_manager import SummaryManager

        if e := require_collection(collection):
            return e
        meta = cstore.get_collection_meta(collection) or {
            "id": collection, "name": collection, "qdrant_name": collection,
        }
        config = services.db.get_collection_config(collection)
        config.pop("sparse_vocab", None)  # internal BM25 state, not useful to consumers
        try:
            info = services.db.get_collection_info(collection)
            points_count = info.get("points_count", 0)
        except Exception:
            points_count = 0
        notes_count = sum(
            1 for n in nstore.list_notes() if n.collection == collection
        )
        sm = SummaryManager(db=services.db)
        try:
            desc = sm.get_project_description(collection) or ""
        except Exception:
            desc = ""
        return ok(
            **meta,
            config=config,
            points_count=points_count,
            notes_count=notes_count,
            catalog_definition=desc,
        )

    return to_json(await run_sync(_run))


# ── create_collection ────────────────────────────────────────


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
    """Create a new collection.

    Only ``name`` is required. Embedding parameters may only be set at
    creation time (they are destructive and cannot be changed later
    without a full re-index).
    """
    def _run() -> dict[str, Any]:
        from src.services import services
        from src.collections import store as cstore

        if services.db.collection_exists(name):
            return err(f"Collection '{name}' already exists")
        if not services.embedding:
            return err("Embedding provider not configured")

        chunk_config: dict[str, Any] = {
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

        collection_id = cstore.generate_id()
        services.db.create_collection(collection_id, vector_size=dimensions, chunk_config=chunk_config)
        cstore.create_collection_meta(collection_id, name, qdrant_name=collection_id)
        return ok(
            id=collection_id,
            message=f"Collection '{name}' created",
            dimensions=dimensions,
        )

    return to_json(await run_sync(_run))


# ── update_collection_config ─────────────────────────────────


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
    rerank_top_k: int | None = None,
    allowed_file_types: list[str] | None = None,
) -> str:
    """Update a collection's **safe** configuration.

    ``collection`` must be a collection **ID** from :func:`list_collections`
    (e.g. ``"col_abc123"``), NOT the display name.

    Destructive fields (``chunk_mode``, ``embedding_*`` other than
    ``embedding_provider_id``) are rejected. Changes only take effect
    for newly uploaded files.
    """
    def _run() -> dict[str, Any]:
        from src.services import services

        if e := require_collection(collection):
            return e

        candidate: dict[str, Any] = {
            k: v for k, v in {
                "chunk_size": chunk_size, "chunk_overlap": chunk_overlap,
                "buffer_ratio": buffer_ratio,
                "parent_strategy": parent_strategy,
                "parent_chunk_size": parent_chunk_size,
                "parent_chunk_overlap": parent_chunk_overlap,
                "child_chunk_size": child_chunk_size, "child_chunk_overlap": child_chunk_overlap,
                "search_mode": search_mode, "sparse_llm_tokenize": sparse_llm_tokenize,
                "contextual_enabled": contextual_enabled, "contextual_window": contextual_window,
                "agent_enabled": agent_enabled, "agent_max_iterations": agent_max_iterations,
                "embedding_provider_id": embedding_provider_id,
                "rerank_top_k": rerank_top_k, "allowed_file_types": allowed_file_types,
            }.items() if v is not None
        }

        safe_updates, rejected = filter_destructive_fields(candidate)
        if rejected:
            return err(
                f"Refused to update destructive fields {rejected!r}. "
                "These require a full re-index and must be changed via the UI.",
                rejected_fields=rejected,
            )
        if not safe_updates:
            return ok(message="No changes provided")

        result = services.db.update_collection_config(collection, safe_updates)
        if "error" in result:
            return result
        return ok(
            message=f"Collection '{collection}' config updated",
            updated_fields=list(safe_updates.keys()),
            config=result,
        )

    return to_json(await run_sync(_run))


# ── delete_collection ────────────────────────────────────────


async def delete_collection(collection: str) -> str:
    """Permanently delete a collection and all its documents.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.
    Fails if it's the only remaining collection.
    """
    def _run() -> dict[str, Any]:
        from src.services import services
        from src.collections import store as cstore
        from src.rag.summary_manager import SummaryManager

        if e := require_collection(collection):
            return e
        collections = [c for c in services.db.list_collections() if c != "__summaries__"]
        if len(collections) <= 1:
            return err("Cannot delete the only remaining collection")
        services.db.delete_collection(collection)
        cstore.delete_collection_meta(collection)
        try:
            sm = SummaryManager(services.db)
            sm.delete_project_description(collection)
            sm.delete_collection_summary(collection)
            sm.delete_conflicts(collection)
        except Exception as e:
            logger.warning("Failed to clean up __summaries__ for %s: %s", collection, e)
        return ok(message=f"Collection '{collection}' deleted")

    return to_json(await run_sync(_run))


__all__ = [
    "list_collections",
    "get_collection",
    "create_collection",
    "update_collection_config",
    "delete_collection",
]
