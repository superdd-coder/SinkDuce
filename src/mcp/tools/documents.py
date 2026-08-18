"""MCP document management tools.

6 atomic tools:
- :func:`list_documents` — **legacy** file-index listing (prefer list_library_tree / list_files)
- :func:`upload_document_from_staging` — **unified** upload via side-channel staging (zero context leak)
- :func:`delete_document` — remove document + chunks + summary + file snapshot
- :func:`get_file_chunks` — indexed chunks for a known file (prefer file_id)
- :func:`get_document_text` — full extractable text for a known file (prefer file_id)
- :func:`set_document_definitive` — toggle definitive flag, trigger consolidate

**Content path (known file_id):** get_document_text → optional get_file_chunks.
**Discovery path (unknown file):** search_direct_chunks / search_agentic_chunks.
**Browse path:** list_library_tree / list_files (file-mgmt), not list_documents.

.. warning::

    ``upload_document`` and ``upload_document_content`` have been **removed**
    from MCP tools.  All uploads now go through the unified staging pattern:

    1. POST content to ``/api/mcp/stage-content`` (regular HTTP side channel):
       - multipart form (``-F "file=@report.pdf"``) — **recommended**, zero overhead
       - octet-stream (``--data-binary @file`` + ``X-Filename`` header)
       - JSON with ``file_path`` — for files already on the server
       - JSON with ``content_b64`` — fallback for JSON-only clients
    2. Call :func:`upload_document_from_staging` with the returned ``staging_token``
       — only the ~36-char UUID enters the LLM context

    This guarantees file content NEVER appears in the conversation transcript,
    regardless of file size.  Tokens expire after 10 minutes.

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

from src.config import DATA_DIR
from src.mcp.common import (
    err,
    mcp_result,
    ok,
    require_collection,
    run_sync,
    safe_filename,
    to_json,
)

logger = logging.getLogger(__name__)

COLLECTIONS_DIR = DATA_DIR / "collections"


def _files_dir(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "files"


def _load_file_index(collection_id: str) -> dict[str, dict]:
    """Load the lightweight file metadata index (no Qdrant scroll)."""
    from src.collections.file_index import load_for_read
    return load_for_read(collection_id)


async def _await_mcp(fn) -> Any:
    """Run blocking work and wrap as compact MCP CallToolResult."""
    data = await run_sync(fn)
    if not isinstance(data, dict):
        data = {"result": data}
    return mcp_result(data)


def _resolve_doc_identity(
    collection: str,
    *,
    source: str = "",
    file_id: str = "",
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Resolve ``(file_id | None, source_key, error | None)``.

    Accepts:
    - ``file_id`` (preferred for file-mgmt)
    - ``source=__file__:{id}`` / ``file:{id}`` / bare file_id
    - legacy ``source`` values from :func:`list_documents`
    """
    fid = (file_id or "").strip() or None
    src = (source or "").strip()

    if not fid and not src:
        return None, None, err(
            "Provide file_id (preferred) or source",
            extract_status="missing_args",
            hint="file_id from list_files / list_library_tree; source from list_documents",
        )

    if fid:
        return fid, f"__file__:{fid}", None

    if src.startswith("__file__:"):
        return src[len("__file__:") :], src, None
    if src.startswith("file:"):
        bare = src[len("file:") :]
        return bare, f"__file__:{bare}", None

    # file index: match source or key == bare id
    try:
        idx = _load_file_index(collection)
    except Exception:
        idx = {}
    for id_, entry in idx.items():
        if id_ == src or entry.get("source") == src:
            entry_src = entry.get("source") or f"__file__:{id_}"
            return id_, entry_src, None

    # file-mgmt DB may know the bare id even if index is stale
    try:
        from src.file_mgmt.store import get_db

        conn = get_db(collection)
        try:
            row = conn.execute(
                "SELECT file_id FROM files WHERE file_id=?", (src,)
            ).fetchone()
        finally:
            conn.close()
        if row:
            return src, f"__file__:{src}", None
    except Exception:
        pass

    # Legacy non-file-mgmt source (pass through as-is)
    return None, src, None


# Task handlers (upload, consolidate, doc_summary, sparse_recalc) are now
# registered by ``src.main.lifespan`` so the same handler set is shared
# between the FastAPI HTTP routes and the MCP sub-app mounted at /mcp.


# ── list_documents ─────────────────────────────────────────────


