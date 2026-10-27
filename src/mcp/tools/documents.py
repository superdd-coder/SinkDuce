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

from mcp.server.fastmcp import Context

from src.config import DATA_DIR
from src.mcp.common import (
    module_gated,
    err,
    mcp_result,
    ok,
    require_collection,
    run_sync,
    safe_filename,
    to_json,
)
from src.hub.confirm import confirm_destructive
from src.hub.routing import _require_collection_on_remote, body_without, hub_routed
from src.hub.upload_forwards import upload_document_forward

logger = logging.getLogger(__name__)

# 迁往 src/documents_views（批次 4：与 REST 端点共用），保留工具侧别名
from src.documents_views import (  # noqa: E402
    lookup_file_names as _lookup_file_names,
    resolve_doc_identity as _resolve_doc_identity,
)

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




# Task handlers (upload, consolidate, doc_summary, sparse_recalc) are now
# registered by ``src.main.lifespan`` so the same handler set is shared
# between the FastAPI HTTP routes and the MCP sub-app mounted at /mcp.


# ── list_documents ─────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/agent/collections/{collection}/documents",
    precheck=_require_collection_on_remote,
    forwards=("collection",),
)
async def list_documents(collection: str, ctx: Context | None = None) -> str:
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
    def _run() -> dict[str, Any]:
        from src.documents_views import documents_list_view

        return documents_list_view(collection)

    return to_json(await run_sync(_run))


# ── upload_document_from_staging (UNIFIED UPLOAD) ───────────────


@module_gated("chat", "database")
@hub_routed(
    # 跨盒子（批次 5）：Hub 取本机 staging 字节 → multipart 直传远端
    # /documents/upload。file_path 一样成立（那是 Hub 机器上的 data/ 文件）。
    forward=upload_document_forward,
    precheck=_require_collection_on_remote,
    timeout=300.0,
    forwards=("staging_token", "collection", "filename", "file_path"),
)
async def upload_document_from_staging(
    staging_token: str = "",
    collection: str = "default",
    filename: str = "",
    file_path: str = "",
    ctx: Context | None = None,
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


#: ``/api/documents/`` 下的**保留首段**（兄弟端点）。delete_document 转发拼的
#: 路径是 ``/documents/{collection}/{source}``，若某库的 id/显示名恰好叫
#: ``tasks``，``delete_document("tasks", "completed")`` 会撞上
#: ``DELETE /documents/tasks/completed``（清任务记录）——静默删错对象。
#: 路由注册顺序保证不了这层（守卫在拼路径之前拒才可靠）。
_RESERVED_DOCUMENT_SEGMENTS = frozenset({
    "tasks", "preview", "extracted", "upload-folder", "ingest-history",
})


@module_gated("chat", "database")
@confirm_destructive()
@hub_routed(
    method="DELETE",
    # REST 路由是 ``{doc_source:path}`` —— source 本身可含斜杠（相对路径），
    # 所以这个参数显式 allow_slash；``..`` 穿越仍被 quote_segment 拒。
    path="/documents/{collection}/{source}",
    precheck=_require_collection_on_remote,
    refuse=lambda a: (
        f"'{a.get('collection')}' collides with a reserved /documents/ sub-path "
        "on the remote API; refusing rather than risking acting on the wrong "
        "resource. Rename the collection or run this locally."
        if str(a.get("collection") or "") in _RESERVED_DOCUMENT_SEGMENTS
        else None
    ),
    # REST（本轮对齐后）回 {message, deleted_chunks}；工具契约是
    # ok(message=工具文案, deleted_chunks, source)。
    shape=lambda payload, a: (
        payload
        if not isinstance(payload, dict)
        else {
            "message": f"Deleted '{a.get('source')}' from '{a.get('collection')}'",
            "deleted_chunks": (payload or {}).get("deleted_chunks", 0),
            "source": a.get("source"),
        }
    ),
    forwards=("collection", "source"),
    allow_slash=("source",),
)
async def delete_document(collection: str, source: str, ctx: Context | None = None) -> str:
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
                services.db.update_collection_config(collection, {"sparse_recalc_counter": sc})
                logger.info(
                    "[SparseRecalc] counter col=%s delta=+%d counter=%d threshold=%d",
                    collection, deleted_count, sc, threshold,
                )
                if sc >= threshold:
                    from src.tasks.handlers import _schedule_sparse_recalc
                    _schedule_sparse_recalc(collection, reason="after delete (mcp)")
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




@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/agent/collections/{collection}/chunks",
    precheck=_require_collection_on_remote,
    query=lambda a: {
        "file_id": a.get("file_id") or None,
        "source": a.get("source") or None,
        "offset": a.get("offset", 0),
        "limit": a.get("limit", 50),
        # 假值规则：显式 False 必须存活（bool(a.get(k, 默认))，不是 or None）
        "include_context": bool(a.get("include_context", True)),
        # ⚠️ 空串是**文档化的合法值**（chunk_type="" = 包含 child 块），
        # 不能 `or "*"`——那会把「包含子块」静默变成「排除子块」（审查 #1）。
        "chunk_type": a.get("chunk_type") if a.get("chunk_type") is not None else "*",
    },
    forwards=("collection", "file_id", "source", "offset", "limit",
              "include_context", "chunk_type"),
)
async def get_file_chunks(
    collection: str,
    file_id: str = "",
    source: str = "",
    offset: int = 0,
    limit: int = 50,
    include_context: bool = True,
    chunk_type: str = "*",
    ctx: Context | None = None,
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
    def _run() -> dict[str, Any]:
        from src.documents_views import file_chunks_view

        return file_chunks_view(
            collection,
            file_id=file_id,
            source=source,
            offset=offset,
            limit=limit,
            include_context=include_context,
            chunk_type=chunk_type,
        )

    return await _await_mcp(_run)


# ── get_document_text ──────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/documents/text",
    precheck=_require_collection_on_remote,
    # 提取路径首次可能触发重解析/重拼（>20s）——默认超时会把它误报成
    # 「盒子连不上」（真实走查实测踩到）
    timeout=90.0,
    query=lambda a: {
        "collection": a.get("collection"),
        "file_id": a.get("file_id") or None,
        "source": a.get("source") or None,
        "version_id": a.get("version_id") or None,
        "offset": a.get("offset", 0),
        "limit": a.get("limit", 10000),
    },
    forwards=("collection", "file_id", "source", "version_id", "offset", "limit"),
)
async def get_document_text(
    collection: str,
    file_id: str = "",
    source: str = "",
    version_id: str = "",
    offset: int = 0,
    limit: int = 10000,
    ctx: Context | None = None,
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
    def _run() -> dict[str, Any]:
        from src.documents_views import document_text_view

        return document_text_view(
            collection,
            file_id=file_id,
            source=source,
            version_id=version_id,
            offset=offset,
            limit=limit,
        )

    return await _await_mcp(_run)


# ── set_document_definitive ──────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="POST",
    path="/documents/{collection}/definitive",
    precheck=_require_collection_on_remote,
    body=body_without("collection"),
    forwards=("collection", "source", "definitive"),
)
async def set_document_definitive(
    collection: str,
    source: str,
    definitive: bool = True,
    ctx: Context | None = None,
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
        from src.documents_views import document_definitive_view

        return document_definitive_view(collection, source, definitive=definitive)

    return to_json(await run_sync(_run))


__all__ = [
    "list_documents",
    "upload_document_from_staging",
    "delete_document",
    "get_file_chunks",
    "get_document_text",
    "set_document_definitive",
]