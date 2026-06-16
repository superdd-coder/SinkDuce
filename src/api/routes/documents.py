from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import Response

from src.services import services
from src.parsers import parse_directory
from src.tasks import task_manager
from src.tasks.handlers import consolidate_handler, doc_summary_handler, upload_handler
from src.rag.summary_manager import SummaryManager

logger = logging.getLogger(__name__)

router = APIRouter()

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 注册任务处理器
task_manager.register_handler("upload", upload_handler)
task_manager.register_handler("consolidate", consolidate_handler)
task_manager.register_handler("doc_summary", doc_summary_handler)


def _get_summary_manager() -> SummaryManager:
    return SummaryManager(db=services.db)


@router.post("/documents/upload")
async def upload_document(
    files: list[UploadFile] = File(...),
    collection: str = "default",
):
    """上传文件 - 异步队列处理"""
    # Check allowed file types for this collection
    col_config = services.db.get_collection_config(collection) if services.db.collection_exists(collection) else {}
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
        # 保存文件 — sanitize filename to prevent path traversal
        safe_name = Path(file.filename).name
        if not safe_name:
            raise HTTPException(status_code=400, detail="Invalid filename")
        save_path = UPLOAD_DIR / safe_name
        save_path.write_bytes(await file.read())

        # 创建异步任务
        task = task_manager.create_task(
            filename=safe_name,
            task_type="upload",
            file_path=str(save_path),
            collection=collection,
            filename_param=safe_name,
        )
        tasks.append(task.to_dict())

    return {
        "message": f"Queued {len(tasks)} files for processing",
        "tasks": tasks,
    }


@router.get("/documents/tasks")
async def get_tasks(collection: str | None = None):
    """获取任务状态，可按collection过滤"""
    tasks = task_manager.get_all_tasks(collection)
    result = []
    for t in tasks:
        ttype, _ = task_manager._task_args.get(t.id, ("unknown", {}))
        result.append(t.to_dict_with_type(ttype))
    return {
        "tasks": result,
        "pending": len(task_manager.get_pending_tasks(collection)),
        "processing": len(task_manager.get_processing_tasks(collection)),
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
    if not services.db.collection_exists(collection):
        services.db.create_collection(collection, vector_size=services.embedding.dimensions)

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
            collection=collection,
            filename_param=doc.source_path,
        )
        tasks.append(task.to_dict())

    return {
        "message": f"Queued {len(tasks)} documents for processing",
        "tasks": tasks,
    }



@router.delete("/documents/{collection}/{doc_source:path}")
async def delete_document(collection: str, doc_source: str):
    logger.info("[DELETE] Deleting document '%s' from collection='%s'", doc_source, collection)
    services.db.delete_by_filter(collection, key="source", value=doc_source)
    logger.info("[DELETE] Chunks deleted from Qdrant")

    # Delete the source file from uploads
    source_path = UPLOAD_DIR / doc_source
    if source_path.exists():
        source_path.unlink()
        logger.info("[DELETE] Source file deleted: %s", doc_source)

    # Clean up doc summary for this document (non-blocking, best effort)
    try:
        logger.info("[DELETE] Cleaning up doc_summary for '%s'", doc_source)
        sm = _get_summary_manager()
        sm.delete_doc_summary(collection, doc_source)
        logger.info("[DELETE] Doc summary cleaned up")
    except Exception as e:
        logger.warning("[DELETE] Doc summary cleanup failed (non-fatal): %s", e)

    # Clean up meeting allocation if this file came from a meeting
    try:
        from pathlib import Path as _Path
        import json as _json
        meetings_dir = _Path("data").resolve() / "meetings"
        if meetings_dir.exists():
            for entry in meetings_dir.iterdir():
                if not entry.is_dir():
                    continue
                meta_path = entry / "meta.json"
                if not meta_path.exists():
                    continue
                try:
                    data = _json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, _json.JSONDecodeError):
                    continue

                # Check both old and new format
                alloc_cols = data.get("allocated_collections", [])
                file_ids = data.get("allocated_file_ids", [])
                old_col = data.get("allocated_collection")
                old_fid = data.get("allocated_file_id")

                changed = False

                # New format: remove matching entry from parallel lists
                if doc_source in file_ids:
                    idx = file_ids.index(doc_source)
                    file_ids.pop(idx)
                    if idx < len(alloc_cols):
                        alloc_cols.pop(idx)
                    data["allocated_collections"] = alloc_cols
                    data["allocated_file_ids"] = file_ids
                    changed = True

                # Old format: clear if matches
                if old_fid == doc_source:
                    data.pop("allocated_collection", None)
                    data.pop("allocated_file_id", None)
                    changed = True

                if changed:
                    meta_path.write_text(_json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                    logger.info("[DELETE] Removed meeting allocation for '%s' from meeting %s (remaining: %d)",
                                doc_source, data.get("id", "")[:12], len(data.get("allocated_file_ids", [])))
                    break
    except Exception as e:
        logger.warning("[DELETE] Meeting allocation cleanup failed (non-fatal): %s", e)

    # Increment summary change counter
    try:
        col_config = services.db.get_collection_config(collection)
        counter = col_config.get("summary_change_counter", 0) + 1
        threshold = col_config.get("summary_consolidate_threshold", 10)
        services.db.update_collection_config(collection, {"summary_change_counter": counter})
        logger.info("[DELETE] summary_change_counter updated to %d (threshold=%d)", counter, threshold)

        # Auto-trigger consolidation when threshold is reached
        if counter >= threshold:
            logger.info("[DELETE] Counter %d >= threshold %d, triggering consolidation", counter, threshold)
            task_manager.create_task(
                filename=f"consolidate:{collection}",
                task_type="consolidate",
                collection=collection,
            )
    except Exception as e:
        logger.warning("[DELETE] Counter update failed (non-fatal): %s", e)

    return {"message": f"Deleted chunks from {doc_source} in {collection}"}


@router.get("/documents/preview/{filename:path}")
def preview_file(filename: str):
    # Handle full paths - extract just the filename
    filename = Path(filename).name
    file_path = UPLOAD_DIR / filename
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
    parsed_path = file_path.with_suffix(file_path.suffix + ".parsed.txt")
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
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        # Filter out __config__ points (collection config stored as a Qdrant point)
        filter_cond = Filter(must_not=[FieldCondition(key="chunk_type", match=MatchValue(value="__config__"))])

        all_points = []
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
            all_points.extend(points)
            if offset is None:
                break

        source_counts: dict[str, int] = {}
        for p in all_points:
            src = p["payload"].get("source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

        return {"collection": collection, "files": [
            {"source": src, "chunk_count": count}
            for src, count in sorted(source_counts.items())
        ]}

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
