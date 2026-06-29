from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body

from src.api.schemas import (
    CollectionCreateRequest,
    CollectionConfigUpdateRequest,
    CollectionInfo,
)
from src.collections import store as collections_store
from src.services import services
from src.tasks.task_manager import task_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/collections")
def list_collections():
    """List all collections with their metadata."""
    # Get existing Qdrant collections
    qdrant_names = [c for c in services.db.list_collections() if c != "__summaries__"]

    # Get or create metadata for each
    result = []
    seen_ids = set()
    for qdrant_name in qdrant_names:
        # Try to find by ID (new format)
        meta = collections_store.get_collection_meta(qdrant_name)
        if not meta:
            # Try to find by qdrant_name (handles renamed collections)
            meta = collections_store.find_collection_by_qdrant_name(qdrant_name)
        if not meta:
            # Try to find by display name (legacy)
            meta = collections_store.find_collection_by_name(qdrant_name)
        if not meta:
            # Legacy collection without metadata - create it with ID = qdrant_name
            meta = collections_store.create_collection_meta(qdrant_name, qdrant_name, qdrant_name=qdrant_name)

        # Skip duplicates (e.g., old "Original Name" and new "col_xxx" pointing to same display name)
        if meta["id"] in seen_ids:
            continue
        seen_ids.add(meta["id"])

        try:
            info = services.db.get_collection_info(qdrant_name)
            result.append({
                "id": meta["id"],
                "name": meta["name"],
                "points_count": info.get("points_count", 0),
            })
        except Exception:
            result.append({
                "id": meta["id"],
                "name": meta["name"],
                "points_count": 0,
            })

    return result


@router.post("/collections")
def create_collection(req: CollectionCreateRequest):
    """Create a new collection."""
    # Check for duplicate display name
    existing = collections_store.find_collection_by_name(req.name)
    if existing:
        return {"error": f"Collection '{req.name}' already exists"}
    logger.info("Creating collection: %s (dimensions=%s, mode=%s)", req.name, req.dimensions, req.chunk_mode)
    if not services.embedding:
        return {"error": "Embedding provider not configured. Please configure it in Settings first."}
    dimensions = req.dimensions or services.embedding.dimensions
    chunk_config = {
        "chunk_mode": req.chunk_mode,
        "parent_strategy": req.parent_strategy,
        "chunk_size": req.chunk_size,
        "chunk_overlap": req.chunk_overlap,
        "buffer_ratio": req.buffer_ratio,
        "parent_chunk_size": req.parent_chunk_size,
        "parent_chunk_overlap": req.parent_chunk_overlap,
        "child_chunk_size": req.child_chunk_size,
        "child_chunk_overlap": req.child_chunk_overlap,
        "search_mode": req.search_mode,
        "contextual_enabled": req.contextual_enabled,
        "contextual_window": req.contextual_window,
        "agent_enabled": req.agent_enabled,
        "agent_max_iterations": req.agent_max_iterations,
        "rerank_top_k": req.rerank_top_k,
    }
    if req.embedding_provider is not None:
        chunk_config["embedding_provider"] = req.embedding_provider
    if req.embedding_model is not None:
        chunk_config["embedding_model"] = req.embedding_model
    if req.embedding_base_url is not None:
        chunk_config["embedding_base_url"] = req.embedding_base_url
    if req.embedding_api_key is not None:
        chunk_config["embedding_api_key"] = req.embedding_api_key
    if req.embedding_batch_size is not None:
        chunk_config["embedding_batch_size"] = req.embedding_batch_size
    if req.rerank_provider is not None:
        chunk_config["rerank_provider"] = req.rerank_provider
    if req.rerank_model is not None:
        chunk_config["rerank_model"] = req.rerank_model
    if req.rerank_base_url is not None:
        chunk_config["rerank_base_url"] = req.rerank_base_url
    if req.rerank_api_key is not None:
        chunk_config["rerank_api_key"] = req.rerank_api_key
    if req.allowed_file_types is not None:
        chunk_config["allowed_file_types"] = req.allowed_file_types

    # Create metadata first to get ID
    collection_id = collections_store.generate_id()

    # Create in Qdrant using ID as name
    services.db.create_collection(collection_id, vector_size=dimensions, chunk_config=chunk_config)

    # Store metadata with display name and qdrant_name
    collections_store.create_collection_meta(collection_id, req.name, qdrant_name=collection_id)

    return {"id": collection_id, "message": f"Collection '{req.name}' created", "dimensions": dimensions}


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: str):
    """Delete a collection by ID."""
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    if not services.db.collection_exists(collection_id):
        return {"error": f"Collection '{meta['name']}' does not exist in database"}

    collections = [c for c in services.db.list_collections() if c != "__summaries__"]
    if len(collections) <= 1:
        return {"error": "Cannot delete the only remaining collection"}

    # Delete from Qdrant (using ID as name)
    services.db.delete_collection(collection_id)
    # Delete metadata
    collections_store.delete_collection_meta(collection_id)

    # Clean up __summaries__ entries for this collection
    try:
        from src.rag.summary_manager import SummaryManager
        sm = SummaryManager(services.db)
        sm.delete_project_description(collection_id)
        sm.delete_collection_summary(collection_id)
        sm.delete_conflicts(collection_id)
        logger.info("Cleaned up __summaries__ entries for collection: %s", collection_id)
    except Exception as e:
        logger.warning("Failed to clean up __summaries__ for %s: %s", collection_id, e)

    logger.info("Deleted collection: %s (%s)", meta["name"], collection_id)
    return {"message": f"Collection '{meta['name']}' deleted"}