async def list_documents(collection: str) -> str:
    """**Legacy** document index listing (file_index / **chunk counts**).

    **When to use**
    - Need per-document **chunk counts** or old ``source`` keys without mounts.
    - Collections that predate file-management and have no folder tree.

    **When not to use**
    - Content Q&A → ``search_direct_chunks`` / ``search_agentic_chunks``.
    - File-mgmt folder/file layout → **list_library_tree**.
    - Flat list with mounts → **list_files**(scope=all).
    - Timeline → **get_timeline**.
    - Do not use this as a primary navigation or search tool.

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


# ── upload_document_from_staging (UNIFIED UPLOAD) ───────────────


async def upload_document_from_staging(
    staging_token: str = "",
    collection: str = "default",
    filename: str = "",
    file_path: str = "",
) -> str:
    """Upload a document to a collection.

    **To upload a file, use Bash — one command, no context leak**::

        curl -F "file=@/path/to/report.pdf" -F "collection=col_xxx" {base_url}/api/mcp/upload

    That's it.  The server validates, saves, and queues the file for
    processing — all in one HTTP call.  File bytes travel over HTTP only;
    they never enter the LLM context.

    **Only use this MCP tool when you already have a staging_token**
    (e.g. from a prior ``POST /api/mcp/stage-content`` call).  Most of the
    time you should use the Bash + curl one-shot above instead.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name. Use ``list_collections`` first to get IDs.

    ``filename`` overrides the staged filename. ``file_path`` uploads a
    server-local file directly (rarely needed).

    Tokens expire after 10 minutes. Processing is async — use ``list_tasks``.
    """
    from src.services import services
    from src.tasks import task_manager
    from src.mcp.staging import staging_store

    raw: bytes
    use_filename: str

    # ── Way A: server-local file_path ──────────────────────────
    if file_path and not staging_token:
        path = Path(file_path)
        if not path.is_file():
            return to_json(err(f"File not found: {file_path}"))
        try:
            from src.paths import assert_readable_data_file

            resolved = assert_readable_data_file(path)
        except ValueError:
            return to_json(err(
                f"File path must be under data/ (not config, qdrant, or *.db). Got: {file_path}"
            ))
        try:
            raw = path.read_bytes()
        except Exception as exc:
            return to_json(err(f"Failed to read file: {exc}"))
        use_filename = filename.strip() or path.name

    # ── Way B: staging token ───────────────────────────────────
    elif staging_token:
        entry = await staging_store.take(staging_token)
        if entry is None:
            return to_json(err(
                f"Staging token '{staging_token}' not found or expired. "
                f"Tokens expire after 10 minutes. Re-stage the content and try again."
            ))
        raw = entry.content
        use_filename = filename.strip() if filename.strip() else entry.filename

    else:
        return to_json(err(
            "Either staging_token or file_path is required. "
            "For server-local files, use file_path. "
            "For external files, first POST to /api/mcp/stage-content then pass the staging_token."
        ))

    # 2. Validate filename
    try:
        safe_name = safe_filename(use_filename)
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


def _lookup_file_names(
    collection: str, file_id: str | None
) -> tuple[str | None, str | None]:
    """Return ``(filename, display_name)`` for a managed file, if known."""
    if not file_id:
        return None, None
    try:
        from src.file_mgmt.service import get_file_detail

        detail = get_file_detail(collection, file_id)
        filename = getattr(detail, "filename", None) or None
        display_name = getattr(detail, "display_name", None) or filename
        return filename, display_name
    except Exception:
        pass
    try:
        idx = _load_file_index(collection)
        entry = idx.get(file_id) or {}
        label = entry.get("source_label") or entry.get("filename")
        return label, label
    except Exception:
        return None, None


async def get_file_chunks(
    collection: str,
    file_id: str = "",
    source: str = "",
    offset: int = 0,
    limit: int = 50,
    include_context: bool = True,
    chunk_type: str = "*",
) -> Any:
    """List **indexed chunks** for one document (text + metadata, no vectors).

    **When to use**
    - Inspect what was actually embedded/indexed for a known file.
    - Cross-check against ``get_document_text``, or fall back when a historical
      blob is missing (chunks = **current index only**).

    **When not to use**
    - Full-document read / clause extraction → prefer ``get_document_text(file_id)``.
    - Do not know which file → ``search_direct_chunks`` / ``search_agentic_chunks``.

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    **Preferred call:** ``get_file_chunks(collection, file_id=\"…\")``.
    Prefer **file_id** over building source strings. Maps to Qdrant
    ``source=__file__:{file_id}`` (``file:{id}`` alias accepted).

    Returns up to ``limit`` chunks sorted by document order.

    Args:
        collection: Collection ID from ``list_collections``.
        file_id: Preferred managed file id from ``list_files`` /
            ``list_library_tree``.
        source: Optional document source key (if ``file_id`` not set).
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

        fid, source_key, resolve_err = _resolve_doc_identity(
            collection, source=source, file_id=file_id
        )
        if resolve_err:
            return resolve_err
        assert source_key is not None

        # Default: exclude child chunks (redundant with parents at same char_offset)
        _filter_type = chunk_type if chunk_type != "*" else None
        _exclude_child = chunk_type == "*"

        from qdrant_client.models import FieldCondition, Filter, MatchValue
        filter_cond = Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=source_key))]
        )

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
            source=source_key,
            file_id=fid,
            offset=offset,
            limit=limit,
            total=total,
            chunks=chunks,
        )

    return await _await_mcp(_run)


