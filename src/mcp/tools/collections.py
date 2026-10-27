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

from mcp.server.fastmcp import Context

from src.mcp.common import (
    module_gated,
    err,
    filter_destructive_fields,
    ok,
    require_collection,
    run_sync,
    to_json,
)
from src.hub.confirm import confirm_destructive
from src.hub.routing import _require_collection_on_remote, body_without, hub_routed

logger = logging.getLogger(__name__)


# ── list_collections ─────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/agent/collections",
    forwards=(),
)
async def list_collections(ctx: Context | None = None) -> str:
    """List all collections with rich metadata for agent discovery.

    Each entry includes an ``id`` field (e.g. ``"col_abc123"`` or ``"default"``).
    **All other tools require this collection ``id``, NOT the display ``name``.**
    Call this first to discover collection IDs before any other operation.

    Each entry includes:
    - ``id`` / ``name`` / ``qdrant_name``
    - ``archived`` — True when the user archived this collection (its content
      is excluded from search unless the search tool gets ``include_archived=true``)
    - ``points_count`` — number of indexed chunks
    - ``notes_count`` — number of notes in this collection
    - ``last_active`` — ISO timestamp of last activity (most recent task)
    - ``catalog_definition`` — the 2-sentence project description (if any)
    - ``created_at`` / ``updated_at``
    """
    def _run() -> list[dict[str, Any]]:
        from src.collections_views import collections_list_view

        return collections_list_view()

    return to_json(await run_sync(_run))


# ── get_collection ────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/agent/collections/{collection}",
    precheck=_require_collection_on_remote,
    forwards=("collection",),
)
async def get_collection(collection: str, ctx: Context | None = None) -> str:
    """Get a collection's complete info: metadata + config + stats + catalog definition.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"`` or
    ``"default"``), NOT the display name. Use :func:`list_collections` first
    to get IDs.
    """
    def _run() -> dict[str, Any]:
        from src.collections_views import collection_info_view

        return collection_info_view(collection)

    return to_json(await run_sync(_run))


# ── create_collection ────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="POST",
    path="/collections",
    # REST 的 CollectionCreateRequest 与工具签名**逐参数同名**；成功/错误形状
    # 也与 ok()/err() 逐字段一致（REST 重复名/未配 embedding 都回 {"error": …}
    # 且 HTTP 200）——所以既不用 body 改名也不用 shape 整形，透传即忠实。
    #（wrapper 已剔除 ctx；未传的参数不进 body，REST 按同值默认补。）
    body=dict,
    forwards=(
        "name", "dimensions", "chunk_mode", "parent_strategy",
        "chunk_size", "chunk_overlap", "buffer_ratio",
        "parent_chunk_size", "parent_chunk_overlap",
        "child_chunk_size", "child_chunk_overlap",
        "search_mode", "sparse_llm_tokenize",
        "contextual_enabled", "contextual_window",
        "agent_enabled", "agent_max_iterations",
        "embedding_provider_id", "embedding_provider", "embedding_model",
        "embedding_base_url", "embedding_api_key", "embedding_batch_size",
        "rerank_provider_id", "rerank_provider", "rerank_model",
        "rerank_base_url", "rerank_api_key", "rerank_top_k",
        "allowed_file_types",
    ),
)
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
    ctx: Context | None = None,
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
        # RBAC：闸门必须在副作用之前（与 HTTP 侧同一修法）。否则被拒者仍留孤儿空集合。
        from src.identity import authorize, get_actor

        authorize(get_actor(), "collection.create", {"collection_id": collection_id})
        services.db.create_collection(collection_id, vector_size=dimensions, chunk_config=chunk_config)
        cstore.create_collection_meta(collection_id, name, qdrant_name=collection_id)
        return ok(
            id=collection_id,
            message=f"Collection '{name}' created",
            dimensions=dimensions,
        )

    return to_json(await run_sync(_run))


# ── update_collection_config ─────────────────────────────────


def _cfg_update_shape(payload: Any, args: dict[str, Any]) -> Any:
    """REST 响应 → 工具信封。

    REST 有三种形态：带 config 的成功（要补 ``updated_fields`` + 换成工具的
    message 文案）、"No changes provided"、``{"error": …}``（透传，与 err() 同形）。
    ``updated_fields`` 由「调用方传了哪些非空字段」算——与本地差一点：本地只列
    **值真的变了**的字段（它读得到旧值），转发侧多一次 GET 才能做到，为一个
    纯报告字段不值得；差异是「多列了没变化的字段」，不会误导写入结果本身。
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return payload
    if "config" not in payload:
        return {"message": payload.get("message") or "No changes provided"}
    collection = str(args.get("collection") or "")
    updated = sorted(
        k for k, v in args.items() if k != "collection" and v is not None
    )
    return {
        "message": f"Collection '{collection}' config updated",
        "updated_fields": updated,
        "config": payload.get("config"),
    }


@module_gated("chat", "database")
@hub_routed(
    method="PUT",
    path="/collections/{collection}/config",
    # REST 的 schema 是工具签名的超集且逐字段同名；只透传工具**有的**字段
    # （工具签名本来就只暴露安全字段，filter_destructive_fields 在两侧都过）。
    # 剔除已进路径的 collection（pydantic 会忽略多余键，但别依赖那种宽容）
    body=body_without("collection"),
    shape=_cfg_update_shape,
    precheck=_require_collection_on_remote,
    forwards=(
        "collection",
        "chunk_size", "chunk_overlap", "buffer_ratio", "parent_strategy",
        "parent_chunk_size", "parent_chunk_overlap",
        "child_chunk_size", "child_chunk_overlap",
        "search_mode", "sparse_llm_tokenize",
        "contextual_enabled", "contextual_window",
        "agent_enabled", "agent_max_iterations",
        "embedding_provider_id", "rerank_top_k", "allowed_file_types",
    ),
)
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
    ctx: Context | None = None,
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
        # 脱敏：合并后的 config 含已存明文 api_key，直接回发等于把密钥送出去
        from src.secrets import redact_mapping

        return ok(
            message=f"Collection '{collection}' config updated",
            updated_fields=list(safe_updates.keys()),
            config=redact_mapping(result),
        )

    return to_json(await run_sync(_run))


# ── delete_collection ────────────────────────────────────────


@module_gated("chat", "database")
@confirm_destructive()
@hub_routed(
    method="DELETE",
    path="/collections/{collection}",
    precheck=_require_collection_on_remote,
    # REST 的两种错误（库不存在 / 只剩最后一个）都以 {"error": …} + HTTP 200
    # 回，恰好是工具 err() 的形状 → 透传即同形。成功 {"message"} 换成工具文案
    # （REST 用**显示名**、工具用 **id**，Agent 手里拿的是 id）。
    shape=lambda payload, a: (
        payload
        if not isinstance(payload, dict) or payload.get("error")
        else {"message": f"Collection '{a.get('collection')}' deleted"}
    ),
    forwards=("collection",),
)
async def delete_collection(collection: str, ctx: Context | None = None) -> str:
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
        from src.meeting.transcript_index import INTERNAL_QDRANT_COLLECTIONS

        collections = [c for c in services.db.list_collections() if c not in INTERNAL_QDRANT_COLLECTIONS]
        if len(collections) <= 1:
            return err("Cannot delete the only remaining collection")
        # RBAC：可见性 + 删除档授权，必须在破坏之前
        from src.identity import authorize, get_actor
        from src.identity import visibility as _vis

        _vis.assert_can_read(get_actor(), collection)
        authorize(get_actor(), "collection.delete", {"collection_id": collection})
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
