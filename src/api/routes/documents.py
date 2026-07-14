from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import Response

from src.services import services
from src.parsers import parse_directory
from src.tasks import task_manager
from src.tasks.handlers import consolidate_handler, doc_summary_handler, sparse_recalc_handler, upload_handler
from src.rag.summary_manager import SummaryManager
from src.collections import store as collection_store

logger = logging.getLogger(__name__)

router = APIRouter()

COLLECTIONS_DIR = Path("data").resolve() / "collections"

def _files_dir(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "files"

# 注册任务处理器
task_manager.register_handler("upload", upload_handler)
task_manager.register_handler("consolidate", consolidate_handler)
task_manager.register_handler("doc_summary", doc_summary_handler)
task_manager.register_handler("sparse_recalc", sparse_recalc_handler)


def _get_summary_manager() -> SummaryManager:
    return SummaryManager(db=services.db)


@router.post("/documents/upload")
async def upload_document(
    files: list[UploadFile] = File(...),
    collection: str = "default",
):
    """上传文件 - 异步队列处理"""
    # Resolve collection: try as ID first, then display name
    col_meta = (collection_store.get_collection_meta(collection)
                or collection_store.find_collection_by_name(collection))
    collection_id = col_meta["id"] if col_meta else collection

    # Check allowed file types for this collection
    col_config = services.db.get_collection_config(collection_id) if services.db.collection_exists(collection_id) else {}
    allowed = col_config.get("allowed_file_types")
    if allowed:
        rejected = []
        for file in files:
            ext = Path(file.filename).suffix.lower().lstrip(".")
            if ext not in allowed:
                rejected.append(f"{file.filename} (.{ext})")
        if rejected:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed for this database: {', '.join(rejected)}. Allowed: {', '.join(allowed)}",
            )

    tasks = []

    for file in files:
        # 保存文件 — 用 file_id 做目录，防同名冲突
        safe_name = Path(file.filename).name
        if not safe_name:
            raise HTTPException(status_code=400, detail="Invalid filename")
        file_id = uuid.uuid4().hex
        file_source = f"__file__:{file_id}"
        file_dir = _files_dir(collection_id) / file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        save_path = file_dir / safe_name
        # Stream upload to disk in chunks via a thread so the event loop stays
        # responsive while other API calls (e.g. list_files on switch) are in flight.
        loop = asyncio.get_running_loop()
        with open(save_path, "wb") as _fp:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                await loop.run_in_executor(None, _fp.write, chunk)

        # 创建异步任务
        task = task_manager.create_task(
            filename=safe_name,
            task_type="upload",
            file_path=str(save_path),
            collection=collection_id,
            filename_param=file_source,
            source_label=safe_name,
            file_id=file_id,
        )
        tasks.append(task.to_dict())

    return {
        "message": f"Queued {len(tasks)} files for processing",
        "tasks": tasks,
    }


@router.get("/documents/tasks")
async def get_tasks(collection: str | None = None):
    """获取任务状态，可按collection过滤"""
    # Resolve collection ID (try ID first, then display name)
    collection_id = None
    if collection:
        col_meta = (collection_store.get_collection_meta(collection)
                    or collection_store.find_collection_by_name(collection))
        collection_id = col_meta["id"] if col_meta else collection

    tasks = task_manager.get_all_tasks(collection_id)
    result = []
    for t in tasks:
        ttype, _ = task_manager._task_args.get(t.id, ("unknown", {}))
        result.append(t.to_dict_with_type(ttype))
    return {
        "tasks": result,
        "pending": len(task_manager.get_pending_tasks(collection_id)),
        "processing": len(task_manager.get_processing_tasks(collection_id)),
    }


@router.get("/documents/tasks/{task_id}")
async def get_task(task_id: str):
    """获取单个任务状态"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.delete("/documents/tasks/completed")
async def clear_completed_tasks():
    """清除已完成的任务"""
    task_manager.clear_completed_tasks()
    return {"message": "Cleared completed tasks"}


@router.post("/documents/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消正在运行的任务"""
    if task_manager.cancel_task(task_id):
        return {"message": "Task cancelled"}
    raise HTTPException(status_code=400, detail="Task not found or cannot be cancelled")


