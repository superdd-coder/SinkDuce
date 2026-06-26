from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

COLLECTIONS_DIR = Path("data").resolve() / "collections"

def _files_dir(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "files"

# Ensure task handlers are registered (idempotent — safe even if HTTP routes also register them)
from src.tasks import task_manager
from src.tasks.handlers import consolidate_handler, doc_summary_handler, sparse_recalc_handler, upload_handler

task_manager.register_handler("upload", upload_handler)
task_manager.register_handler("consolidate", consolidate_handler)
task_manager.register_handler("doc_summary", doc_summary_handler)
task_manager.register_handler("sparse_recalc", sparse_recalc_handler)


async def list_documents(collection: str) -> str:
    """List all documents in a collection with their chunk counts.

    Use this to discover document filenames (source) needed by `delete_document` and `get_doc_summary`.
    """
    from src.services import services

    def _run():
        if not services.db.collection_exists(collection):
            return {"error": f"Collection '{collection}' does not exist"}

        from qdrant_client.models import FieldCondition, Filter, MatchValue

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
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


async def upload_document(file_path: str, collection: str = "default") -> str:
    """Upload a document to a collection for indexing. Processing is async — use `get_task_status` to check progress.

    The file must be accessible on the server filesystem. Supported formats: PDF, DOCX, XLSX, PPTX, MD, TXT, CSV, HTML, JSON.
    """
    from src.services import services

    path = Path(file_path)
    if not path.is_file():
        return json.dumps({"error": f"File not found: {file_path}"})

    def _run():
        if services.db.collection_exists(collection):
            col_config = services.db.get_collection_config(collection)
            allowed = col_config.get("allowed_file_types")
            if allowed:
                ext = path.suffix.lower().lstrip(".")
                if ext not in allowed:
                    return {"error": f"File type '.{ext}' not allowed. Allowed: {', '.join(allowed)}"}

        safe_name = path.name
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
        return {"message": "File queued for processing", "task_id": task.id, "filename": safe_name}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False)


async def upload_folder(path: str, collection: str = "default") -> str:
    """Batch-import all supported documents from a server directory.

    Each file is queued as a separate async task. Use `get_task_status` to monitor progress.
    """
    from src.services import services
    from src.parsers import parse_directory

    folder = Path(path)
    if not folder.is_dir():
        return json.dumps({"error": f"Not a directory: {path}"})

    def _run():
        if not services.db.collection_exists(collection):
            services.db.create_collection(collection, vector_size=services.embedding.dimensions)

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
            tasks.append({"task_id": task.id, "filename": doc.source_path})
        return {"message": f"Queued {len(tasks)} documents for processing", "tasks": tasks}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


async def delete_document(collection: str, source: str) -> str:
    """Delete a document from a collection — removes all chunks, the source file, and its summary.

    Use `list_documents` first to get the correct source filename.
    """
    from src.services import services
    from src.rag.summary_manager import SummaryManager

    def _run():
        if not services.db.collection_exists(collection):
            return {"error": f"Collection '{collection}' does not exist"}

        deleted_count = services.db.delete_by_filter(collection, key="source", value=source)

        # Delete file directory via file index
        try:
            from src.collections.file_index import load as load_file_index, remove_by_source as remove_file_index
            idx = load_file_index(collection)
            for fid, entry in idx.items():
                if entry.get("source") == source:
                    file_dir = _files_dir(collection) / fid
                    if file_dir.exists():
                        shutil.rmtree(file_dir)
                    remove_file_index(collection, source)
                    break
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
                logger.info("[SparseRecalc] counter col=%s delta=+%d counter=%d threshold=%d",
                            collection, deleted_count, sc, threshold)
                if sc >= threshold:
                    task_manager.create_task(
                        filename=f"recalc:{collection}",
                        task_type="sparse_recalc",
                        collection=collection,
                    )
                    logger.info("[SparseRecalc] triggered for %s (counter=%d >= threshold=%d)",
                                collection, sc, threshold)
            except Exception as e:
                logger.warning("[SparseRecalc] counter update failed (non-fatal): %s", e)

        try:
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

        return {"message": f"Deleted '{source}' from '{collection}'"}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False)


async def get_task_status(task_id: str) -> str:
    """Check the status and progress of an async task (upload, transcription, consolidation, etc.).

    Returns status (pending/processing/completed/failed), progress percentage, and any error details.
    """
    from src.tasks import task_manager as tm

    task = tm.get_task(task_id)
    if not task:
        return json.dumps({"error": f"Task '{task_id}' not found"})
    return json.dumps(task.to_dict(), ensure_ascii=False, default=str)