@router.put("/collections/{collection_id}/rename")
def rename_collection(collection_id: str, body: dict = Body()):
    """Rename a collection (display name only)."""
    new_name = body.get("name")
    if not new_name or not new_name.strip():
        return {"error": "Name is required"}

    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    old_name = meta["name"]
    if old_name == new_name.strip():
        return {"message": "Name unchanged"}

    # Just update the display name, Qdrant name (ID) stays the same
    collections_store.update_collection_meta(collection_id, name=new_name.strip())

    logger.info("Renamed collection display: %s -> %s", old_name, new_name)
    return {"message": f"Collection renamed to '{new_name}'"}


@router.get("/collections/{collection_id}/info", response_model=CollectionInfo)
def collection_info(collection_id: str):
    """Get collection info by ID."""
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    # Use collection_id as Qdrant name
    info = services.db.get_collection_info(collection_id)
    return CollectionInfo(**info)


@router.get("/collections/{collection_id}/config")
def get_collection_config(collection_id: str):
    """Get collection config by ID."""
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    # Use collection_id as Qdrant name
    config = services.db.get_collection_config(collection_id)
    return config


@router.put("/collections/{collection_id}/config")
def update_collection_config(collection_id: str, req: CollectionConfigUpdateRequest):
    """Update collection config by ID."""
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    # Use collection_id as Qdrant name
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"message": "No changes provided"}

    result = services.db.update_collection_config(collection_id, updates)
    if "error" in result:
        return result

    return {"message": f"Collection config updated", "config": result}


@router.post("/collections/{collection_id}/sparse-recalc")
def trigger_sparse_recalc(collection_id: str):
    """Trigger a full sparse vocabulary recalculation for this collection."""
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    task = task_manager.create_task(
        filename=f"recalc:{collection_id}",
        task_type="sparse_recalc",
        collection=collection_id,
    )
    return {"message": "Sparse recalculation triggered", "task_id": task.id, "task_type": "sparse_recalc"}


# ══════════════════════════════════════════════════════════════════════════
# Catalog endpoints
# ══════════════════════════════════════════════════════════════════════════

@router.get("/collections/{collection_id}/catalog")
def get_catalog_entry(collection_id: str):
    """Get catalog entry (name, definition, coverage, tags) for a collection."""
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    config = services.db.get_collection_config(collection_id)
    # Read definition from consolidation result first
    definition = ""
    try:
        from src.rag.summary_manager import SummaryManager
        desc = SummaryManager(services.db).get_project_description(collection_id)
        if desc and desc.get("content"):
            import re
            definition = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', desc["content"])
    except Exception:
        pass
    if not definition:
        definition = config.get("project_description", "")
    return {
        "id": collection_id,
        "name": meta.get("name", collection_id),
        "definition": definition,
        "coverage": config.get("coverage", ""),
        "tags": config.get("tags", []),
    }


@router.put("/collections/{collection_id}/tags")
def update_collection_tags(collection_id: str, tags: list[str] = Body(...)):
    """Update tags for a collection."""
    if not services.catalog:
        return {"error": "Catalog service not available"}

    services.catalog.update_tags(collection_id, tags)
    return {"message": "Tags updated", "tags": tags}


@router.post("/collections/{collection_id}/coverage")
def refresh_coverage(collection_id: str):
    """Trigger a coverage refresh for a collection."""
    if not services.catalog:
        return {"error": "Catalog service not available"}

    # Check active uploads/doc_summaries; defer if any are running
    active = len(task_manager.get_active_tasks(
        collection=collection_id, task_types=["upload", "doc_summary"],
    ))
    if active > 0:
        services.catalog.mark_dirty(collection_id)
        return {"message": f"Coverage deferred ({active} active tasks)", "deferred": True}

    services.catalog.update_coverage(collection_id)
    config = services.db.get_collection_config(collection_id)
    return {"message": "Coverage refreshed", "coverage": config.get("coverage", "")}
