"""MCP Notes management tools.

6 atomic tools wrapping ``src.notes`` (Tiptap-backed Markdown notes):

- :func:`list_notes` — list notes for a collection
- :func:`get_note` — full note (metadata + content + references)
- :func:`create_note` — create empty note
- :func:`update_note` — update title / content (auto-syncs injection-block refs)
- :func:`delete_note` — delete note + clean up ingested chunks
- :func:`trigger_propagation` — re-distill this note into all notes that
  reference it (chain propagation supported via ``auto=True``)

Note: ``distill_note`` (one-shot distillation without propagation) is folded
into ``trigger_propagation`` because the only meaningful use case is to push
changes downstream. If you need a single-distillation result without writing
it into the target, call ``get_note`` to read the cached distillation after
propagation runs.

Note: ingestion (``POST /ingest``) is intentionally not exposed as an MCP
tool because it requires running the full embedding pipeline (chunk +
enrich + embed + sparse encode). The UI handles that workflow.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.mcp.common import err, ok, require_collection, run_sync, to_json

logger = logging.getLogger(__name__)


# ── list_notes ─────────────────────────────────────────────────


async def list_notes(collection: str) -> str:
    """List notes for a collection, sorted by ``updated_at`` desc.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.

    Returns note metadata plus two derived flags:
    - ``is_extracted`` — this note has been distilled into other notes
    - ``is_ingested`` — this note has chunks indexed in Qdrant
    """
    def _run() -> dict[str, Any]:
        from src.notes import store as nstore
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        from src.services import services

        if e := require_collection(collection):
            return e

        notes = nstore.list_notes(collection)
        ingested_ids: set[str] = set()
        if services.db.collection_exists(collection):
            offset = None
            try:
                while True:
                    points, offset = services.db.scroll_points(
                        collection=collection,
                        limit=200,
                        offset=offset,
                        with_payload=["source"],
                        with_vectors=False,
                        scroll_filter=Filter(
                            must=[FieldCondition(key="file_type", match=MatchValue(value="note"))]
                        ),
                    )
                    for p in points:
                        src = p.get("payload", {}).get("source", "")
                        if isinstance(src, str) and src.startswith("__note__:"):
                            ingested_ids.add(src[len("__note__:"):])
                    if offset is None:
                        break
            except Exception as e:
                logger.warning("Failed to query ingested note IDs: %s", e)

        items = []
        for n in notes:
            referenced_by = nstore.get_referenced_by(n.id)
            items.append({
                "id": n.id,
                "title": n.title,
                "collection": n.collection,
                "created_at": n.created_at.isoformat(),
                "updated_at": n.updated_at.isoformat(),
                "is_extracted": len(referenced_by) > 0,
                "extracted_into": referenced_by,
                "is_ingested": n.id in ingested_ids,
            })
        return ok(collection=collection, notes=items, total=len(items))

    return to_json(await run_sync(_run))


# ── get_note ───────────────────────────────────────────────────


async def get_note(collection: str, note_id: str) -> str:
    """Get a note's metadata, content, references, and extraction status.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    Returns:
        ``id``, ``title``, ``collection``, ``created_at``, ``updated_at``,
        ``content`` (raw markdown), ``references`` (injection-block sources
        embedded in this note), ``is_extracted``, ``extracted_into``,
        ``is_ingested``.
    """
    def _run() -> dict[str, Any]:
        from src.notes import store as nstore
        from src.services import services
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if e := require_collection(collection):
            return e

        n = nstore.get_note(note_id)
        if not n:
            return err(f"Note '{note_id}' not found")

        content = nstore.get_content(note_id) or ""
        references = nstore.get_references(note_id)
        referenced_by = nstore.get_referenced_by(note_id)

        # Resolve source titles for references
        for ref in references:
            src = nstore.get_note(ref.get("source_note_id", ""))
            ref["source_title"] = src.title if src else ref.get("source_note_id", "")

        # Check ingested status
        is_ingested = False
        try:
            if services.db.collection_exists(collection):
                fc = Filter(must=[FieldCondition(
                    key="source", match=MatchValue(value=f"__note__:{note_id}")
                )])
                is_ingested = services.db.count_by_filter(collection, fc) > 0
        except Exception:
            pass

        return ok(
            id=n.id,
            title=n.title,
            collection=n.collection,
            created_at=n.created_at.isoformat(),
            updated_at=n.updated_at.isoformat(),
            content=content,
            references=references,
            is_extracted=len(referenced_by) > 0,
            extracted_into=referenced_by,
            is_ingested=is_ingested,
        )

    return to_json(await run_sync(_run))


# ── create_note ────────────────────────────────────────────────


async def create_note(collection: str, title: str = "") -> str:
    """Create an empty note in a collection.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.

    Title defaults to current timestamp if omitted. Use :func:`update_note`
    next to set content.
    """
    from datetime import datetime

    def _run() -> dict[str, Any]:
        from src.notes import store as nstore

        if e := require_collection(collection):
            return e

        final_title = title.strip() or datetime.now().strftime("%Y-%m-%d %H:%M")
        n = nstore.create_note(collection, final_title)
        return ok(
            id=n.id,
            title=n.title,
            collection=n.collection,
            created_at=n.created_at.isoformat(),
            updated_at=n.updated_at.isoformat(),
        )

    return to_json(await run_sync(_run))


# ── update_note ────────────────────────────────────────────────


async def update_note(
    collection: str,
    note_id: str,
    title: str | None = None,
    content: str | None = None,
) -> str:
    """Update a note's title and/or content.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    When ``content`` is provided, this tool auto-syncs injection-block
    references — it re-parses the content for ``:::distill-block{...}``
    fences and updates both ``references.json`` (this note's outgoing refs)
    and the referenced notes' ``referenced_by.json`` (their incoming refs).
    Pass ``content=None`` to update only the title.
    """
    if title is None and content is None:
        return to_json(err("Provide at least one of 'title' or 'content'"))

    def _run() -> dict[str, Any]:
        from src.notes import store as nstore
        from src.notes.service import parse_injection_blocks

        if e := require_collection(collection):
            return e

        n = nstore.get_note(note_id)
        if not n:
            return err(f"Note '{note_id}' not found")

        updated_fields: list[str] = []

        if title is not None and title != n.title:
            nstore.update_note(note_id, title=title)
            updated_fields.append("title")

        if content is not None:
            # Capture old refs BEFORE saving new content so we can diff.
            old_refs = nstore.get_references(note_id)
            old_source_ids = {r["source_note_id"] for r in old_refs}

            # Parse new injection blocks from content.
            blocks = parse_injection_blocks(content)
            refs: list[dict[str, Any]] = []
            new_source_ids: set[str] = set()
            for block in blocks:
                source_id = block["source_note_id"]
                if not source_id:
                    continue
                new_source_ids.add(source_id)
                src = nstore.get_note(source_id)
                refs.append({
                    "block_id": block["block_id"],
                    "source_note_id": source_id,
                    "source_title": src.title if src else "",
                })

            # Save content + references (atomic from caller's perspective).
            nstore.save_content(note_id, content)
            nstore.save_references(note_id, refs)
            updated_fields.append("content")

            # Diff old vs new and update backlinks on referenced notes.
            for removed in old_source_ids - new_source_ids:
                nstore._remove_referenced_by(removed, note_id)
            for added in new_source_ids - old_source_ids:
                nstore._add_referenced_by(added, note_id)

        return ok(message="Note updated", id=note_id, updated_fields=updated_fields)

    return to_json(await run_sync(_run))


# ── delete_note ────────────────────────────────────────────────


async def delete_note(collection: str, note_id: str) -> str:
    """Delete a note and clean up everything that references it.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    Removes:
    - the note directory (``data/notes/{note_id}/``)
    - the note's ingested chunks from Qdrant (if any)
    - backlinks in any note that referenced this one (their ``referenced_by.json``)
    """
    def _run() -> dict[str, Any]:
        from src.notes import store as nstore
        from src.services import services

        if e := require_collection(collection):
            return e

        n = nstore.get_note(note_id)
        if not n:
            return err(f"Note '{note_id}' not found")

        # Clean up ingested chunks from Qdrant.
        source = f"__note__:{note_id}"
        try:
            if services.db.collection_exists(collection):
                services.db.delete_by_filter(collection, key="source", value=source)
        except Exception as e:
            logger.warning("Failed to clean up chunks for note %s: %s", note_id, e)

        # delete_note in store also cleans up references from OTHER notes.
        deleted = nstore.delete_note(note_id)
        if not deleted:
            return err(f"Note '{note_id}' could not be deleted")
        return ok(message=f"Note '{note_id}' deleted", id=note_id)

    return to_json(await run_sync(_run))


# ── trigger_propagation ────────────────────────────────────────


async def trigger_propagation(
    collection: str,
    note_id: str,
    auto: bool = True,
) -> str:
    """Re-distill this note into all notes that reference it.

    Runs the propagation synchronously (the LLM call blocks). For long
    propagation chains this can take several seconds — wrap your agent call
    accordingly.

    Args:
        collection: Collection **ID** from ``list_collections`` (used only for
            validation; propagation works across collections because notes
            are globally unique by ID).
        note_id: The source note whose changes should propagate downstream.
        auto: If True (default), chain-propagate through downstream notes
            (a note downstream of the source gets re-distilled into ITS
            downstream notes). If False, only direct children are updated.
    """
    def _run() -> dict[str, Any]:
        from src.notes import store as nstore
        from src.notes.service import propagate_forward

        if e := require_collection(collection):
            return e

        n = nstore.get_note(note_id)
        if not n:
            return err(f"Note '{note_id}' not found")

        updated = propagate_forward(note_id, auto=auto)
        return ok(
            message=f"Propagated to {len(updated)} note(s)",
            source_note_id=note_id,
            updated_note_ids=updated,
            auto=auto,
        )

    # The LLM distillation call inside propagate_forward is blocking —
    # hand the whole thing off to a thread.
    return to_json(await asyncio.to_thread(_run))


__all__ = [
    "list_notes",
    "get_note",
    "create_note",
    "update_note",
    "delete_note",
    "trigger_propagation",
]