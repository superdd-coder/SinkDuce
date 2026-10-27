"""MCP search tools — context-only retrieval.

Two atomic retrieval tools, both return chunks (no LLM answer generation —
the agent generates the answer from the chunks using its own LLM):

- :func:`search_direct_chunks` — Dense / Sparse / Hybrid + optional rerank.
  Bypasses agentic decomposition. Use for focused lookups.

- :func:`search_agentic_chunks` — Full Agentic RAG pipeline (rewrite →
  decompose → retrieve → grade → aggregate). Returns the same chunk
  structure as ``search_direct_chunks`` so the agent can format it
  however it wants. Use for complex / multi-hop questions.

Plus one operational tool:
- :func:`get_query_history` — recent questions, answers, and sources
  from past RAG queries.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

import asyncio
import json
import logging
from pathlib import Path

from src.hub.routing import hub_routed
from src.mcp.common import err, ok, require_collection, to_json, module_gated

# 批次4迁移：与 REST 端点共用同一份（既有测试仍从这里 import，保留再导出）
from src.rag.search_views import (  # noqa: F401
    _chunk_filename,
    _chunk_to_dict,
    _file_id_from_source,
)

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────


def _extract_images_base64(chunks: list) -> dict[str, dict]:
    """Extract and encode images referenced in chunk metadata."""
    from src.parsers.image_utils import encode_image_base64

    images: dict[str, dict] = {}
    seen: set[str] = set()
    for chunk in chunks:
        meta = getattr(chunk, "metadata", {}) if hasattr(chunk, "metadata") else {}
        chunk_images = meta.get("images", [])
        if not isinstance(chunk_images, list):
            continue
        for img_ref in chunk_images:
            if not isinstance(img_ref, dict):
                continue
            img_id = img_ref.get("image_id", "")
            file_id = img_ref.get("file_id", "")
            if not img_id or not file_id or img_id in seen:
                continue
            seen.add(img_id)
            encoded = encode_image_base64(img_id, file_id)
            if encoded:
                b64, mime = encoded
                images[img_id] = {"base64": b64, "mime": mime}
    return images


# ── search_direct_chunks ──────────────────────────────────────


@module_gated("chat")
@hub_routed(
    method="POST",
    path="/search/direct",
    # 检索管线（嵌入 + BM25 + 可选重排）在远端跑，20s 默认超时不够。
    # 无需 precheck：远端端点自带库存在/归档/可见性全量校验，err dict 按
    # 200-with-error 约定原样回流（不会静默成功）。
    body=dict,
    timeout=90.0,
    forwards=(
        "query", "collection", "collections", "search_mode",
        "top_k", "rerank_top_k", "use_reranker", "min_score",
        "include_images", "include_archived",
    ),
)
async def search_direct_chunks(
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
    ctx: Context | None = None,
) -> str:
    """PRIMARY for content facts: direct chunk retrieval (no answer generation).

    **When to use**
    - User asks what documents say / factual Q&A and you do **not** know which
      file holds the answer.
    - Focused single-collection (or multi-id) lookup without rewrite/decompose.
    - Prefer this over browsing ``list_library_tree`` or ``get_timeline`` for
      content questions.

    **When not to use**
    - Already know ``file_id`` and need full text → ``get_document_text``.
    - Already know ``file_id`` and need its index slices → ``get_file_chunks``.
    - Complex multi-hop / rewrite needed → ``search_agentic_chunks``.
    - Only need folder layout → ``list_library_tree``; timeline → ``get_timeline``.

    **After search:** each hit has ``source`` (canonical ``__file__:{file_id}``),
    plus ``file_id`` and ``filename`` (display name). Prefer ``file_id`` on
    get_document_text / get_file_chunks rather than re-searching the same file.

    The agent should read returned chunks and answer with its own LLM if needed.

    Args:
        query: The user question / search string.
        collection: Collection **ID** from ``list_collections`` (e.g. ``"col_abc123"``
            or ``"default"``). Used when ``collections`` is not given.
        collections: List of collection **IDs** to search across. If given,
            overrides ``collection``. Use ``list_collections`` first to get IDs.
        search_mode: One of ``dense`` / ``sparse`` / ``hybrid``.
        top_k: Max chunks to return per collection.
        rerank_top_k: Number of chunks to keep after reranking.
        use_reranker: If True, apply the configured reranker.
        min_score: Drop chunks below this score (post-rerank if applicable).
        include_images: Embed base64-encoded images referenced in chunks.
        include_archived: Include collections the user has archived. Keep false
            for every normal search. Enable only when the user explicitly
            refers to the archived status (e.g. "the archived X", "X I
            archived"). A question that merely names a collection that happens
            to be archived does NOT qualify — answer from active collections,
            or tell the user the collection is archived and ask whether to
            search it.

    Returns:
        JSON ``{"results": [...chunks...], "query_used": str, "total": N,
        "images"?: {...}}``.
    """
    target_collections = collections or [collection]
    for c in target_collections:
        if e := require_collection(c):
            return to_json(e)

    if not include_archived:
        from src.collections.store import archived_collection_ids

        hit = [c for c in target_collections if c in archived_collection_ids()]
        if hit:
            return to_json(err(
                f"Collection '{hit[0]}' is archived. Pass include_archived=true "
                "only when the user explicitly asks for archived content."
            ))

    def _run():
        from src.rag.search_views import direct_search_view

        return direct_search_view(
            query,
            collection=collection,
            collections=collections,
            search_mode=search_mode,
            top_k=top_k,
            rerank_top_k=rerank_top_k,
            use_reranker=use_reranker,
            min_score=min_score,
            include_images=include_images,
            include_archived=include_archived,
        )

    # 必须保留请求上下文：`_run` 里读 get_actor() 做可见性过滤，裸 run_in_executor
    # 会让身份退回 owner → 隐藏库的过滤失效（见 src/async_ctx.py）。
    from src.async_ctx import run_with_context

    result = await run_with_context(_run)
    return json.dumps(result, ensure_ascii=False, default=str)


# ── search_agentic_chunks ─────────────────────────────────────


@module_gated("chat")
@hub_routed(
    method="POST",
    path="/search/agentic",
    # agentic 管线多轮（重写/分解/检索/评分）——20s 默认超时必炸，显式加长
    body=dict,
    timeout=120.0,
    forwards=("query", "include_images", "include_archived"),
)
async def search_agentic_chunks(
    query: str,
    include_images: bool = False,
    include_archived: bool = False,
    ctx: Context | None = None,
) -> str:
    """PRIMARY for complex content facts: agentic chunk retrieval (no answer gen).

    Full pipeline (rewrite → decompose → retrieve → grade → aggregate), then
    return chunks without LLM answer generation.

    **When to use**
    - Complex / multi-hop content questions where ``search_direct_chunks`` is
      not enough.
    - Need automatic collection discovery (no collection id from the user).
    - Prefer over browsing ``list_library_tree`` for "what do the docs say".

    **When not to use**
    - Simple focused lookup in a known collection → ``search_direct_chunks``.
    - Known file_id → ``get_document_text`` / ``get_file_chunks`` (cheaper).
    - Library layout only → ``list_library_tree``; timeline → ``get_timeline``.

    Same chunk structure as ``search_direct_chunks`` (``source``, ``file_id``,
    ``filename``); agent formats the answer with its own LLM. No collection
    parameter — pipeline auto-discovers targets.

    Args:
        query: The user question.
        include_images: Embed base64-encoded images from retrieved chunks.
        include_archived: Include collections the user has archived. Keep false
            for every normal search. Enable only when the user explicitly
            refers to the archived status (e.g. "the archived X", "X I
            archived"). A question that merely names a collection that happens
            to be archived does NOT qualify — answer from active collections,
            or tell the user the collection is archived and ask whether to
            search it.

    Returns:
        JSON ``{"results": [...chunks...], "query_used": str, "total": N,
        "tasks": [...], "images"?: {...}}``. ``tasks`` is the agentic
        sub-query trail for transparency.
    """
    def _run():
        from src.rag.search_views import agentic_search_view

        return agentic_search_view(
            query,
            include_images=include_images,
            include_archived=include_archived,
        )

    # 必须保留请求上下文：`_run` 里读 get_actor() 做可见性过滤，裸 run_in_executor
    # 会让身份退回 owner → 隐藏库的过滤失效（见 src/async_ctx.py）。
    from src.async_ctx import run_with_context

    result = await run_with_context(_run)
    return json.dumps(result, ensure_ascii=False, default=str)


# ── get_query_history ─────────────────────────────────────────


@module_gated("chat")
@hub_routed(
    method="GET",
    path="/history",
    query=lambda args: {
        "limit": args.get("limit", 50),
        "include_details": bool(args.get("include_details")),
    },
    forwards=("limit", "include_details"),
)
async def get_query_history(
    limit: int = 50, include_details: bool = False, ctx: Context | None = None
) -> str:
    """Get recent query history from past RAG queries.

    By default returns only ``question`` + ``timestamp`` per entry (lightweight).
    Set ``include_details=True`` to also include ``answer``, ``sources``, and
    other full fields.

    Reads from ``data/history/history.jsonl``.
    """
    def _run():
        from src.config import DATA_DIR

        file = DATA_DIR / "history" / "history.jsonl"
        if not file.exists():
            return []
        entries = []
        for line in file.read_text().strip().split("\n"):
            if line:
                entry = json.loads(line)
                entries.append(entry)

        # RBAC（2026-09-17）：条目含 **question / answer / sources 全文**，是隐藏库最
        # 直接的泄露面（别人查过隐藏库，历史里就留着原文）。必须按 collection 可见性
        # 过滤，且**过滤在截断之前**——否则隐藏条目会占掉额度，返回条数 < limit 本身
        # 就是"最近 N 条里有你看不到的东西"的可观测信号。
        # 判定失败 → 返回空（fail-closed），不回落"不过滤"。
        try:
            from src.identity import get_actor
            from src.identity import visibility as _vis

            actor = get_actor()
            # 批量版：集合/名字映射循环外建一次（逐条版对 member 是二次方）
            entries = _vis.history_entries_visible(actor, entries)
        except Exception:
            logger.exception("get_query_history visibility gate failed — returning empty")
            return []

        if not include_details:
            entries = [
                {
                    "question": e.get("question", ""),
                    "timestamp": e.get("timestamp", ""),
                }
                for e in entries
            ]
        return entries[-limit:]

    # 必须保留请求上下文：`_run` 里读 get_actor() 做可见性过滤，裸 run_in_executor
    # 会让身份退回 owner → 隐藏库的过滤失效（见 src/async_ctx.py）。
    from src.async_ctx import run_with_context

    result = await run_with_context(_run)
    return json.dumps(result, ensure_ascii=False, default=str)


__all__ = [
    "search_direct_chunks",
    "search_agentic_chunks",
    "get_query_history",
]
