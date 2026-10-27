"""Agent 检索端点（批次 4）：与 MCP search 工具共用同一份信封。

这两个端点跨盒子转发才有的意义：Hub 拿远端钥匙打这里，远端按**它自己的
嵌入模型/索引**检索并返回与工具本地调用同形的结果。网页不用它们。

跑在线程池（检索是阻塞管线）；``run_in_threadpool`` 经 anyio 复制
contextvars → 按钥匙角色的可见性过滤照常生效（见 src/async_ctx.py）。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from starlette.concurrency import run_in_threadpool

from src.api.routes.identity import require_module

router = APIRouter(dependencies=[Depends(require_module("chat"))])


@router.post("/search/direct")
async def direct_search(body: dict = Body()):
    """`search_direct_chunks` 工具同款信封。

    body: ``{query, collection?, collections?, search_mode?, top_k?,
    rerank_top_k?, use_reranker?, min_score?, include_images?,
    include_archived?}``（默认与工具一致）。失败回 200 + ``{"error": …}``
    （与 collections create/delete 同一约定——错误形状与工具侧逐字段一致）。
    """
    from src.rag.search_views import direct_search_view

    return await run_in_threadpool(
        direct_search_view,
        str(body.get("query") or ""),
        # 只在键缺席时用默认；显式空串保留（与本地工具同错："" → 库不存在）
        collection=(body["collection"] if isinstance(body.get("collection"), str) else "default"),
        collections=body.get("collections"),
        search_mode=(body["search_mode"] if isinstance(body.get("search_mode"), str) and body["search_mode"] else "dense"),
        top_k=int(body.get("top_k", 10)),
        rerank_top_k=int(body.get("rerank_top_k", 5)),
        use_reranker=bool(body.get("use_reranker", False)),
        min_score=float(body.get("min_score", 0.0)),
        include_images=bool(body.get("include_images", False)),
        include_archived=bool(body.get("include_archived", False)),
    )


@router.post("/search/agentic")
async def agentic_search(body: dict = Body()):
    """`search_agentic_chunks` 工具同款信封。body: ``{query, include_images?,
    include_archived?}``。agentic 管线较慢（重写/分解/评分），Hub 出站超时
    20s——超大会超时，属预期（工具侧同样限制）。"""
    from src.rag.search_views import agentic_search_view

    return await run_in_threadpool(
        agentic_search_view,
        str(body.get("query") or ""),
        include_images=bool(body.get("include_images", False)),
        include_archived=bool(body.get("include_archived", False)),
    )