@router.post("/documents/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    """重试失败的任务"""
    task = task_manager.retry_task(task_id)
    if task:
        return {"message": "Task re-queued", "task": task.to_dict()}
    raise HTTPException(status_code=400, detail="Task not found or not in failed state")


@router.post("/documents/upload-folder")
async def upload_folder(
    path: str,
    collection: str = "default",
):
    """上传文件夹 - 异步队列处理"""
    # Resolve collection: try as ID first, then display name
    col_meta = (collection_store.get_collection_meta(collection)
                or collection_store.find_collection_by_name(collection))
    collection_id = col_meta["id"] if col_meta else collection

    if not services.db.collection_exists(collection_id):
        services.db.create_collection(collection_id, vector_size=services.embedding.dimensions)

    folder = Path(path)
    if not folder.is_dir():
        return {"error": f"Not a directory: {path}"}

    docs = parse_directory(folder)
    tasks = []

    for doc in docs:
        task = task_manager.create_task(
            filename=doc.source_path,
            task_type="upload",
            file_path=doc.source_path,
            collection=collection_id,
            filename_param=doc.source_path,
        )
        tasks.append(task.to_dict())

    return {
        "message": f"Queued {len(tasks)} documents for processing",
        "tasks": tasks,
    }



@router.get("/documents/{collection}/{file_id}/images/{image_id}")
def get_document_image(collection: str, file_id: str, image_id: str):
    """Serve a document image from disk.

    URL pattern: /api/documents/{collection}/{file_id}/images/{image_id}
    Images are stored at data/collections/{collection}/files/{file_id}/images/.
    """
    from pathlib import Path as _Path

    COLLECT_FILES_DIR = _Path("data").resolve() / "collections"

    img_dir = COLLECT_FILES_DIR / collection / "files" / file_id / "images"
    if not img_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Collection {collection} or file {file_id} not found")

    for ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
        img_path = img_dir / f"{image_id}.{ext}"
        if img_path.is_file():
            content = img_path.read_bytes()
            import mimetypes
            mime, _ = mimetypes.guess_type(str(img_path))
            mime = mime or f"image/{ext}"
            return Response(
                content=content,
                media_type=mime,
                headers={
                    "Content-Disposition": f'inline; filename="{image_id}.{ext}"',
                    "Content-Length": str(len(content)),
                    "Cache-Control": "public, max-age=86400",
                },
            )

    raise HTTPException(status_code=404, detail=f"Image {image_id} not found")


@router.delete("/documents/{collection}/{doc_source:path}")
async def delete_document(collection: str, doc_source: str):
    # Resolve collection: try as ID first, then display name
    col_meta = (collection_store.get_collection_meta(collection)
                or collection_store.find_collection_by_name(collection))
    collection_id = col_meta["id"] if col_meta else collection

    logger.info("[DELETE] Deleting document '%s' from collection='%s'", doc_source, collection_id)
    deleted_count = services.db.delete_by_filter(collection_id, key="source", value=doc_source)
    logger.info("[DELETE] %d chunks deleted from Qdrant", deleted_count)

    # Bump sparse recalc counter
    if deleted_count > 0:
        try:
            col_config = services.db.get_collection_config(collection_id)
            sc = col_config.get("sparse_recalc_counter", 0) + deleted_count
            threshold = col_config.get("sparse_recalc_threshold", 5000)
            services.db.update_collection_config(collection_id, {"sparse_recalc_counter": sc})
            logger.info("[SparseRecalc] counter col=%s delta=+%d counter=%d", collection_id, deleted_count, sc)
            if sc >= threshold:
                task_manager.create_task(
                    filename=f"recalc:{collection_id}",
                    task_type="sparse_recalc",
                    collection=collection_id,
                )
                logger.info("[SparseRecalc] triggered for %s", collection_id)
        except Exception as e:
            logger.warning("[SparseRecalc] counter update failed (non-fatal): %s", e)

    # Delete the source file directory via file index lookup
    try:
        from src.collections.file_index import load as load_file_index, remove_by_source as remove_file_index
        idx = load_file_index(collection_id)
        # Find file_id by source
        for fid, entry in idx.items():
            if entry.get("source") == doc_source:
                file_dir = _files_dir(collection_id) / fid
                if file_dir.exists():
                    shutil.rmtree(file_dir)
                remove_file_index(collection_id, doc_source)
                logger.info("[DELETE] Source file deleted: %s -> %s", doc_source, file_dir)
                break
    except Exception as e:
        logger.warning("[DELETE] File index cleanup failed (non-fatal): %s", e)

    # Take snapshot BEFORE doc summary cleanup (for debounce net-change detection)
    pre_snapshot: dict[str, bool] = {}
    try:
        from src.api.routes.info import _snapshot_includes
        pre_snapshot = _snapshot_includes(collection_id)
    except Exception:
        pass

    # Clean up doc summary for this document (non-blocking, best effort)
    try:
        logger.info("[DELETE] Cleaning up doc_summary for '%s'", doc_source)
        sm = _get_summary_manager()
        sm.delete_doc_summary(collection_id, doc_source)
        logger.info("[DELETE] Doc summary cleaned up")
    except Exception as e:
        logger.warning("[DELETE] Doc summary cleanup failed (non-fatal): %s", e)

    # Clean up meeting allocation if this file came from a meeting.
    # Meeting-sourced files use the format __meeting__:{meeting_id}:{tab_id}.
    try:
        import re as _re

        meeting_match = _re.match(r"^__meeting__:([a-f0-9]+):(tab_\w+)$", doc_source)
        if meeting_match:
            mid = meeting_match.group(1)
            tid = meeting_match.group(2)

            from src.meeting import store as meeting_store
            meeting = meeting_store.get_meeting(mid)
            if meeting and meeting.tabs:
                updated_tabs: list[dict] = []
                for t in meeting.tabs:
                    td = t if isinstance(t, dict) else t.model_dump()
                    if td.get("tab_id") == tid:
                        td["allocated_file_id"] = ""
                        td["associated_collection_id"] = ""
                        td["associated_collection_name"] = ""
                    updated_tabs.append(td)

                # Rebuild meeting-level tracking arrays from tabs
                alloc_cols: list[str] = []
                alloc_fids: list[str] = []
                for td in updated_tabs:
                    cid = td.get("associated_collection_id", "")
                    fid = td.get("allocated_file_id", "")
                    if cid and fid:
                        alloc_cols.append(cid)
                        alloc_fids.append(fid)

                meeting_store.update_meeting(
                    mid,
                    tabs=updated_tabs,
                    allocated_collections=alloc_cols,
                    allocated_file_ids=alloc_fids,
                )
                logger.info(
                    "[DELETE] Cleaned meeting tab %s/%s allocation (remaining: %d)",
                    mid[:12], tid, len(alloc_fids),
                )
    except Exception as e:
        logger.warning("[DELETE] Meeting allocation cleanup failed (non-fatal): %s", e)

    # Schedule debounced consolidation (replaces old counter-based trigger).
    # Only enter the debounce flow if the deleted file was a "definitive"
    # document — i.e. it actually contributed to the collection summary
    # (had a doc_summary with include_in_summary != False). Deleting a
    # non-definitive file (no summary, or summary with include=False)
    # cannot change the consolidated output, so skip the 10s timer entirely.
    try:
        from src.api.routes.info import schedule_debounced_consolidate
        was_definitive = pre_snapshot.get(doc_source) is True
        if was_definitive:
            schedule_debounced_consolidate(collection_id, pre_snapshot)
        else:
            logger.info(
                "[DELETE] '%s' was not definitive (not in pre_snapshot or include=False), "
                "skipping debounce", doc_source,
            )
    except Exception as e:
        logger.warning("[DELETE] Debounce schedule failed (non-fatal): %s", e)

    # Trigger coverage refresh after deletion.
    # If upload tasks are running, skip — the last upload will trigger
    # coverage with the correct (post-delete) file list.
    if services.catalog:
        def _trigger():
            try:
                from src.tasks.task_manager import task_manager
                active = len(task_manager.get_active_tasks(
                    collection=collection_id, task_types=["upload", "doc_summary"],
                ))
                if active > 0:
                    logger.info("[Coverage] SKIP delete %r (%d active tasks → last one will trigger)",
                                doc_source, active)
                    services.catalog.mark_dirty(collection_id)
                else:
                    services.catalog.update_coverage(collection_id)
            except Exception:
                logger.exception("[Coverage] delete trigger failed for %s", doc_source)

        import threading
        threading.Thread(target=_trigger, daemon=True).start()

    return {"message": f"Deleted chunks from {doc_source} in {collection_id}"}


def _find_file_path(source: str, collection_id: str | None = None) -> Path | None:
    """Find the preview file for a source identifier."""
    from src.collections.file_index import load as load_file_index
    from pathlib import Path as _Path

    def _first_file(d: Path) -> Path | None:
        """Return the best preview file: PDFs → original, others → parsed.txt."""
        # If original is PDF, return it for iframe rendering
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() == ".pdf":
                return f
        # Prefer parsed.txt for text-based preview
        parsed = d / "parsed.txt"
        if parsed.is_file():
            return parsed
        # Fallback: any file
        for f in sorted(d.iterdir()):
            if f.is_file() and f.name != "parsed.txt":
                return f
        return None

    # If we know the collection, look up directly
    if collection_id:
        idx = load_file_index(collection_id)
        for fid, entry in idx.items():
            if entry.get("source") == source:
                return _first_file(_files_dir(collection_id) / fid)

    # Fallback: search all collections
    if COLLECTIONS_DIR.is_dir():
        for col_dir in COLLECTIONS_DIR.iterdir():
            if not col_dir.is_dir():
                continue
            idx = load_file_index(col_dir.name)
            for fid, entry in idx.items():
                if entry.get("source") == source:
                    return _first_file(_files_dir(col_dir.name) / fid)

    return None


def _read_legacy_text(source: str, collection_id: str) -> str | None:
    """Read chunk text from Qdrant for legacy sources not in files.json."""
    try:
        from src.services import services as _svc
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        pts, _ = _svc.db.scroll_points(
            collection_id, limit=1,
            with_payload=["text"],
            with_vectors=False,
            scroll_filter=Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=source))]
            ),
        )
        if pts:
            return pts[0].get("payload", {}).get("text", "")
    except Exception:
        pass
    return None


@router.get("/documents/preview/{filename:path}")
def preview_file(filename: str, collection: str | None = None):
    # Handle full paths - extract just the name part
    filename = Path(filename).name
    file_path = _find_file_path(filename, collection)

    # Legacy fallback: source not in files.json
    if not file_path and collection:
        legacy_text = _read_legacy_text(filename, collection)
        if legacy_text:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
            tmp.write(legacy_text)
            tmp.close()
            file_path = Path(tmp.name)

    # Fallback: search all collections for legacy source
    if not file_path:
        for col_dir in COLLECTIONS_DIR.iterdir():
            if not col_dir.is_dir():
                continue
            legacy_text = _read_legacy_text(filename, col_dir.name)
            if legacy_text:
                import tempfile
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
                tmp.write(legacy_text)
                tmp.close()
                file_path = Path(tmp.name)
                break

    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    suffix = file_path.suffix.lower()

    # PDF: return raw bytes for iframe rendering
    if suffix == ".pdf":
        content = file_path.read_bytes()
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{file_path.name}"',
                "Content-Length": str(len(content)),
                "Accept-Ranges": "bytes",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # Text-based formats: return raw text directly
    text_types = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".tsv": "text/csv"}
    if suffix in text_types:
        content = file_path.read_bytes()
        return Response(
            content=content,
            media_type=text_types[suffix],
            headers={
                "Content-Disposition": f'inline; filename="{file_path.name}"',
                "Content-Length": str(len(content)),
                "X-Content-Type-Options": "nosniff",
            },
        )

    # All other supported formats: serve stored parsed text (matches chunker offsets)
    parsed_path = file_path.parent / "parsed.txt"
    if parsed_path.is_file():
        content = parsed_path.read_bytes()
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'inline; filename="{file_path.stem}.txt"',
                "Content-Length": str(len(content)),
                "X-Content-Type-Options": "nosniff",
            },
        )

    # Fallback: re-parse (for files uploaded before parsed-text storage was added)
    from src.parsers import PARSERS

    parser = PARSERS.get(suffix)
    if parser is None:
        raise HTTPException(status_code=400, detail=f"Unsupported file format: {suffix}")

    try:
        doc = parser.parse(file_path)
        text = doc.content or "(No text content extracted)"
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse file: {e}")

    # Cache for future requests
    try:
        parsed_path.write_text(text, encoding="utf-8")
    except Exception:
        pass

    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{file_path.stem}.txt"',
            "Content-Length": str(len(text.encode("utf-8"))),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/documents/extracted/{filename:path}")
def get_extracted_text(filename: str, collection: str | None = None):
    """Return parsed/extracted text as JSON with format metadata.

    Response: { "text": "...", "format": "markdown" | "text" }
    """
    filename = Path(filename).name
    file_path = _find_file_path(filename, collection)

    # Legacy fallback: source not in files.json → read from Qdrant
    if not file_path and collection:
        legacy_text = _read_legacy_text(filename, collection)
        if legacy_text:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
            tmp.write(legacy_text)
            tmp.close()
            file_path = Path(tmp.name)

    # Fallback: search all collections for legacy source
    if not file_path:
        for col_dir in COLLECTIONS_DIR.iterdir():
            if not col_dir.is_dir():
                continue
            legacy_text = _read_legacy_text(filename, col_dir.name)
            if legacy_text:
                import tempfile
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
                tmp.write(legacy_text)
                tmp.close()
                file_path = Path(tmp.name)
                break

    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")

    # Get file_type from files.json index
    from src.collections.file_index import load as load_file_index
    fmt = "text"
    if COLLECTIONS_DIR.is_dir():
        for col_dir in COLLECTIONS_DIR.iterdir():
            if not col_dir.is_dir():
                continue
            idx = load_file_index(col_dir.name)
            for fid, entry in idx.items():
                if entry.get("source") == filename:
                    fmt = entry.get("file_type", "text")
                    break

    # Try parsed text first
    parsed_path = file_path.parent / "parsed.txt"
    if parsed_path.is_file():
        text = parsed_path.read_text(encoding="utf-8")
        return {"text": text, "format": fmt}

    # Fallback: re-parse
    suffix = file_path.suffix.lower()
    from src.parsers import PARSERS

    parser = PARSERS.get(suffix)
    if parser is None:
        raise HTTPException(status_code=400, detail=f"Unsupported file format: {suffix}")

    try:
        doc = parser.parse(file_path)
        text = doc.content or "(No text content extracted)"
        fmt = doc.file_type
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse file: {e}")

    return {"text": text, "format": fmt}

@router.get("/documents/{collection}")
def list_documents(collection: str):
    if not services.db.collection_exists(collection):
        return {"collection": collection, "total_chunks": 0, "error": "Collection does not exist"}
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    filter_cond = Filter(must_not=[FieldCondition(key="chunk_type", match=MatchValue(value="__config__"))])
    try:
        count = services.db.count_by_filter(collection, filter_cond)
    except Exception:
        count = services.db.count_points(collection)
    return {"collection": collection, "total_chunks": count}


@router.get("/documents/{collection}/files")
async def list_files(collection: str):
    if not services.db.collection_exists(collection):
        return {"collection": collection, "files": []}

    def _fetch():
        from src.collections.file_index import load as load_file_index

        idx = load_file_index(collection)

        files = []
        # New format: from files.json index — sort by ingest time descending (newest first)
        for fid, entry in sorted(
            idx.items(),
            key=lambda x: x[1].get("ingested_at", 0),
            reverse=True,
        ):
            src = entry.get("source", fid)
            files.append({
                "source": src,
                "chunk_count": entry.get("chunks", 0),
                "file_type": entry.get("file_type", ""),
                "original_ext": entry.get("original_ext", ""),
                "display_name": entry.get("source_label", src),
                "has_meeting": src.startswith("__meeting__:"),
                "note_title": entry.get("source_label", "") if entry.get("file_type") == "note" else "",
            })

        # Legacy: scroll Qdrant for chunks without file_id (created before file_id system).
        # Use is_null filter so Qdrant returns ONLY legacy chunks instead of streaming
        # the entire collection (which is O(N) in chunk count and slow for big collections,
        # especially when the upload path is also hitting Qdrant concurrently).
        from qdrant_client.models import (
            FieldCondition, Filter, MatchValue, IsNullCondition, PayloadField,
        )
        legacy_filter = Filter(
            must=[
                IsNullCondition(is_null=PayloadField(key="file_id")),
            ],
            must_not=[
                FieldCondition(key="chunk_type", match=MatchValue(value="__config__")),
            ],
        )
        legacy_sources: dict[str, int] = {}
        offset = None
        while True:
            points, offset = services.db.scroll_points(
                collection=collection, limit=1000, offset=offset,
                with_payload=["source"],
                with_vectors=False, scroll_filter=legacy_filter,
            )
            for p in points:
                src = p.get("payload", {}).get("source", "unknown")
                legacy_sources[src] = legacy_sources.get(src, 0) + 1
            if offset is None:
                break

        indexed_sources = {e.get("source") for e in idx.values()}
        for src, count in sorted(legacy_sources.items(), key=lambda x: x[0]):
            if src not in indexed_sources:
                files.append({
                    "source": src,
                    "chunk_count": count,
                    "file_type": "",
                    "display_name": src,
                })

        # Attach summary status for each file (definitive toggle state)
        try:
            from src.rag.summary_manager import SummaryManager
            sm = SummaryManager(db=services.db)
            summaries = sm.get_doc_summaries(collection, included_only=False)
            summary_map: dict[str, dict] = {s["source"]: s for s in summaries}
            for f in files:
                ds = summary_map.get(f["source"])
                f["has_summary"] = ds is not None
                f["include_in_summary"] = ds.get("include_in_summary", True) is not False if ds else None
        except Exception:
            for f in files:
                f["has_summary"] = None
                f["include_in_summary"] = None

        return {"collection": collection, "files": files}

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


@router.get("/documents/{collection}/files/{source:path}/chunks")
def get_file_chunks(collection: str, source: str, limit: int = 100, offset: int = 0):
    if not services.db.collection_exists(collection):
        return {"collection": collection, "source": source, "chunks": [], "total": 0}

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    filter_cond = Filter(
        must=[FieldCondition(key="source", match=MatchValue(value=source))]
    )

    total = services.db.count_by_filter(collection, filter_cond)

    # Fetch ALL chunks for the file, then sort, then paginate.
    # Qdrant returns chunks in insertion order, not sorted by chunk_index,
    # so we must sort before applying limit/offset pagination.
    all_points, _ = services.db.scroll_points(
        collection=collection,
        limit=total if total > 0 else 10000,
        offset=None,
        scroll_filter=filter_cond,
        with_payload=True,
        with_vectors=False,
    )

    chunks = [
        {
            "id": p["id"],
            "text": p["payload"].get("text", ""),
            "chunk_index": p["payload"].get("chunk_index", 0),
            "file_type": p["payload"].get("file_type", ""),
            "context": p["payload"].get("context", ""),
            "chunk_type": p["payload"].get("chunk_type", "normal"),
            "parent_id": p["payload"].get("parent_id"),
            "summary": p["payload"].get("summary", ""),
            # Position fields for source navigation
            "char_offset": p["payload"].get("char_offset"),
            "page_number": p["payload"].get("page_number"),
            "slide_number": p["payload"].get("slide_number"),
            "section_label": p["payload"].get("section_label"),
            "heading_path": p["payload"].get("heading_path"),
            "note_id": p["payload"].get("note_id", ""),
            "meeting_id": p["payload"].get("meeting_id", ""),
        }
        for p in all_points
    ]
    # Sort: group parent with its children (parent0, child0_0, child0_1, parent1, child1_0, ...)
    parent_idx_map = {c["id"]: c["chunk_index"] for c in chunks if c.get("chunk_type") == "parent"}
    def _sort_key(c):
        ct = c.get("chunk_type", "normal")
        ci = c.get("chunk_index", 0)
        pid = c.get("parent_id")
        if ct == "parent":
            return (ci, 0, 0)  # parent comes before its children
        elif ct == "child":
            parent_ci = parent_idx_map.get(pid, 9999)
            return (parent_ci, 1, ci)  # children after their parent, ordered by chunk_index
        else:
            return (ci, 0, 0)
    chunks.sort(key=_sort_key)

    # Apply pagination after sorting
    chunks = chunks[offset : offset + limit]

    return {
        "collection": collection,
        "source": source,
        "chunks": chunks,
        "total": total,
    }
