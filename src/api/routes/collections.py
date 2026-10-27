from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends

from src.api.schemas import (
    CollectionCreateRequest,
    CollectionConfigUpdateRequest,
    CollectionInfo,
)
from src.collections import store as collections_store
from src.services import services
from src.tasks.task_manager import task_manager
from src.file_mgmt.store import init_collection_db, delete_collection_db

logger = logging.getLogger(__name__)
# RBAC 守卫：这些端点直接以路径参数接收库 ID，之前完全没设防。
# 读档不足 → 404（与"不存在"同形）；可见但写档不足 → 403。
def _ccol(collection_id: str):
    from collections.abc import Mapping as _M

    from src.identity import get_actor
    from src.identity import visibility as _vis

    actor = get_actor()
    if actor is None:
        return None
    _vis.assert_can_read(actor, collection_id)  # 不可见 → 404
    return actor


def _gate_write(collection_id: str, action: str) -> None:
    """可见性 + 写档授权。**必须在任何副作用之前调用**。"""
    from src.identity import authorize

    actor = _ccol(collection_id)
    if actor is None:
        return
    authorize(actor, action, {"collection_id": collection_id})


from src.api.routes.identity import require_module

router = APIRouter(dependencies=[Depends(require_module("chat", "database"))])


def _emit_collection(event_type: str, collection_id: str) -> None:
    """Broadcast a collection change so peers refresh their cached list.

    Multi-device sync (see development_doc/2026-09-16-multi-device-state-sync-design.md §5).
    These handlers are sync `def`, so this runs on Starlette's threadpool; the
    event bus is queue-based and safe to publish to from any thread.
    """
    from src.events.emit import emit

    emit(event_type, scope={"collection_id": collection_id})