# ── get_document_text ──────────────────────────────────────────


async def get_document_text(
    collection: str,
    file_id: str = "",
    source: str = "",
    version_id: str = "",
    offset: int = 0,
    limit: int = 10000,
) -> Any:
    """Get **plain text** of a document from file-mgmt version storage (or legacy).

    **When to use**
    - Read / summarize / extract clauses from a **known** file (have file_id).
    - Preferred over stitching many search chunks when the target file is known.

    **When not to use**
    - Unknown which file → ``search_direct_chunks`` / ``search_agentic_chunks`` first
      (do **not** call ``list_library_tree`` just to answer content questions).
    - Only check what was indexed → ``get_file_chunks``.
    - Historical version with ``blob_available=false`` (from list_file_versions)
      → will fail; do not invent body from chunks.

    **vs get_file_chunks vs search (pick one primary path)**

    - ``search_*_chunks`` → discovery by query across the collection (default).
    - ``get_document_text`` → full extractable body (current or version_id).
    - ``get_file_chunks`` → indexed slices for one file (current index).

    ``collection`` must be a collection **ID** (e.g. ``"col_abc123"``),
    NOT the display name.

    **Preferred call (file-mgmt):**

        get_document_text(collection=\"col_…\", file_id=\"…\")

    Prefer **file_id**; avoid hand-building source strings. Optional
    ``version_id`` pins a historical blob (check ``blob_available`` first).
    Default = current version.

    Also accepts ``source``:
    - ``__file__:{file_id}`` (canonical Qdrant source)
    - ``file:{file_id}`` (alias)
    - legacy source keys from :func:`list_documents`

    Uses the same extract path as the HTTP Source viewer (``parsed.txt`` /
    ``.extracted.txt`` / re-parse / Qdrant stitch).

    Returns at most ``limit`` characters (default 10000). Pass ``limit=0``
    for unlimited (MCP clients only; in-app Chat clamps this).
    ``offset``/``limit`` are a **character window** into the extractable
    plain text — **not** PDF page numbers.

    Success payload always includes::

        total_chars, offset, limit, returned_chars,
        truncated, has_more, next_offset (or null),
        content, file_id, source, …

    When ``has_more`` is true, call again with ``offset=next_offset`` to
    continue. Prefer starting from a search hit's ``char_offset`` when the
    agent already retrieved a relevant chunk.

    Success structured fields include ``filename`` / ``display_name`` when known.
    Failure: ``extract_status``, ``reason``, ``file_id``, ``source``, ``hint``.

    **Historical versions:** metadata may exist without a blob →
    ``extract_status=blob_missing``. Chunks only reflect the **current** index.

    After MCP signature changes, **restart** SinkDuce + client so tools/list
    refreshes ``file_id`` / ``version_id`` in inputSchema.

    Args:
        collection: Collection ID from ``list_collections``.
        file_id: Preferred managed file id from ``list_files`` /
            ``list_library_tree``.
        source: Optional source key when ``file_id`` is not used.
        version_id: Optional historical version id (current if empty).
        offset: Character offset into extractable text (default 0).
            Use a chunk's ``char_offset`` from search results to jump near
            a relevant passage.
        limit: Max characters to return (default 10000; 0 = unlimited).
    """
    from fastapi import HTTPException

    def _fail(
        message: str,
        *,
        extract_status: str,
        fid: str | None = None,
        source_key: str | None = None,
        vid: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        default_hint = (
            "use get_file_chunks with file_id=… (chunks are for the currently "
            "indexed version; historical body is unavailable without a blob)"
            if extract_status == "blob_missing"
            else "use get_file_chunks with file_id=… or source=__file__:{file_id}"
        )
        hint = extra.pop("hint", None) or default_hint
        fname, dname = _lookup_file_names(collection, fid)
        payload = err(
            message,
            extract_status=extract_status,
            reason=message,
            file_id=fid,
            source=source_key,
            version_id=vid,
            hint=hint,
            **extra,
        )
        if fname:
            payload["filename"] = fname
        if dname:
            payload["display_name"] = dname
        return payload

    def _run() -> dict[str, Any]:
        if e := require_collection(collection):
            e.setdefault("extract_status", "collection_missing")
            return e

        fid, source_key, resolve_err = _resolve_doc_identity(
            collection, source=source, file_id=file_id
        )
        if resolve_err:
            return resolve_err
        assert source_key is not None
        vid = (version_id or "").strip() or None

        try:
            from src.api.routes.documents import get_extracted_text

            result = get_extracted_text(
                filename=source_key,
                collection=collection,
                version_id=vid,
            )
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                msg = str(detail.get("message") or detail.get("detail") or detail)
            else:
                msg = str(detail)
            status = "blob_missing" if exc.status_code == 404 else "extract_failed"
            return _fail(
                msg,
                extract_status=status,
                fid=fid,
                source_key=source_key,
                vid=vid,
                status_code=exc.status_code,
            )
        except Exception as exc:
            logger.exception("get_document_text extract failed")
            return _fail(
                f"Failed to extract text: {exc}",
                extract_status="extract_failed",
                fid=fid,
                source_key=source_key,
                vid=vid,
            )

        full = result.get("text") or ""
        total_chars = len(full)
        # Character window (not PDF pages)
        off = max(0, int(offset or 0))
        lim = int(limit) if limit is not None else 10000
        if lim < 0:
            lim = 10000
        window = full[off : off + lim] if lim > 0 else full[off:]
        returned_chars = len(window)
        end_pos = off + returned_chars
        truncated = bool(end_pos < total_chars)
        has_more = truncated
        next_offset = end_pos if has_more else None
        filename, display_name = _lookup_file_names(collection, fid)

        payload = ok(
            collection=collection,
            source=source_key,
            file_id=fid,
            version_id=vid,
            extract_status="ok" if full else "empty",
            format=result.get("format"),
            total_chars=total_chars,
            offset=off,
            limit=lim,
            returned_chars=returned_chars,
            truncated=truncated,
            has_more=has_more,
            next_offset=next_offset,
            content=window,
            # Alias for agents that expect "text"
            text=window,
        )
        if filename:
            payload["filename"] = filename
        if display_name:
            payload["display_name"] = display_name
        if result.get("preview_hint"):
            payload["preview_hint"] = result["preview_hint"]
        if not full:
            payload["hint"] = (
                "No extractable text for this version; try get_file_chunks "
                "(current index only) or open the Raw blob in the UI"
            )
        elif has_more:
            payload["hint"] = (
                f"Character window [{off}:{end_pos}) of {total_chars} chars; "
                f"has_more=true. If this page is not enough to answer, call "
                f"get_document_text again with offset={next_offset} (same file_id) "
                f"— page forward until evidence is sufficient. Prefer starting from "
                f"a search hit's char_offset when you already know a relevant passage. "
                f"Stop when the answer is complete — do not read the entire file by default."
            )
        else:
            payload["hint"] = (
                f"Character window covers the end of extractable text "
                f"({total_chars} chars total). No further offset needed."
            )
        return payload

    return await _await_mcp(_run)


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

        src = (source or "").strip()
        file_id = ""
        if src.startswith("__file__:"):
            file_id = src[len("__file__:") :].strip()
        elif src.startswith("file:"):
            file_id = src[len("file:") :].strip()
        if file_id:
            from src.file_mgmt import service as fm
            from src.file_mgmt.store import get_db

            conn = get_db(collection)
            try:
                row = conn.execute(
                    "SELECT version, is_definitive FROM files WHERE file_id=?",
                    (file_id,),
                ).fetchone()
            finally:
                conn.close()
            if not row:
                return err(f"File not found for source '{source}'")
            canonical = f"__file__:{file_id}"
            if bool(row["is_definitive"]) == bool(definitive):
                return ok(
                    source=canonical,
                    definitive=bool(definitive),
                    debounce_skipped=True,
                    message="Document definitive flag unchanged",
                )
            fm.update_file(
                collection,
                file_id,
                {
                    "is_definitive": bool(definitive),
                    "version": int(row["version"]),
                },
            )
            return ok(
                source=canonical,
                definitive=bool(definitive),
                message="Document definitive flag updated",
            )

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
    "upload_document_from_staging",
    "delete_document",
    "get_file_chunks",
    "get_document_text",
    "set_document_definitive",
]