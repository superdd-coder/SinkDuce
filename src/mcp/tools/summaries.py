"""MCP summary management tools.

4 atomic tools that wrap ``src.rag.summary_manager.SummaryManager``:
- :func:`get_collection_summary` — LLM-generated overview of a collection
- :func:`get_doc_summary` — structured summary of one document
- :func:`get_conflicts` — contradictions detected across documents
- :func:`trigger_consolidate` — rebuild the collection-level summary (async)

Note: ``get_project_description`` (the 2-sentence description) has been
removed from MCP because the same information is now embedded in
``list_collections`` (``catalog_definition`` field) and ``get_collection``
(``catalog_definition`` field). Use those instead — they surface the
description alongside richer metadata.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

import logging
from typing import Any

from src.hub.routing import _require_collection_on_remote, hub_routed
from src.mcp.common import err, ok, require_collection, run_sync, to_json, module_gated

logger = logging.getLogger(__name__)


def _get_summary_manager():
    from src.services import services
    from src.rag.summary_manager import SummaryManager
    return SummaryManager(db=services.db)


# ── get_collection_summary ─────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/collections/{collection}/info/summary",
    # 实测字段完全一致 —— 唯一一个不需要整形就等价的
    precheck=_require_collection_on_remote,
    forwards=("collection",),
)
async def get_collection_summary(collection: str, ctx: Context | None = None) -> str:
    """LLM-generated overview of a collection (from document summaries).

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.

    **When to use:** "what is this collection about" / high-level overview.
    **When not:** factual lookups in source text → search tools; file layout →
    ``list_library_tree``; timeline → ``get_timeline``. Not a file list.
    """
    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e
        sm = _get_summary_manager()
        summary = sm.get_collection_summary(collection)
        if summary is None:
            return err(f"No summary found for collection '{collection}'")
        return summary

    return to_json(await run_sync(_run))


# ── get_doc_summary ────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    # source 是 :path 参数（可含斜杠），显式 allow_slash；.. 仍被拒
    path="/agent/collections/{collection}/doc-summary/{source}",
    precheck=_require_collection_on_remote,
    forwards=("collection", "source"),
    allow_slash=("source",),
)
async def get_doc_summary(
    collection: str, source: str, ctx: Context | None = None,
) -> str:
    """Get the structured summary of a specific document.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    Returns extracted data points, facts, and insights. Use
    :func:`list_documents` first to find the correct ``source`` filename.
    """
    def _run() -> dict[str, Any]:
        from src.documents_views import doc_summary_view

        return doc_summary_view(collection, source)

    return to_json(await run_sync(_run))


# ── get_conflicts ──────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/collections/{collection}/info/conflicts",
    shape=lambda payload, args: (
        payload if (isinstance(payload, dict) and payload.get("conflicts"))
        # 工具在「没有冲突」时会附带一句 message（REST 不带）→ 补上，
        # 否则跨盒子调用会少一个"为什么是空的"的说明
        else {**payload, "message": "No conflicts detected"}
        if isinstance(payload, dict) else payload
    ),
    precheck=_require_collection_on_remote,
    forwards=("collection",),
)
async def get_conflicts(collection: str, ctx: Context | None = None) -> str:
    """Check for contradictory information across documents in a collection.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    Returns detected conflicts (e.g. different dates, conflicting numbers).
    """
    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e
        sm = _get_summary_manager()
        conflicts = sm.get_conflicts(collection)
        if not conflicts:
            return ok(collection=collection, conflicts=[], message="No conflicts detected")
        return ok(collection=collection, conflicts=conflicts)

    return to_json(await run_sync(_run))


# ── trigger_consolidate ────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="POST",
    path="/collections/{collection}/info/consolidate",
    # precheck：REST 对坏库不查（会**静默创建**一个注定失败的 consolidate 任务，
    # 报「成功」）；本地工具先报"库不存在"。
    precheck=_require_collection_on_remote,
    # REST 回 {message, task}；工具契约是 ok(message=工具文案, task_id, collection)。
    shape=lambda payload, a: (
        payload
        if not isinstance(payload, dict) or payload.get("error")
        else {
            "message": f"Consolidation triggered for '{a.get('collection')}'",
            "task_id": ((payload.get("task") or {}).get("id")),
            "collection": a.get("collection"),
        }
    ),
    forwards=("collection",),
)
async def trigger_consolidate(collection: str, ctx: Context | None = None) -> str:
    """Rebuild the collection-level summary from all document summaries.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    Run this after uploading or deleting documents to refresh the collection
    overview, project description, and conflict report. The work runs as an
    async task — use ``list_tasks`` / ``get_task_status`` to check completion.
    """
    from src.services import services
    from src.tasks import task_manager

    def _run() -> dict[str, Any]:
        if not services.db.collection_exists(collection):
            return err(f"Collection '{collection}' does not exist")
        task = task_manager.create_task(
            filename=f"consolidate:{collection}",
            task_type="consolidate",
            collection=collection,
        )
        return ok(
            message=f"Consolidation triggered for '{collection}'",
            task_id=task.id,
            collection=collection,
        )

    return to_json(await run_sync(_run))


__all__ = [
    "get_collection_summary",
    "get_doc_summary",
    "get_conflicts",
    "trigger_consolidate",
]