@router.get("/collections")
def list_collections():
    """List all collections with their metadata."""
    # Get existing Qdrant collections
    from src.meeting.transcript_index import INTERNAL_QDRANT_COLLECTIONS

    qdrant_names = [c for c in services.db.list_collections() if c not in INTERNAL_QDRANT_COLLECTIONS]

    # RBAC（2026-09-16）：库列表是最直接的枚举出口——不可见的库必须**不出现在响应里**
    # （不是前端过滤：那意味着数据已到客户端）。
    from src.identity import get_actor
    from src.identity import visibility as _visibility

    _actor = get_actor()
    try:
        qdrant_names = _visibility.filter_collection_ids(_actor, qdrant_names)
    except Exception:
        logger.exception("[Collections] visibility gate failed — returning empty list")
        return []

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

        # RBAC：每库附带四档开关，前端靠它决定「设置」入口是否画出来。
        # 只是化妆信号——真正的墙是各端点的 authorize（PUT config 要 config 档）。
        try:
            perms = _visibility.collection_permissions(_actor, meta["id"])
        except Exception:
            # fail-closed：拿不准就给全 False（少画入口，而不是多画）
            logger.exception("[Collections] permission probe failed for %s", meta["id"])
            perms = {t: False for t in ("read", "write", "delete", "config")}

        try:
            info = services.db.get_collection_info(qdrant_name)
            result.append({
                "id": meta["id"],
                "name": meta["name"],
                "points_count": info.get("points_count", 0),
                "archived": bool(meta.get("archived")),
                "permissions": perms,
            })
        except Exception:
            result.append({
                "id": meta["id"],
                "name": meta["name"],
                "points_count": 0,
                "archived": bool(meta.get("archived")),
                "permissions": perms,
            })

    # Library list: alphabetical by display name (case-insensitive)
    result.sort(key=lambda c: (c.get("name") or "").casefold())
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
        # 与 MCP create_collection 的本地实现同集（2026-09-18 批次 2 审查发现：
        # schema 收了这三个字段、这里却不落 —— 跨盒子建库会静默用默认值索引）
        "sparse_llm_tokenize": req.sparse_llm_tokenize,
        "contextual_enabled": req.contextual_enabled,
        "contextual_window": req.contextual_window,
        "agent_enabled": req.agent_enabled,
        "agent_max_iterations": req.agent_max_iterations,
        "rerank_top_k": req.rerank_top_k,
    }
    if req.embedding_provider_id is not None:
        chunk_config["embedding_provider_id"] = req.embedding_provider_id
    if req.rerank_provider_id is not None:
        chunk_config["rerank_provider_id"] = req.rerank_provider_id
    if req.embedding_provider is not None:
        chunk_config["embedding_provider"] = req.embedding_provider
    if req.embedding_model is not None:
        chunk_config["embedding_model"] = req.embedding_model
    if req.embedding_base_url is not None:
        chunk_config["embedding_base_url"] = req.embedding_base_url
    from src.secrets import skip_secret_write

    if req.embedding_api_key is not None and not skip_secret_write(
        "embedding_api_key", req.embedding_api_key
    ):
        chunk_config["embedding_api_key"] = req.embedding_api_key
    if req.embedding_batch_size is not None:
        chunk_config["embedding_batch_size"] = req.embedding_batch_size
    if req.rerank_provider is not None:
        chunk_config["rerank_provider"] = req.rerank_provider
    if req.rerank_model is not None:
        chunk_config["rerank_model"] = req.rerank_model
    if req.rerank_base_url is not None:
        chunk_config["rerank_base_url"] = req.rerank_base_url
    if req.rerank_api_key is not None and not skip_secret_write(
        "rerank_api_key", req.rerank_api_key
    ):
        chunk_config["rerank_api_key"] = req.rerank_api_key
    if req.allowed_file_types is not None:
        chunk_config["allowed_file_types"] = req.allowed_file_types
    # Persist explicitly so UI checkbox and ingest path share the same stored value
    # (not only via _DEFAULT_COLLECTION_CONFIG merge on read).
    chunk_config["cloud_parsing"] = bool(req.cloud_parsing)

    # Create metadata first to get ID
    collection_id = collections_store.generate_id()

    # 建库闸门（RBAC）：必须在**任何副作用之前**。
    # 曾经只靠 create_collection_meta 里的接缝，而它在 db.create_collection 之后
    # ——被拒的用户仍会留下一个孤儿空集合（2026-09-16 审计发现）。
    from src.identity import authorize, get_actor

    authorize(get_actor(), "collection.create", {"collection_id": collection_id})

    # Create in Qdrant using ID as name
    services.db.create_collection(collection_id, vector_size=dimensions, chunk_config=chunk_config)

    # Store metadata with display name and qdrant_name
    collections_store.create_collection_meta(collection_id, req.name, qdrant_name=collection_id)

    # Initialize per-collection file-management SQLite DB (Phase 1)
    try:
        init_collection_db(collection_id)
    except Exception as e:
        logger.error("Failed to init file-mgmt DB for %s: %s", collection_id, e)

    _emit_collection("collection.created", collection_id)
    return {"id": collection_id, "message": f"Collection '{req.name}' created", "dimensions": dimensions}


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: str):
    """Delete a collection by ID."""
    # 授权必须最早（破坏性操作：删 Qdrant 集合 + file-mgmt DB + 元数据）
    _gate_write(collection_id, "collection.delete")
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    if not services.db.collection_exists(collection_id):
        return {"error": f"Collection '{meta['name']}' does not exist in database"}

    from src.meeting.transcript_index import INTERNAL_QDRANT_COLLECTIONS

    collections = [c for c in services.db.list_collections() if c not in INTERNAL_QDRANT_COLLECTIONS]
    if len(collections) <= 1:
        return {"error": "Cannot delete the only remaining collection"}

    # Delete from Qdrant (using ID as name)
    services.db.delete_collection(collection_id)
    # Delete file-management SQLite DB
    delete_collection_db(collection_id)
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

    _emit_collection("collection.deleted", collection_id)
    logger.info("Deleted collection: %s (%s)", meta["name"], collection_id)
    return {"message": f"Collection '{meta['name']}' deleted"}


@router.post("/collections/{collection_id}/archive")
def archive_collection(collection_id: str):
    """Archive a collection: retrieval-level exclusion, data untouched."""
    _gate_write(collection_id, "collection.update")
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    updated = collections_store.set_archived(collection_id, True)
    if not updated:
        return {"error": f"Collection '{collection_id}' not found"}

    _emit_collection("collection.archived", collection_id)
    logger.info("Archived collection: %s (%s)", meta["name"], collection_id)
    return {"message": f"Collection '{meta['name']}' archived"}


