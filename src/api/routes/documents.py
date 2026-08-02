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
    Images live under ``files/{file_id}/{version_id}/images/`` (or legacy
    ``files/{file_id}/images/``).
    """
    from src.file_mgmt.storage_paths import find_image_file

    img_path = find_image_file(collection, file_id, image_id)
    if img_path is None:
        raise HTTPException(status_code=404, detail=f"Image {image_id} not found")

    content = img_path.read_bytes()
    import mimetypes

    mime, _ = mimetypes.guess_type(str(img_path))
    mime = mime or f"image/{img_path.suffix.lstrip('.') or 'png'}"
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Content-Disposition": f'inline; filename="{img_path.name}"',
            "Content-Length": str(len(content)),
            "Cache-Control": "public, max-age=86400",
        },
    )

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


def _current_version_meta(
    collection_id: str, file_id: str
) -> tuple[Path | None, bool]:
    """Return (current version path, files.unsupported) for a managed file.

    *unsupported* is **current-version only** (ingest eligibility). Historical
    version blobs under the same file_id must still be previewable via
    ``storage_file`` regardless of this flag.
    """
    try:
        from src.file_mgmt.store import get_db
        from src.file_mgmt.storage_paths import (
            ensure_layout_migrated,
            resolve_version_blob,
        )

        ensure_layout_migrated(collection_id)
        conn = get_db(collection_id)
        try:
            row = conn.execute(
                """SELECT fv.storage_file_id, fv.version_id, f.unsupported
                   FROM files f
                   JOIN file_versions fv ON fv.version_id = f.current_version_id
                   WHERE f.file_id=?""",
                (file_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None, False
        p = resolve_version_blob(
            collection_id,
            file_id,
            row["version_id"],
            row["storage_file_id"],
        )
        return p, bool(row["unsupported"])
    except Exception:
        logger.debug(
            "Could not resolve current version for %s/%s",
            collection_id,
            file_id,
            exc_info=True,
        )
        return None, False


def _current_version_file(collection_id: str, file_id: str) -> Path | None:
    """Resolve the on-disk path of the file's *current* version (file-mgmt).

    Multiple version blobs may coexist under ``files/{file_id}/``; always prefer
    the ``storage_file_id`` of ``files.current_version_id`` so preview matches
    the latest upload (including unsupported types that never rewrite parsed.txt).
    """
    path, _ = _current_version_meta(collection_id, file_id)
    return path


def _is_current_storage_file(
    collection_id: str, file_id: str, storage_file: str | None
) -> bool:
    """True when *storage_file* is the file's current version blob name.

    Compares DB ``storage_file_id`` even when the on-disk blob is missing
    (e.g. unsupported image never written / deleted) so preview flags stay correct.
    """
    if not storage_file:
        return False
    want = Path(storage_file).name
    cur = _current_version_file(collection_id, file_id)
    if cur is not None:
        return cur.name == want
    # Disk missing — still resolve name from current version row
    try:
        from src.file_mgmt.store import get_db

        conn = get_db(collection_id)
        try:
            row = conn.execute(
                """SELECT fv.storage_file_id FROM files f
                   JOIN file_versions fv ON fv.version_id = f.current_version_id
                   WHERE f.file_id=?""",
                (file_id,),
            ).fetchone()
        finally:
            conn.close()
        if row and row["storage_file_id"]:
            return Path(row["storage_file_id"]).name == want
    except Exception:
        pass
    return False


def _file_id_from_source(source: str) -> str | None:
    if source.startswith("__file__:"):
        return source[len("__file__:") :]
    return None


def _find_file_path(
    source: str,
    collection_id: str | None = None,
    *,
    storage_file: str | None = None,
    version_id: str | None = None,
    prefer_original: bool = False,
) -> Path | None:
    """Find the on-disk path for a document source.

    Optional *storage_file* / *version_id* force a specific version blob under
    the managed file directory (e.g. a non-current version from the Log).

    When *prefer_original* is True (Raw preview), never substitute ``parsed.txt``
    for Office binaries — File Viewer needs the real ``.docx`` / ``.xlsx`` / etc.
    """
    from src.collections.file_index import load as load_file_index

    # Explicit version pin — never fall through to current if missing.
    if (
        (storage_file or version_id)
        and collection_id
        and source.startswith("__file__:")
    ):
        from src.file_mgmt.storage_paths import (
            ensure_layout_migrated,
            resolve_version_blob,
            storage_basename,
        )

        ensure_layout_migrated(collection_id)
        fid = source[len("__file__:") :]
        safe = storage_basename(storage_file) if storage_file else ""

        # 1) Pin by version_id (preferred — unique)
        if version_id:
            try:
                from src.file_mgmt.store import get_db

                conn = get_db(collection_id)
                try:
                    vr = conn.execute(
                        """SELECT version_id, storage_file_id FROM file_versions
                           WHERE file_id=? AND version_id=?""",
                        (fid, version_id),
                    ).fetchone()
                finally:
                    conn.close()
                if vr:
                    p = resolve_version_blob(
                        collection_id,
                        fid,
                        vr["version_id"],
                        vr["storage_file_id"] or safe or None,
                    )
                    if p is not None:
                        return p
                # version dir may hold blob even if DB name drifted
                p = resolve_version_blob(
                    collection_id, fid, version_id, safe or None
                )
                if p is not None:
                    return p
            except Exception:
                logger.debug(
                    "version_id resolve failed for %s/%s/%s",
                    collection_id,
                    fid,
                    version_id,
                    exc_info=True,
                )
            if storage_file is None and not safe:
                return None

        # 2) Pin by storage basename (ambiguous if legacy shared names)
        if safe and safe != "parsed.txt":
            try:
                from src.file_mgmt.store import get_db

                conn = get_db(collection_id)
                try:
                    vrows = conn.execute(
                        """SELECT version_id, storage_file_id FROM file_versions
                           WHERE file_id=? ORDER BY version_no ASC""",
                        (fid,),
                    ).fetchall()
                finally:
                    conn.close()
                matches = [
                    vr
                    for vr in vrows
                    if storage_basename(vr["storage_file_id"]) == safe
                    or (vr["storage_file_id"] or "") == storage_file
                ]
                # Prefer exact version_id if already known; else only return if
                # a single match has a blob (avoid wrong-version for shared names).
                found: list[Path] = []
                for vr in matches:
                    p = resolve_version_blob(
                        collection_id,
                        fid,
                        vr["version_id"],
                        vr["storage_file_id"],
                    )
                    if p is not None:
                        found.append(p)
                if len(found) == 1:
                    return found[0]
                if version_id:
                    for vr in matches:
                        if vr["version_id"] == version_id:
                            p = resolve_version_blob(
                                collection_id,
                                fid,
                                vr["version_id"],
                                vr["storage_file_id"],
                            )
                            if p is not None:
                                return p
            except Exception:
                logger.debug(
                    "storage_file resolve failed for %s/%s", collection_id, fid,
                    exc_info=True,
                )
            # Legacy flat last chance for this basename only
            p_flat = _files_dir(collection_id) / fid / safe
            if p_flat.is_file():
                return p_flat
            return None
        if version_id:
            return None

    def _preview_for_file_dir(
        d: Path, *, current_name: str | None = None
    ) -> Path | None:
        """Best path inside a version dir (or legacy flat managed file dir)."""
        if not d.is_dir():
            return None
        if current_name:
            cur = d / Path(current_name).name
            if cur.is_file() and cur.name != "parsed.txt":
                if prefer_original:
                    # Raw: always the real blob (docx/xlsx/pptx/pdf/md/…)
                    return cur
                # Source-oriented: prefer parsed.txt for Office when fresher
                # (legacy callers that want text without re-parse).
                suffix = cur.suffix.lower()
                parsed = d / "parsed.txt"
                if (
                    suffix not in {".pdf", ".txt", ".md", ".csv", ".tsv"}
                    and parsed.is_file()
                    and parsed.stat().st_mtime >= cur.stat().st_mtime
                ):
                    return parsed
                return cur
        # Legacy: PDF first for iframe
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() == ".pdf":
                return f
        if not prefer_original:
            parsed = d / "parsed.txt"
            if parsed.is_file():
                return parsed
        for f in sorted(d.iterdir()):
            if (
                f.is_file()
                and f.name != "parsed.txt"
                and not f.name.endswith(".extracted.txt")
            ):
                return f
        return None

    def _resolve_in_collection(col: str, src: str) -> Path | None:
        # Managed file source: __file__:{file_id}
        if src.startswith("__file__:"):
            fid = src[len("__file__:") :]
            cur = _current_version_file(col, fid)
            if cur is not None:
                return _preview_for_file_dir(cur.parent, current_name=cur.name)
            return _preview_for_file_dir(_files_dir(col) / fid)

        idx = load_file_index(col)
        for fid, entry in idx.items():
            if entry.get("source") == src:
                cur = _current_version_file(col, fid)
                if cur is not None:
                    return _preview_for_file_dir(cur.parent, current_name=cur.name)
                return _preview_for_file_dir(_files_dir(col) / fid)
        return None

    # If we know the collection, look up directly
    if collection_id:
        found = _resolve_in_collection(collection_id, source)
        if found is not None:
            return found

    # Fallback: search all collections
    if COLLECTIONS_DIR.is_dir():
        for col_dir in COLLECTIONS_DIR.iterdir():
            if not col_dir.is_dir():
                continue
            found = _resolve_in_collection(col_dir.name, source)
            if found is not None:
                return found

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
def preview_file(
    filename: str,
    collection: str | None = None,
    storage_file: str | None = None,
    version_id: str | None = None,
):
    # Handle full paths - extract just the name part
    # Note: __file__:{id} uses colon — do not Path().name-strip the source key
    source_key = filename
    if not filename.startswith("__") and "/" in filename:
        source_key = Path(filename).name
    # Raw preview: always prefer the original blob (never parsed.txt for Office).
    file_path = _find_file_path(
        source_key,
        collection,
        storage_file=storage_file,
        version_id=version_id,
        prefer_original=True,
    )

    # When a specific history blob was requested, do not substitute legacy text
    # or scan other collections — that would silently show the wrong version.
    if not file_path and storage_file:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Version blob not found: {Path(storage_file).name}. "
                "This old version may have been overwritten before unique "
                "storage names were introduced, or the file was never kept on disk."
            ),
        )

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

    # Never serve shared parse cache as "original" even if path resolution fails open
    if file_path.name == "parsed.txt":
        raise HTTPException(
            status_code=404,
            detail="Original file blob not found (only parsed text cache exists)",
        )

    suffix = file_path.suffix.lower()
    content = file_path.read_bytes()

    media_map = {
        ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".markdown": "text/markdown; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".tsv": "text/tab-separated-values; charset=utf-8",
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".log": "text/plain; charset=utf-8",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".docm": "application/vnd.ms-word.document.macroEnabled.12",
        ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    }
    media = media_map.get(suffix, "application/octet-stream")

    safe_ascii_name = _ascii_filename(file_path.name)
    return Response(
        content=content,
        media_type=media,
        headers={
            # Latin-1 only in raw header values — Chinese/CJK names must use
            # RFC 5987 filename* or an ASCII fallback (Starlette encodes headers
            # as latin-1 and raises 400 on non-ASCII header bytes).
            "Content-Disposition": _content_disposition_inline(file_path.name),
            "Content-Length": str(len(content)),
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
            # ASCII-only: File Viewer uses this for extension routing when present
            "X-File-Name": safe_ascii_name,
        },
    )


def _ascii_filename(filename: str) -> str:
    """ASCII-safe basename for HTTP headers (latin-1 constraint)."""
    name = Path(filename).name or "file"
    ascii_name = (
        name.encode("ascii", "replace")
        .decode("ascii")
        .replace('"', "_")
        .replace("\\", "_")
        .replace("?", "_")
    )
    # Prefer keeping extension for File Viewer routing
    if not ascii_name or ascii_name in {".", ".."} or all(c in "._?" for c in ascii_name):
        suf = Path(name).suffix
        ascii_name = f"file{suf}" if suf else "file.bin"
    return ascii_name


def _content_disposition_inline(filename: str) -> str:
    """Build a Content-Disposition value safe for HTTP headers (latin-1)."""
    from urllib.parse import quote

    name = Path(filename).name or "file"
    ascii_name = _ascii_filename(name)
    # RFC 5987 UTF-8 filename*
    utf8_star = quote(name, safe="")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_star}"


@router.get("/documents/extracted/{filename:path}")
def get_extracted_text(
    filename: str,
    collection: str | None = None,
    storage_file: str | None = None,
    version_id: str | None = None,
):
    """Return parsed/extracted text as JSON with format metadata.

    Response: { "text": "...", "format": "markdown" | "text" }

    Optional *storage_file* selects a specific version blob for preview
    (Log → version-update message detail / All Files → Old versions).

    Optional *version_id* pins Qdrant stitch to that version when multiple
    history rows share the same storage basename (legacy overwrite case).
    """
    source_key = filename
    if not filename.startswith("__") and "/" in filename:
        source_key = Path(filename).name
    file_path = _find_file_path(
        source_key,
        collection,
        storage_file=storage_file,
        version_id=version_id,
    )

    # Pinned history blob missing → never substitute Qdrant/legacy text from
    # another version (that is how unsupported old .png showed random Source).
    if not file_path and (storage_file or version_id):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Version blob not found: "
                f"{Path(storage_file).name if storage_file else (version_id or '')}. "
                "No Source text is available for this version."
            ),
        )

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
    lookup_src = source_key
    if COLLECTIONS_DIR.is_dir():
        for col_dir in COLLECTIONS_DIR.iterdir():
            if not col_dir.is_dir():
                continue
            idx = load_file_index(col_dir.name)
            for fid, entry in idx.items():
                if entry.get("source") == lookup_src:
                    fmt = entry.get("file_type", "text")
                    break

    suffix = file_path.suffix.lower()
    text_suffixes = {".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".html", ".htm", ".log"}
    # PDF re-parse is slow; Office (docx/xlsx/pptx) parse is usually acceptable
    # for Source when cache is missing.
    heavy_pdf = {".pdf"}
    office_parse = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}

    # Shared parsed.txt for *supported current* version only.
    # Historical storage_file / version_id must never fall through to the
    # current parsed.txt or a shared ".extracted.txt" from a later overwrite.
    use_shared_parsed = True
    fid = _file_id_from_source(source_key) if collection else None
    is_historical = False
    if collection and fid:
        _, cur_unsupported = _current_version_meta(collection, fid)
        cur_vid: str | None = None
        try:
            from src.file_mgmt.store import get_db as _get_db

            _conn = _get_db(collection)
            try:
                _crow = _conn.execute(
                    "SELECT current_version_id FROM files WHERE file_id=?",
                    (fid,),
                ).fetchone()
                if _crow:
                    cur_vid = _crow["current_version_id"]
            finally:
                _conn.close()
        except Exception:
            cur_vid = None

        if version_id and cur_vid and version_id != cur_vid:
            is_historical = True
            use_shared_parsed = False
        elif version_id and not cur_vid:
            is_historical = True
            use_shared_parsed = False
        elif storage_file and not _is_current_storage_file(
            collection, fid, storage_file
        ):
            # Non-current blob name → historical even when *current* is unsupported
            # (otherwise we would serve the latest shared .extracted.txt).
            is_historical = True
            use_shared_parsed = False
        elif cur_unsupported:
            use_shared_parsed = False
    elif storage_file or version_id:
        use_shared_parsed = False
        is_historical = True

    def _respond(
        body: str,
        body_fmt: str,
        *,
        preview_hint: str | None = None,
    ) -> dict:
        """Fill blank image file_ids then build the extract response."""
        out_text = body
        if fid and out_text:
            out_text = _fill_empty_image_file_ids(out_text, fid)
        payload: dict = {"text": out_text, "format": body_fmt}
        if preview_hint:
            payload["preview_hint"] = preview_hint
        return payload

    if use_shared_parsed:
        parsed_path = file_path.parent / "parsed.txt"
        if parsed_path.is_file():
            return _respond(parsed_path.read_text(encoding="utf-8"), fmt)

    # Per-blob extract cache (written after successful parse).
    # For historical versions that share a storage basename (legacy overwrite),
    # prefer version_id-scoped cache so v3/v4/v5 do not share one text file.
    if is_historical and version_id:
        blob_cache = file_path.parent / f"{file_path.name}.{version_id[:12]}.extracted.txt"
    else:
        blob_cache = file_path.parent / f"{file_path.name}.extracted.txt"
    if blob_cache.is_file():
        try:
            return _respond(
                blob_cache.read_text(encoding="utf-8"),
                "markdown" if suffix in {".md", ".docx", ".pdf"} else "text",
            )
        except Exception:
            pass
    # Legacy unscoped cache only for non-colliding historical / current
    legacy_blob_cache = file_path.parent / f"{file_path.name}.extracted.txt"
    if (
        not is_historical
        and legacy_blob_cache.is_file()
        and legacy_blob_cache != blob_cache
    ):
        try:
            return _respond(
                legacy_blob_cache.read_text(encoding="utf-8"),
                "markdown" if suffix in {".md", ".docx", ".pdf"} else "text",
            )
        except Exception:
            pass

    if suffix in text_suffixes:
        try:
            return _respond(
                file_path.read_text(encoding="utf-8", errors="replace"),
                "markdown" if suffix == ".md" else "text",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")

    # Historical: always try version_id stitch first (unique per version even when
    # multiple rows share one on-disk basename after legacy overwrite).
    if is_historical and collection:
        stitched = _stitch_version_text_from_chunks(
            collection,
            source_key,
            storage_file or file_path.name,
            version_id=version_id,
        )
        if stitched:
            try:
                blob_cache.write_text(stitched, encoding="utf-8")
            except Exception:
                pass
            fmt_out = (
                "pdf"
                if suffix in heavy_pdf
                else ("markdown" if suffix in {".docx", ".doc", ".md"} else "text")
            )
            return _respond(stitched, fmt_out)
        # Historical PDF with no version chunks: re-parse the on-disk blob when
        # it is *not* the current version's basename (unique old blob). Shared
        # names would only re-show latest content — prefer empty + Raw instead.
        if suffix in heavy_pdf:
            if storage_file and not _is_current_storage_file(
                collection, fid or "", storage_file
            ):
                pass  # fall through to full re-parse of this unique blob
            else:
                return _respond("", "pdf", preview_hint="raw")

    from src.parsers import PARSERS

    parser = PARSERS.get(suffix)
    if parser is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format for text extract: {suffix}",
        )

    try:
        doc = parser.parse(file_path)
        text = doc.content or "(No text content extracted)"
        fmt = doc.file_type
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse file: {e}")

    if fid and text:
        text = _fill_empty_image_file_ids(text, fid)

    try:
        blob_cache.write_text(text, encoding="utf-8")
    except Exception:
        pass
    if use_shared_parsed:
        try:
            (file_path.parent / "parsed.txt").write_text(text, encoding="utf-8")
        except Exception:
            pass

    return _respond(text, fmt)


def _fill_empty_image_file_ids(text: str, file_id: str) -> str:
    """Rewrite ``file_id:`` (empty) inside :::image fences to the managed file id.

    Parsers leave ``file_id:`` blank; ingest usually fills it. Historical re-parse
    and older caches often still have blanks, which break Source image URLs.
    """
    if not text or not file_id or ":::image" not in text:
        return text
    import re

    return re.sub(
        r"(:::image[ \t]*\nimage_id:[ \t]*[a-f0-9]+[ \t]*\nfile_id:)[ \t]*\n",
        rf"\1 {file_id}\n",
        text,
    )


def _stitch_version_text_from_chunks(
    collection_id: str,
    source: str,
    storage_file: str,
    version_id: str | None = None,
) -> str | None:
    """Rebuild Source text from Qdrant chunks for a historical version blob.

    Prefers explicit *version_id* (unique). Falls back to lookup via
    ``file_versions.storage_file_id`` (ambiguous when several versions share
    one basename after a legacy overwrite).
    """
    try:
        from src.file_mgmt.store import get_db
        from src.services import services
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        fid = _file_id_from_source(source)
        if not fid or services.db is None:
            return None
        resolved_vid = (version_id or "").strip() or None
        if not resolved_vid:
            safe = Path(storage_file).name
            conn = get_db(collection_id)
            try:
                # Prefer non-current row when multiple share storage basename
                row = conn.execute(
                    """SELECT fv.version_id FROM file_versions fv
                       JOIN files f ON f.file_id = fv.file_id
                       WHERE fv.file_id=? AND fv.storage_file_id=?
                       ORDER BY CASE WHEN fv.version_id = f.current_version_id
                                     THEN 1 ELSE 0 END,
                                fv.version_no DESC
                       LIMIT 1""",
                    (fid, safe),
                ).fetchone()
            finally:
                conn.close()
            if not row:
                return None
            resolved_vid = row["version_id"]
        filt = Filter(
            must=[
                FieldCondition(key="source", match=MatchValue(value=source)),
                FieldCondition(
                    key="version_id", match=MatchValue(value=resolved_vid)
                ),
            ]
        )
        total = services.db.count_by_filter(collection_id, filt)
        if total <= 0:
            return None
        pts, _ = services.db.scroll_points(
            collection=collection_id,
            limit=min(total, 10000),
            offset=None,
            scroll_filter=filt,
            with_payload=True,
            with_vectors=False,
        )
        items = []
        for p in pts:
            pl = p.get("payload") or {}
            if pl.get("chunk_type") == "parent":
                continue  # prefer leaf / normal text
            text = (pl.get("text") or "").strip()
            if not text:
                continue
            items.append((int(pl.get("chunk_index") or 0), text))
        if not items:
            return None
        items.sort(key=lambda x: x[0])
        return "\n\n".join(t for _, t in items)
    except Exception:
        logger.debug(
            "stitch version text failed for %s %s",
            source,
            storage_file,
            exc_info=True,
        )
        return None

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
                # Index key is the managed file_id (needed to open FileMgmt detail)
                "file_id": fid,
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


def _resolve_current_version_id(collection: str, source: str) -> str | None:
    """Best-effort current version_id for a managed ``__file__:{id}`` source."""
    if not source.startswith("__file__:"):
        return None
    fid = source[len("__file__:") :].strip()
    if not fid:
        return None
    try:
        from src.file_mgmt.store import get_db

        conn = get_db(collection)
        try:
            row = conn.execute(
                "SELECT current_version_id FROM files WHERE file_id=?", (fid,)
            ).fetchone()
            return (row["current_version_id"] if row else None) or None
        finally:
            conn.close()
    except Exception:
        return None


@router.get("/documents/{collection}/files/{source:path}/chunks")
def get_file_chunks(
    collection: str,
    source: str,
    limit: int = 100,
    offset: int = 0,
    include_archived: bool = False,
    version_id: str | None = None,
):
    """List chunks for a document source.

    By default returns the **current** version only:
    - Prefer points with ``version_id == files.current_version_id``
    - Else non-archived points (legacy rows without version_id)

    When *version_id* is set (old version open), return only that version's
    points and include archived ones (history rows are archived after upload).
    """
    if not services.db.collection_exists(collection):
        return {"collection": collection, "source": source, "chunks": [], "total": 0}

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    must = [FieldCondition(key="source", match=MatchValue(value=source))]
    must_not: list = []
    resolved_version = (version_id or "").strip() or None
    # Current version pin (only when not asking for a specific historical version)
    cur_vid: str | None = None
    pinned_to_current_version = False

    if resolved_version:
        must.append(
            FieldCondition(key="version_id", match=MatchValue(value=resolved_version))
        )
        # Historical versions are archived=true after a newer upload
        include_archived = True
    else:
        # Current open: pin to current_version_id when known so leftover
        # non-archived points from older uploads do not pollute Chunks.
        cur_vid = _resolve_current_version_id(collection, source)
        if cur_vid:
            must.append(
                FieldCondition(key="version_id", match=MatchValue(value=cur_vid))
            )
            pinned_to_current_version = True
        if not include_archived:
            must_not.append(
                FieldCondition(key="archived", match=MatchValue(value=True))
            )

    filter_cond = Filter(must=must, must_not=must_not or None)

    total = services.db.count_by_filter(collection, filter_cond)

    # Fallback ONLY for legacy docs with no version_id on the file row.
    # If we pinned to current_version_id and got 0 (e.g. unsupported latest
    # with no ingest), do NOT fall back to older non-archived leftovers —
    # that is the "shows wrong version's chunks" bug.
    if (
        total == 0
        and not resolved_version
        and not include_archived
        and not pinned_to_current_version
    ):
        fallback_must = [
            FieldCondition(key="source", match=MatchValue(value=source))
        ]
        fallback_not = [
            FieldCondition(key="archived", match=MatchValue(value=True))
        ]
        filter_cond = Filter(must=fallback_must, must_not=fallback_not)
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
            "version_id": p["payload"].get("version_id") or "",
            "archived": bool(p["payload"].get("archived")),
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
