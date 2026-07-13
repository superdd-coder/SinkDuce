"""MCP document management tools.

6 atomic tools:
- :func:`list_documents` — discover documents via the file index (fast, no Qdrant scroll)
- :func:`upload_document` — upload from a server filesystem path
- :func:`upload_document_content` — upload from base64-encoded content (for tools that have bytes in memory)
- :func:`delete_document` — remove document + chunks + summary + file snapshot
- :func:`get_file_chunks` — list chunks for a document (text + metadata only, no vectors)
- :func:`get_document_text` — get the full plain text of a document

Note: ``upload_folder`` (batch directory import) has been intentionally removed
because it requires server filesystem traversal — not safe for MCP exposure.
Bulk imports should be done via the API/UI.

Note: ``get_task_status`` lives in :mod:`src.mcp.tools.tasks` (semantically a
task tool, not a document tool).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from src.mcp.common import (
    decode_base64_content,
    err,
    ok,
    require_collection,
    run_sync,
    safe_filename,
    to_json,
)

logger = logging.getLogger(__name__)

COLLECTIONS_DIR = Path("data").resolve() / "collections"


def _files_dir(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "files"


def _load_file_index(collection_id: str) -> dict[str, dict]:
    """Load the lightweight file metadata index (no Qdrant scroll)."""
    from src.collections.file_index import load
    return load(collection_id)


# Task handlers (upload, consolidate, doc_summary, sparse_recalc) are now
# registered by ``src.main.lifespan`` so the same handler set is shared
# between the FastAPI HTTP routes and the MCP sub-app mounted at /mcp.


# ── list_documents ─────────────────────────────────────────────


async def list_documents(collection: str) -> str:
    """List all documents in a collection, using the file index (no Qdrant scroll).

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.

    Returns file metadata: ``source``, ``source_label``, ``file_type``,
    ``chunks``, ``ingested_at``, ``original_ext``, and the implicit
    ``file_id`` (key) for use with ``get_file_chunks`` / ``delete_document``.

    If the file index is empty (e.g. for collections created before the index
    existed), falls back to scrolling Qdrant and re-hydrating the index.
    """
    from src.services import services

    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e

        idx = _load_file_index(collection)
        if not idx:
            # Hydrate: walk Qdrant once to rebuild the index, then return it.
            from qdrant_client.models import FieldCondition, Filter, MatchValue
            filter_cond = Filter(
                must_not=[FieldCondition(key="chunk_type", match=MatchValue(value="__config__"))]
            )
            source_counts: dict[str, int] = {}
            offset = None
            while True:
                points, offset = services.db.scroll_points(
                    collection=collection,
                    limit=1000,
                    offset=offset,
                    with_payload=["source"],
                    with_vectors=False,
                    scroll_filter=filter_cond,
                )
                for p in points:
                    src = p["payload"].get("source", "unknown")
                    source_counts[src] = source_counts.get(src, 0) + 1
                if offset is None:
                    break

            files = [
                {
                    "source": src,
                    "source_label": src,
                    "file_type": "document",
                    "chunks": count,
                    "file_id": None,  # not indexed — caller must look up separately
                }
                for src, count in sorted(source_counts.items())
            ]
            return ok(collection=collection, files=files, index_hydrated=True)

        files = []
        for fid, entry in sorted(idx.items(), key=lambda kv: kv[1].get("ingested_at", 0)):
            files.append({
                "file_id": fid,
                "source": entry.get("source"),
                "source_label": entry.get("source_label"),
                "file_type": entry.get("file_type"),
                "chunks": entry.get("chunks", 0),
                "ingested_at": entry.get("ingested_at"),
                "original_ext": entry.get("original_ext"),
            })
        return ok(collection=collection, files=files, total=len(files))

    return to_json(await run_sync(_run))


# ── upload_document ────────────────────────────────────────────


async def upload_document(file_path: str, collection: str = "default") -> str:
    """Upload a document from the **SinkDuce server's** filesystem to a collection.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.

    ``file_path`` is a path on the **remote server**, NOT your local machine.
    If the file is on your local machine, use :func:`upload_document_content`
    with base64-encoded content instead.

    Processing is async — use ``list_tasks`` / ``get_task_status`` to check
    progress.
    """
    from src.services import services
    from src.tasks import task_manager

    path = Path(file_path)
    if not path.is_file():
        return to_json(err(f"File not found: {file_path}"))

    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e
        col_config = services.db.get_collection_config(collection)
        allowed = col_config.get("allowed_file_types")
        if allowed:
            ext = path.suffix.lower().lstrip(".")
            if ext not in allowed:
                return err(
                    f"File type '.{ext}' not allowed. Allowed: {', '.join(allowed)}"
                )

        try:
            safe_name = safe_filename(path.name)
        except ValueError as exc:
            return err(str(exc))

        file_id = uuid.uuid4().hex
        file_source = f"__file__:{file_id}"
        file_dir = _files_dir(collection) / file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        save_path = file_dir / safe_name
        shutil.copy2(path, save_path)

        task = task_manager.create_task(
            filename=safe_name,
            task_type="upload",
            file_path=str(save_path),
            collection=collection,
            filename_param=file_source,
            source_label=safe_name,
            file_id=file_id,
        )
        return ok(
            message="File queued for processing",
            task_id=task.id,
            file_id=file_id,
            filename=safe_name,
            collection=collection,
        )

    return to_json(await run_sync(_run))


# ── upload_document_content ────────────────────────────────────


async def upload_document_content(
    filename: str,
    content_b64: str,
    collection: str = "default",
) -> str:
    """Upload a document from base64-encoded content (for in-memory bytes).

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.

    Use this when the calling tool has the document bytes in memory and wants
    to avoid round-tripping through a temp file on disk. ``content_b64`` may
    include a ``data:...;base64,`` prefix — it will be stripped automatically.

    Supported file types match ``upload_document``: PDF, DOCX, XLSX, PPTX, MD,
    TXT, CSV, HTML, JSON. The collection's ``allowed_file_types`` filter still
    applies.
    """
    from src.services import services
    from src.tasks import task_manager

    try:
        safe_name = safe_filename(filename)
    except ValueError as exc:
        return to_json(err(str(exc)))

    try:
        raw = decode_base64_content(content_b64)
    except ValueError as exc:
        return to_json(err(str(exc)))

    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e
        col_config = services.db.get_collection_config(collection)
        allowed = col_config.get("allowed_file_types")
        if allowed:
            ext = Path(safe_name).suffix.lower().lstrip(".")
            if ext not in allowed:
                return err(
                    f"File type '.{ext}' not allowed. Allowed: {', '.join(allowed)}"
                )

        file_id = uuid.uuid4().hex
        file_source = f"__file__:{file_id}"
        file_dir = _files_dir(collection) / file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        save_path = file_dir / safe_name
        save_path.write_bytes(raw)

        task = task_manager.create_task(
            filename=safe_name,
            task_type="upload",
            file_path=str(save_path),
            collection=collection,
            filename_param=file_source,
            source_label=safe_name,
            file_id=file_id,
        )
        return ok(
            message="Content queued for processing",
            task_id=task.id,
            file_id=file_id,
            filename=safe_name,
            size_bytes=len(raw),
            collection=collection,
        )

    return to_json(await run_sync(_run))


# ── delete_document ────────────────────────────────────────────


async def delete_document(collection: str, source: str) -> str:
    """Delete a document — removes chunks, file snapshot, and doc summary.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.
    Use :func:`list_documents` first to get the correct ``source`` filename.
    The collection summary is *not* touched here; call ``trigger_consolidate``
    afterwards if you want it refreshed.
    """
    from src.services import services
    from src.rag.summary_manager import SummaryManager

    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e

        deleted_count = services.db.delete_by_filter(collection, key="source", value=source)

        # Delete file snapshot via file index
        try:
            from src.collections.file_index import load as load_file_index, remove_by_source as remove_file_index
            idx = load_file_index(collection)
            removed_file_id: str | None = None
            for fid, entry in idx.items():
                if entry.get("source") == source:
                    file_dir = _files_dir(collection) / fid
                    if file_dir.exists():
                        shutil.rmtree(file_dir)
                    removed_file_id = fid
                    break
            if removed_file_id:
                remove_file_index(collection, source)
        except Exception as e:
            logger.warning("File index cleanup failed (non-fatal): %s", e)

        try:
            sm = SummaryManager(db=services.db)
            sm.delete_doc_summary(collection, source)
        except Exception as e:
            logger.warning("Doc summary cleanup failed (non-fatal): %s", e)

        # ── Sparse recalc counter (vocab drift tracking) ──
        if deleted_count > 0:
            try:
                col_config = services.db.get_collection_config(collection)
                sc = col_config.get("sparse_recalc_counter", 0) + deleted_count
                threshold = col_config.get("sparse_recalc_threshold", 5000)
                from src.tasks import task_manager
                services.db.update_collection_config(collection, {"sparse_recalc_counter": sc})
                logger.info(
                    "[SparseRecalc] counter col=%s delta=+%d counter=%d threshold=%d",
                    collection, deleted_count, sc, threshold,
                )
                if sc >= threshold:
                    task_manager.create_task(
                        filename=f"recalc:{collection}",
                        task_type="sparse_recalc",
                        collection=collection,
                    )
                    logger.info(
                        "[SparseRecalc] triggered for %s (counter=%d >= threshold=%d)",
                        collection, sc, threshold,
                    )
            except Exception as e:
                logger.warning("[SparseRecalc] counter update failed (non-fatal): %s", e)

        try:
            from src.tasks import task_manager
            col_config = services.db.get_collection_config(collection)
            counter = col_config.get("summary_change_counter", 0) + 1
            threshold = col_config.get("summary_consolidate_threshold", 10)
            services.db.update_collection_config(collection, {"summary_change_counter": counter})
            if counter >= threshold:
                task_manager.create_task(
                    filename=f"consolidate:{collection}",
                    task_type="consolidate",
                    collection=collection,
                )
        except Exception as e:
            logger.warning("Counter update failed (non-fatal): %s", e)

        return ok(
            message=f"Deleted '{source}' from '{collection}'",
            deleted_chunks=deleted_count,
            source=source,
        )

    return to_json(await run_sync(_run))


# ── get_file_chunks ────────────────────────────────────────────


async def get_file_chunks(
    collection: str,
    source: str,
    offset: int = 0,
    limit: int = 50,
    include_context: bool = True,
    chunk_type: str = "*",
) -> str:
    """List chunks for a document (text + metadata, no vectors).

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    Returns up to ``limit`` chunks sorted by document order. Use this to
    inspect what was actually indexed without fetching the full text via
    ``get_document_text``.

    Args:
        collection: Collection ID from ``list_collections``.
        source: The ``source`` value from ``list_documents``.
        offset: Skip this many chunks before returning (default 0).
        limit: Max chunks to return (default 50).
        include_context: Include the contextual enrichment prefix in each chunk.
        chunk_type: Filter by chunk type — ``"normal"``, ``"parent"``,
            ``"child"``. Default ``"*"`` returns all types except ``"child"``
            (child chunks are sub-divisions of parents; use ``chunk_type="child"``
            or ``chunk_type=""`` to include them).
    """
    from src.services import services

    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e

        # Default: exclude child chunks (redundant with parents at same char_offset)
        _filter_type = chunk_type if chunk_type != "*" else None
        _exclude_child = chunk_type == "*"

        from qdrant_client.models import FieldCondition, Filter, MatchValue
        filter_cond = Filter(must=[FieldCondition(key="source", match=MatchValue(value=source))])

        chunks = []
        paged_offset = None
        while True:
            points, paged_offset = services.db.scroll_points(
                collection=collection,
                limit=200,
                offset=paged_offset,
                with_payload=True,
                with_vectors=False,
                scroll_filter=filter_cond,
            )
            for p in points:
                payload = p.get("payload", {})
                chunk = {
                    "id": getattr(p, "id", None),
                    "text": payload.get("text", ""),
                    "chunk_type": payload.get("chunk_type", "normal"),
                    "chunk_index": payload.get("chunk_index"),
                    "char_offset": payload.get("char_offset"),
                    "source": payload.get("source"),
                    "parent_id": payload.get("parent_id"),
                }
                if include_context and payload.get("context"):
                    chunk["context"] = payload["context"]
                chunks.append(chunk)
            if paged_offset is None:
                break

        # Sort by char_offset; tiebreak: parents/normal before children
        _TYPE_ORDER = {"normal": 0, "parent": 0, "child": 1}
        chunks.sort(key=lambda c: (
            c.get("char_offset") is None,
            c.get("char_offset") or 0,
            _TYPE_ORDER.get(c.get("chunk_type", "normal"), 0),
        ))
        if _filter_type:
            chunks = [c for c in chunks if c.get("chunk_type") == _filter_type]
        elif _exclude_child:
            chunks = [c for c in chunks if c.get("chunk_type") != "child"]
        total = len(chunks)
        chunks = chunks[offset:offset + limit]
        return ok(
            collection=collection,
            source=source,
            offset=offset,
            limit=limit,
            total=total,
            chunks=chunks,
        )

    return to_json(await run_sync(_run))


# ── get_document_text ──────────────────────────────────────────


async def get_document_text(collection: str, source: str, offset: int = 0, limit: int = 10000) -> str:
    """Get the full plain text of a document by re-reading its file snapshot.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    Use this when you need the raw extracted text (not the indexed chunks).
    Returns at most ``limit`` characters (default 10000). Pass ``limit=0`` for
    unlimited output. Use ``offset`` to read from a position further into the
    document.

    Returns an error if the file snapshot is missing.

    Args:
        collection: Collection ID from ``list_collections``.
        source: The ``source`` value from ``list_documents``.
        offset: Character offset to start reading from (default 0).
        limit: Max characters to return (default 10000; 0 = unlimited).
    """
    from src.collections.file_index import load as load_file_index

    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            return e
        idx = load_file_index(collection)
        target_file_id: str | None = None
        for fid, entry in idx.items():
            if entry.get("source") == source:
                target_file_id = fid
                break
        if not target_file_id:
            return err(f"No file snapshot found for source '{source}' in '{collection}'")

        file_dir = _files_dir(collection) / target_file_id
        if not file_dir.is_dir():
            return err(f"File snapshot directory missing: {file_dir}")

        files = list(file_dir.iterdir())
        if not files:
            return err(f"File snapshot empty: {file_dir}")

        target = next((f for f in files if f.is_file()), None)
        if target is None:
            return err(f"No file in snapshot: {file_dir}")

        try:
            full = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return err(f"Failed to read file: {exc}")

        total_chars = len(full)
        window = full[offset:offset + limit] if limit > 0 else full[offset:]
        return ok(
            collection=collection,
            source=source,
            file_id=target_file_id,
            filename=target.name,
            size_bytes=target.stat().st_size,
            total_chars=total_chars,
            offset=offset,
            limit=limit,
            content=window,
        )

    return to_json(await run_sync(_run))


# ── set_document_definitive ──────────────────────────────────────


async def set_document_definitive(
    collection: str,
    source: str,
    definitive: bool = True,
) -> str:
    """Set a document's definitive (include-in-summary) flag.

    Marking a document definitive includes it in collection-level summary
    consolidation. When ``definitive=True``, a debounced consolidate is
    automatically triggered (no need to call ``trigger_consolidate``).

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use :func:`list_collections` first to get IDs.

    ``source`` must be the ``source`` value from :func:`list_documents`
    (e.g. ``"__file__:abc123"``).

    Args:
        collection: Collection ID from ``list_collections``.
        source: Document source from ``list_documents``.
        definitive: True to include in summary, False to exclude (default True).
    """
    def _run() -> dict[str, Any]:
        from src.services import services
        from src.rag.summary_manager import SummaryManager

        if e := require_collection(collection):
            return e

        sm = SummaryManager(db=services.db)
        existing = sm.get_doc_summary(collection, source)

        if existing is not None:
            sm.set_doc_summary_include(collection, source, definitive)
        else:
            sm.upsert_doc_summary(
                collection, source,
                data=[], facts=[], insights=[],
                include_in_summary=definitive,
            )

        consolidate_triggered = False
        if definitive:
            try:
                from src.api.routes.info import _snapshot_includes, schedule_debounced_consolidate
                pre = _snapshot_includes(collection)
                schedule_debounced_consolidate(collection, pre)
                consolidate_triggered = True
            except Exception:
                logger.warning(
                    "[set_document_definitive] Failed to trigger consolidate for %s/%s",
                    collection, source, exc_info=True,
                )

        return ok(
            source=source,
            definitive=definitive,
            consolidate_triggered=consolidate_triggered,
            message=(
                "Document set to definitive; consolidation triggered"
                if consolidate_triggered else
                "Document definitive flag updated"
            ),
        )

    return to_json(await run_sync(_run))


__all__ = [
    "list_documents",
    "upload_document",
    "upload_document_content",
    "delete_document",
    "get_file_chunks",
    "get_document_text",
    "set_document_definitive",
]