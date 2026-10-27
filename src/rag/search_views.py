"""search 的 **Agent 视图函数**（批次 4：REST 新端点与 MCP 工具共用）。

`direct_search_view` / `agentic_search_view` 是两个检索工具（Agent 的
PRIMARY 路径）的信封构造：参数校验（库存在 + 归档闸门）→ 走 services 的
检索管线 → chunk → dict。**同步阻塞函数**——调用方必须带上下文跑线程
（MCP 侧 ``run_with_context``；REST 侧 ``run_in_threadpool``——anyio 会
复制 contextvars，身份可见性过滤不失效，见 src/async_ctx.py 的教训）。

``_chunk_to_dict`` 从 MCP 工具模块迁来（工具侧反向 import）。
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp.common import err, require_collection

logger = logging.getLogger("rag.search_views")


"""下面三个函数（chunk_to_dict 及其依赖）与 MCP 工具侧同一份——从工具
模块迁来，工具侧保留别名 import，防止两处实现漂移。"""


def _file_id_from_source(source: str) -> str:
    src = (source or "").strip()
    if src.startswith("__file__:"):
        return src[len("__file__:") :].strip()
    if src.startswith("file:"):
        return src[len("file:") :].strip()
    return ""


def _chunk_filename(collection: str, source: str, meta: dict) -> str:
    """Human file name for MCP hits — never a raw ``__file__:{id}`` key."""
    from src.collections.file_index import is_opaque_source_key, resolve_display_name

    payload = meta.get("source_label") or meta.get("display_name") or ""
    pl = str(payload).strip()
    if pl and is_opaque_source_key(pl):
        pl = ""
    if collection and source:
        try:
            name = resolve_display_name(
                str(collection), str(source), payload_label=pl or None
            )
            if name and not is_opaque_source_key(name):
                return name
        except Exception:
            logger.debug("resolve_display_name failed for %s", source, exc_info=True)
    if pl:
        return pl
    src = str(source or "")
    if src and not is_opaque_source_key(src):
        return src.replace("\\", "/").rsplit("/", 1)[-1]
    return ""


def _chunk_to_dict(c) -> dict:
    """Normalize a RetrievedChunk to a plain dict."""
    meta = getattr(c, "metadata", None) or {}
    source = meta.get("source", "") or ""
    collection = meta.get("collection", "") or ""
    file_id = (meta.get("file_id") or "").strip() or _file_id_from_source(source)
    return {
        "text": c.text,
        "score": c.score,
        "source": source,
        "file_id": file_id,
        "filename": _chunk_filename(collection, source, meta),
        "collection": collection,
        "chunk_type": meta.get("chunk_type", "normal"),
        "context": meta.get("context"),
        "id": meta.get("id", ""),
        "images": meta.get("images", []),
    }


def _archived_gate(target_collections: list[str], include_archived: bool) -> dict[str, Any] | None:
    if not include_archived:
        from src.collections.store import archived_collection_ids

        hit = [c for c in target_collections if c in archived_collection_ids()]
        if hit:
            return err(
                f"Collection '{hit[0]}' is archived. Pass include_archived=true "
                "only when the user explicitly asks for archived content."
            )
    return None


def direct_search_view(
    query: str,
    collection: str = "default",
    collections: list[str] | None = None,
    search_mode: str = "dense",
    top_k: int = 10,
    rerank_top_k: int = 5,
    use_reranker: bool = False,
    min_score: float = 0.0,
    include_images: bool = False,
    include_archived: bool = False,
) -> dict[str, Any]:
    """`search_direct_chunks` 工具的信封（校验 + retrieve + images 提取）。"""
    from src.services import services
    from src.rag.collection_utils import get_embedding_overrides

    target_collections = list(collections or [collection])
    for c in target_collections:
        if e := require_collection(c):
            return e
    if e := _archived_gate(target_collections, include_archived):
        return e
    if not services.direct_query:
        return {"error": "Direct query module not available"}

    embedding_overrides = get_embedding_overrides(target_collections)

    dq_result = services.direct_query.retrieve(
        query, target_collections, top_k=top_k,
        search_mode=search_mode,
        rerank_enabled=use_reranker,
        rerank_top_k=rerank_top_k,
        min_score=min_score,
        embedding_overrides=embedding_overrides,
    )

    payload = {
        "results": [_chunk_to_dict(c) for c in dq_result.chunks],
        "query_used": query,
        "total": len(dq_result.chunks),
    }
    if include_images:
        from src.mcp.tools.search import _extract_images_base64

        payload["images"] = _extract_images_base64(dq_result.chunks)
    return payload


def agentic_search_view(
    query: str,
    include_images: bool = False,
    include_archived: bool = False,
) -> dict[str, Any]:
    """`search_agentic_chunks` 工具的信封（agentic 管线 + tasks 透明化轨迹）。"""
    from src.services import services

    if not services.agentic_query:
        return {"error": "Agentic query service not available"}

    # generate_answer=False: we only want chunks, not a generated answer.
    result = services.agentic_query.run(
        query, generate_answer=False, include_images=include_images,
        include_archived=include_archived,
    )

    chunks = result.all_chunks or []
    payload = {
        "results": [_chunk_to_dict(c) for c in chunks],
        "query_used": query,
        "total": len(chunks),
        "tasks": result.tasks or [],
    }
    if include_images and result.images:
        payload["images"] = result.images
    return payload


__all__ = ["_chunk_to_dict", "agentic_search_view", "direct_search_view"]
