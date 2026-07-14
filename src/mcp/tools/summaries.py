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

import logging
from typing import Any

from src.mcp.common import err, ok, require_collection, run_sync, to_json

logger = logging.getLogger(__name__)


def _get_summary_manager():
    from src.services import services
    from src.rag.summary_manager import SummaryManager
    return SummaryManager(db=services.db)


# ── get_collection_summary ─────────────────────────────────────


async def get_collection_summary(collection: str) -> str:
    """Get the LLM-generated overview of a collection (summarizes all docs).

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.

    Use this to quickly understand what a collection contains before diving
    into specific documents or running queries.
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


async def get_doc_summary(collection: str, source: str) -> str:
    """Get the structured summary of a specific document.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    Returns extracted data points, facts, and insights. Use
    :func:`list_documents` first to find the correct ``source`` filename.
    """
    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e
        sm = _get_summary_manager()
        summary = sm.get_doc_summary(collection, source)
        if summary is None:
            return err(f"No summary found for document '{source}' in collection '{collection}'")
        return summary

    return to_json(await run_sync(_run))


# ── get_conflicts ──────────────────────────────────────────────


async def get_conflicts(collection: str) -> str:
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


async def trigger_consolidate(collection: str) -> str:
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