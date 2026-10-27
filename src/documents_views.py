"""documents 域的 **Agent 视图函数**（批次 4：REST 新端点与 MCP 工具共用）。

`document_text_view` / `document_definitive_view` 与 MCP 工具的本地输出**同一份**；
``_resolve_doc_identity`` / ``_lookup_file_names`` 从工具模块迁来这里（工具侧
反向 import——REST 层不该依赖 mcp 包）。

放顶层模块而不是 api/routes 里：视图函数要 import 路由层的 ``get_extracted_text``
（延迟），若视图也住在路由模块里就循环了。
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp.common import err, ok, require_collection

logger = logging.getLogger("documents_views")


def _load_file_index(collection: str) -> dict:
    from src.collections.file_index import load

    return load(collection)


def resolve_doc_identity(
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


def lookup_file_names(
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


def _fail(
    collection: str,
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
    fname, dname = lookup_file_names(collection, fid)
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


def document_text_view(
    collection: str,
    file_id: str = "",
    source: str = "",
    version_id: str = "",
    offset: int = 0,
    limit: int = 10000,
) -> dict[str, Any]:
    """`get_document_text` 工具的信封：身份解析 → R6/R5 隐藏闸门 → 提取 →
    字符窗口分页。失败也返回 err dict（200 语义，与工具一致）。"""
    from fastapi import HTTPException

    if e := require_collection(collection):
        e.setdefault("extract_status", "collection_missing")
        return e

    fid, source_key, resolve_err = resolve_doc_identity(
        collection, source=source, file_id=file_id
    )
    if resolve_err:
        return resolve_err
    # v2 R6/R5: read tools hide Unassigned-only AND file-level archived files
    from src.file_mgmt.files import archived_or_unassigned_file_ids

    if fid and fid in archived_or_unassigned_file_ids(collection, [fid]):
        return err(
            "File not available — it is archived or in the Unassigned folder",
            extract_status="hidden",
            file_id=fid,
            source=source_key,
        )

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
            collection,
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
            collection,
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
    filename, display_name = lookup_file_names(collection, fid)

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
            "— page forward until evidence is sufficient. Prefer starting from "
            "a search hit's char_offset when you already know a relevant passage. "
            "Stop when the answer is complete — do not read the entire file by default."
        )
    else:
        payload["hint"] = (
            f"Character window covers the end of extractable text "
            f"({total_chars} chars total). No further offset needed."
        )
    return payload


def document_definitive_view(
    collection: str, source: str, definitive: bool = True
) -> dict[str, Any]:
    """`set_document_definitive` 工具的信封（file-mgmt 路径 + 摘要路径 +
    防抖 consolidate 触发）。"""
    from src.services import services
    from src.rag.summary_manager import SummaryManager

    if e := require_collection(collection):
        return e
    # 写授权（审查 #5）：file-mgmt 分支在 update_file 内部自带，但摘要分支
    # 此前只有读级可见性——批次 4 把它暴露到 REST 后，只读钥匙能翻
    # include_in_summary 并触发 consolidate。补在**任何副作用之前**。
    from src.api.routes.documents import _assert_col_write

    _assert_col_write(collection, "file.update")

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


__all__ = [
    "document_definitive_view",
    "document_text_view",
    "lookup_file_names",
    "resolve_doc_identity",
]


def documents_list_view(collection: str) -> dict[str, Any]:
    """`list_documents` 工具的信封：文件索引（缺则 Qdrant 水化重建）。"""
    from src.services import services

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


def file_chunks_view(
    collection: str,
    file_id: str = "",
    source: str = "",
    offset: int = 0,
    limit: int = 50,
    include_context: bool = True,
    chunk_type: str = "*",
) -> dict[str, Any]:
    """`get_file_chunks` 工具的信封（身份解析 + R6/R5 闸门 + 类型过滤 + 排序）。"""
    from src.services import services

    if e := require_collection(collection):
        return e

    fid, source_key, resolve_err = resolve_doc_identity(
        collection, source=source, file_id=file_id
    )
    if resolve_err:
        return resolve_err
    # v2 R6/R5: read tools hide Unassigned-only AND file-level archived files
    from src.file_mgmt.files import archived_or_unassigned_file_ids

    if fid and fid in archived_or_unassigned_file_ids(collection, [fid]):
        return err(
            "File not available — it is archived or in the Unassigned folder",
            extract_status="hidden",
            file_id=fid,
            source=source_key,
        )

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
                "id": p.get("id"),
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


def doc_summary_view(collection: str, source: str) -> dict[str, Any]:
    """`get_doc_summary` 工具的信封（未分配闸门 + 摘要或 err）。"""
    from src.rag.summary_manager import SummaryManager
    from src.services import services

    if e := require_collection(collection):
        return e
    # v2 R6: Unassigned-only files are invisible to AI tools
    if source.startswith("__file__:"):
        fid = source[len("__file__:"):]
        from src.file_mgmt.files import unassigned_only_file_ids

        if fid in unassigned_only_file_ids(collection, [fid]):
            return err(
                f"No summary found for document '{source}' in collection '{collection}'"
            )
    sm = SummaryManager(db=services.db)
    summary = sm.get_doc_summary(collection, source)
    if summary is None:
        return err(f"No summary found for document '{source}' in collection '{collection}'")
    return summary


__all__ += ["doc_summary_view", "documents_list_view", "file_chunks_view"]