@router.post("/collections/{collection_id}/unarchive")
def unarchive_collection(collection_id: str):
    """Restore an archived collection."""
    _gate_write(collection_id, "collection.update")
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    updated = collections_store.set_archived(collection_id, False)
    if not updated:
        return {"error": f"Collection '{collection_id}' not found"}

    _emit_collection("collection.unarchived", collection_id)
    logger.info("Unarchived collection: %s (%s)", meta["name"], collection_id)
    return {"message": f"Collection '{meta['name']}' unarchived"}


@router.put("/collections/{collection_id}/rename")
def rename_collection(collection_id: str, body: dict = Body()):
    """Rename a collection (display name only)."""
    _gate_write(collection_id, "collection.update")
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

    _emit_collection("collection.renamed", collection_id)
    logger.info("Renamed collection display: %s -> %s", old_name, new_name)
    return {"message": f"Collection renamed to '{new_name}'"}


@router.get("/collections/{collection_id}/info", response_model=CollectionInfo)
def collection_info(collection_id: str):
    """Get collection info by ID."""
    _ccol(collection_id)
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    # Use collection_id as Qdrant name
    info = services.db.get_collection_info(collection_id)
    return CollectionInfo(**info)


@router.get("/collections/{collection_id}/config")
def get_collection_config(collection_id: str):
    """Get collection config by ID."""
    _ccol(collection_id)
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    from src.secrets import redact_mapping

    # Use collection_id as Qdrant name
    config = services.db.get_collection_config(collection_id)
    return redact_mapping(config) if isinstance(config, dict) else config


@router.put("/collections/{collection_id}/config")
def update_collection_config(collection_id: str, req: CollectionConfigUpdateRequest):
    """Update collection config by ID."""
    _gate_write(collection_id, "collection.update")
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    from src.secrets import skip_secret_write

    # Use collection_id as Qdrant name
    updates = {
        k: v
        for k, v in req.model_dump().items()
        if v is not None and not skip_secret_write(k, v)
    }
    if not updates:
        return {"message": "No changes provided"}

    result = services.db.update_collection_config(collection_id, updates)
    if "error" in result:
        return result

    # 脱敏：update_collection_config 返回的是完整合并 config（含已存明文 api_key），
    # 直接回发等于把密钥送出去。GET 侧早已走 redact_mapping，这里是它的不对称面。
    from src.secrets import redact_mapping

    return {"message": "Collection config updated", "config": redact_mapping(result)}


@router.post("/collections/{collection_id}/sparse-recalc")
def trigger_sparse_recalc(collection_id: str):
    """Trigger a full sparse vocabulary recalculation for this collection."""
    _gate_write(collection_id, "collection.update")
    meta = collections_store.get_collection_meta(collection_id)
    if not meta:
        return {"error": f"Collection '{collection_id}' not found"}

    from src.tasks.handlers import _schedule_sparse_recalc
    if not _schedule_sparse_recalc(collection_id, reason="manual trigger"):
        return {"message": "A sparse recalculation is already queued or running", "task_type": "sparse_recalc", "already_active": True}
    return {"message": "Sparse recalculation triggered", "task_type": "sparse_recalc"}


# ══════════════════════════════════════════════════════════════════════════
# Catalog endpoints
# ══════════════════════════════════════════════════════════════════════════

@router.get("/collections/{collection_id}/catalog")
def get_catalog_entry(collection_id: str):
    """Get catalog entry (name, definition, coverage, tags) for a collection."""
    _ccol(collection_id)
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
    _gate_write(collection_id, "collection.update")
    if not services.catalog:
        return {"error": "Catalog service not available"}

    services.catalog.update_tags(collection_id, tags)
    return {"message": "Tags updated", "tags": tags}


@router.post("/collections/{collection_id}/coverage")
def refresh_coverage(collection_id: str):
    """Trigger a coverage refresh for a collection."""
    _gate_write(collection_id, "collection.update")
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
