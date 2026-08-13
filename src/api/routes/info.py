"""Info API routes — collection summaries, conflicts, doc summaries,
consolidation trigger, and meeting-log lookup.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

from src.services import services
from src.tasks import task_manager
from src.rag.summary_manager import SummaryManager
from src.collections import store as collections_store

logger = logging.getLogger(__name__)

router = APIRouter()

# Resolve meetings directory (same convention as src/meeting/store.py)
MEETINGS_DIR = Path("data").resolve() / "meetings"


# ═══════════════════════════════════════════════════════════════
# Debounced consolidate — 10 s timer, net-change detection
# Truth source: files.is_definitive (for __file__: sources).
# ═══════════════════════════════════════════════════════════════

_debounce: dict[str, dict] = {}  # collection_id -> {"timer": Timer, "snapshot": {source: bool}}


def source_is_definitive(collection_id: str, source: str) -> bool:
    """Whether this source participates in Collection Summary consolidate.

    File-mgmt files (``__file__:{file_id}``): ``files.is_definitive``.
    Other sources (notes/meetings legacy): fall back to doc_summary.include_in_summary.
    """
    if (source or "").startswith("__file__:"):
        file_id = source[len("__file__:") :]
        try:
            from src.file_mgmt.store import get_db

            conn = get_db(collection_id)
            try:
                row = conn.execute(
                    "SELECT is_definitive FROM files WHERE file_id=?",
                    (file_id,),
                ).fetchone()
                return bool(row and row["is_definitive"])
            finally:
                conn.close()
        except Exception:
            logger.warning(
                "source_is_definitive failed for %s/%s",
                collection_id,
                source,
                exc_info=True,
            )
            return False

    sm = _get_summary_manager()
    ds = sm.get_doc_summary(collection_id, source)
    if not ds:
        return False
    return ds.get("include_in_summary", True) is not False


def current_version_id_for_source(collection_id: str, source: str) -> str | None:
    """Return files.current_version_id for ``__file__:{id}`` sources, else None."""
    if not (source or "").startswith("__file__:"):
        return None
    file_id = source[len("__file__:") :]
    try:
        from src.file_mgmt.store import get_db

        conn = get_db(collection_id)
        try:
            row = conn.execute(
                "SELECT current_version_id FROM files WHERE file_id=?",
                (file_id,),
            ).fetchone()
            return (row["current_version_id"] if row else None) or None
        finally:
            conn.close()
    except Exception:
        logger.debug(
            "current_version_id_for_source failed for %s/%s",
            collection_id,
            source,
            exc_info=True,
        )
        return None


def pick_current_doc_summaries(
    collection_id: str, summaries: list[dict]
) -> list[dict]:
    """One summary per source — prefer payload matching current_version_id."""
    by_source: dict[str, list[dict]] = {}
    for s in summaries:
        src = s.get("source") or ""
        if not src:
            continue
        by_source.setdefault(src, []).append(s)

    out: list[dict] = []
    for src, items in by_source.items():
        cur = current_version_id_for_source(collection_id, src)
        if cur:
            matched = [i for i in items if i.get("version_id") == cur]
            if matched:
                out.append(matched[0])
                continue
            legacy = [i for i in items if not (i.get("version_id") or "").strip()]
            if legacy:
                out.append(legacy[0])
                continue
        out.append(items[0])
    return out


def _snapshot_includes(collection_id: str) -> dict[str, bool]:
    """Snapshot of which doc-summary sources are definitive (consolidate set).

    Key = source, value = participates in Collection Summary.
    Used for debounce net-change detection.
    """
    sm = _get_summary_manager()
    summaries = sm.get_doc_summaries(collection_id, included_only=False)
    # Deduplicate by source (multiple version points may exist)
    return {
        src: source_is_definitive(collection_id, src)
        for src in {s["source"] for s in summaries if s.get("source")}
    }


def _do_consolidate(collection_id: str) -> None:
    """Timer callback: compare current state with snapshot, trigger consolidate on net change."""
    logger.info("[DEBOUNCE] Timer fired for collection='%s', checking net change...", collection_id)
    state = _debounce.pop(collection_id, None)
    if state is None:
        return

    # Skip if consolidation is already running
    if task_manager.has_active_task(collection_id, "consolidate"):
        logger.info("[DEBOUNCE] Consolidation already running for '%s', skipping", collection_id)
        return

    force = bool(state.get("force_content_change"))
    if force:
        has_change = True
    else:
        try:
            current = _snapshot_includes(collection_id)
        except Exception:
            logger.warning(
                "[DEBOUNCE] Failed to snapshot current state for '%s', bailing out",
                collection_id,
                exc_info=True,
            )
            return

        snapshot = state["snapshot"]
        all_sources = set(snapshot.keys()) | set(current.keys())
        has_change = any(snapshot.get(src) != current.get(src) for src in all_sources)

    if not has_change:
        logger.info("[DEBOUNCE] No net change for collection='%s', skipping consolidation", collection_id)
        return

    logger.info("[DEBOUNCE] Net change detected for collection='%s', triggering consolidation", collection_id)
    task_manager.create_task(
        filename=f"consolidate:{collection_id}",
        task_type="consolidate",
        collection=collection_id,
    )

def schedule_debounced_consolidate(
    collection_id: str,
    pre_change_snapshot: dict[str, bool] | None = None,
    *,
    force_content_change: bool = False,
) -> None:
    """Schedule a debounced consolidation check after definitive-set changes.

    Must be called with a snapshot taken BEFORE the change was applied.
    On the first call within a debounce window, the snapshot is stored.
    Subsequent calls only reset the timer — the original snapshot is kept.

    If *pre_change_snapshot* is not provided, a snapshot is taken now
    (which reflects the post-change state, only safe when the change is
    additive, e.g. a new summary that didn't exist before).

    *force_content_change*: definitive membership may be unchanged but current
    doc summary text changed (e.g. re-summarize / new version) — still run.
    """
    if collection_id not in _debounce:
        snap = pre_change_snapshot if pre_change_snapshot is not None else _snapshot_includes(collection_id)
        _debounce[collection_id] = {
            "timer": None,
            "snapshot": snap,
            "force_content_change": force_content_change,
        }
        logger.info("[DEBOUNCE] First change for collection='%s', snapshot has %d sources",
                    collection_id, len(snap))

    state = _debounce[collection_id]
    if force_content_change:
        state["force_content_change"] = True

    # Cancel existing timer
    if state["timer"] is not None:
        state["timer"].cancel()
        logger.info("[DEBOUNCE] Reset timer for collection='%s'", collection_id)

    # Start new 10-second timer
    timer = threading.Timer(10.0, _do_consolidate, args=[collection_id])
    state["timer"] = timer
    timer.start()
    logger.info("[DEBOUNCE] Scheduled consolidation check for collection='%s' in 10s", collection_id)


def clear_debounce(collection_id: str) -> None:
    """Clear debounce state for a collection (called after successful consolidation)."""
    state = _debounce.pop(collection_id, None)
    if state and state["timer"] is not None:
        state["timer"].cancel()
    logger.info("[DEBOUNCE] Cleared debounce state for collection='%s'", collection_id)


def _get_summary_manager() -> SummaryManager:
    return SummaryManager(db=services.db)


def _resolve_collection_id(collection: str) -> str:
    """Resolve collection: try as ID first, fall back to name (for legacy)."""
    meta = collections_store.get_collection_meta(collection)
    if meta:
        return meta["id"]
    # Try to find by name
    meta = collections_store.find_collection_by_name(collection)
    if meta:
        return meta["id"]
    return collection


# ── Collection summary ──────────────────────────────────────


@router.get("/collections/{collection}/info/summary")
def get_collection_summary(collection: str):
    """Get the consolidated collection summary."""
    collection_id = _resolve_collection_id(collection)
    logger.info("[INFO] GET summary for collection='%s' (resolved='%s')", collection, collection_id)
    sm = _get_summary_manager()
    summary = sm.get_collection_summary(collection_id)
    if summary is None:
        logger.info("[INFO] No summary found for collection='%s'", collection_id)
        raise HTTPException(status_code=404, detail=f"No summary found for collection '{collection}'")
    logger.info("[INFO] Found summary for collection='%s' (content=%d chars)", collection_id, len(summary.get("content", "")))
    return summary


@router.get("/collections/{collection}/info/project-description")
def get_project_description(collection: str):
    """Get the project description (2-sentence summary) for a collection."""
    collection_id = _resolve_collection_id(collection)
    logger.info("[INFO] GET project-description for collection='%s' (resolved='%s')", collection, collection_id)
    sm = _get_summary_manager()
    desc = sm.get_project_description(collection_id)
    if desc is None:
        logger.info("[INFO] No project description found for collection='%s'", collection_id)
        raise HTTPException(status_code=404, detail=f"No project description found for collection '{collection}'")
    logger.info("[INFO] Found project description for collection='%s' (content=%d chars)", collection_id, len(desc.get("content", "")))
    return desc


# ── Conflicts ───────────────────────────────────────────────


@router.get("/collections/{collection}/info/conflicts")
def get_collection_conflicts(collection: str):
    """Get all conflicts for this collection."""
    collection_id = _resolve_collection_id(collection)
    logger.info("[INFO] GET conflicts for collection='%s' (resolved='%s')", collection, collection_id)
    sm = _get_summary_manager()
    conflicts = sm.get_conflicts(collection_id)

    # Add human-readable labels via files.json WITHOUT mutating original source
    # (the original source is needed by the frontend to call preview/summary APIs).
    try:
        from src.collections.file_index import load_for_read
        import re as _re_uuid
        idx = load_for_read(collection_id)
        # Build lookup tables. Conflict sources come from the LLM, which sometimes
        # uses the raw UUID or formats like "note (uuid)" / "file (uuid)" — match
        # those patterns too so labels resolve even for older conflict data.
        label_map: dict[str, str] = {}
        uuid_to_label: dict[str, str] = {}
        for fid, entry in idx.items():
            src = entry.get("source", "")
            label = entry.get("source_label", fid[:8])
            if src:
                label_map[src] = label
            label_map[fid] = label  # UUID → label fallback
            uuid_to_label[fid] = label
        for c in conflicts:
            for key in ("source1", "source2"):
                src = c.get(key, "")
                if not src:
                    continue
                label = label_map.get(src)
                if not label:
                    # Try to extract a UUID from common LLM-generated formats
                    m = _re_uuid.search(r'[0-9a-f]{32}', src)
                    if m:
                        label = uuid_to_label.get(m.group())
                if label:
                    c[f"{key}_label"] = label
                else:
                    logger.info("[INFO] Conflict source '%s' not in label_map (keys: %s)", src, list(label_map.keys())[:5])
    except Exception:
        pass

    logger.info("[INFO] Found %d conflicts for collection='%s'", len(conflicts), collection_id)
    return {"collection": collection_id, "conflicts": conflicts}


# ── Doc summary ─────────────────────────────────────────────


@router.get("/collections/{collection}/info/doc-summaries/{source:path}")
def get_doc_summary(
    collection: str,
    source: str,
    version_id: str | None = None,
):
    """Get structured summary for a specific document (optional *version_id*)."""
    collection_id = _resolve_collection_id(collection)
    # Default managed files to current version when version_id omitted
    vid = (version_id or "").strip() or None
    if vid is None and (source or "").startswith("__file__:"):
        vid = current_version_id_for_source(collection_id, source)
    logger.info(
        "[INFO] GET doc-summary collection='%s' source='%s' version_id=%r",
        collection_id,
        source,
        vid,
    )
    sm = _get_summary_manager()
    doc_summary = sm.get_doc_summary(collection_id, source, version_id=vid)
    if doc_summary is None:
        logger.info("[INFO] No doc-summary found for source='%s' in collection='%s'", source, collection_id)
        raise HTTPException(status_code=404, detail=f"No summary found for document '{source}' in collection '{collection}'")
    logger.info("[INFO] Found doc-summary for source='%s' (data=%d, facts=%d, insights=%d)",
                source, len(doc_summary.get("data", [])), len(doc_summary.get("facts", [])), len(doc_summary.get("insights", [])))
    return doc_summary


@router.put("/collections/{collection}/info/doc-summaries/{source:path}/include")
async def set_doc_summary_include(collection: str, source: str, body: dict):
    """Legacy endpoint: maps to files.is_definitive for ``__file__:`` sources.

    Prefer PATCH /api/file-mgmt/.../files/{id} with is_definitive.
    Kept for non-file sources and any remaining callers.
    """
    collection_id = _resolve_collection_id(collection)
    include = body.get("include", True)
    logger.info(
        "[INFO] SET include/definitive=%s for source='%s' in collection='%s'",
        include,
        source,
        collection_id,
    )

    # File-mgmt: route through is_definitive (truth source)
    if (source or "").startswith("__file__:"):
        file_id = source[len("__file__:") :]
        from src.file_mgmt import service as fm_service

        conn = None
        try:
            from src.file_mgmt.store import get_db

            conn = get_db(collection_id)
            row = conn.execute(
                "SELECT version, is_definitive FROM files WHERE file_id=?",
                (file_id,),
            ).fetchone()
        finally:
            if conn is not None:
                conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"File not found for source '{source}'")
        if bool(row["is_definitive"]) == bool(include):
            return {
                "source": source,
                "include_in_summary": include,
                "debounce_skipped": True,
            }
        fm_service.update_file(
            collection_id,
            file_id,
            {"is_definitive": bool(include), "version": row["version"]},
        )
        return {"source": source, "include_in_summary": include}

    # Non-file: keep include_in_summary + debounce
    sm = _get_summary_manager()
    pre_snapshot = _snapshot_includes(collection_id)
    previous = pre_snapshot.get(source)

    found = sm.set_doc_summary_include(collection_id, source, include)
    if not found:
        raise HTTPException(
            status_code=404, detail=f"No summary found for document '{source}'"
        )

    if (previous is True) == (include is True):
        return {
            "source": source,
            "include_in_summary": include,
            "debounce_skipped": True,
        }

    schedule_debounced_consolidate(collection_id, pre_snapshot)
    return {"source": source, "include_in_summary": include}


@router.post("/collections/{collection}/info/doc-summaries/{source:path}/generate")
async def generate_doc_summary(
    collection: str,
    source: str,
    version_id: str | None = None,
):
    """Generate or re-generate doc summary for a document (async via task queue).

    Always targets the **current** version of a managed file. Historical
    versions cannot be re-summarized (would overwrite current summary / wrong
    content). Optional *version_id* must equal ``files.current_version_id`` when
    provided; otherwise 400.
    """
    collection_id = _resolve_collection_id(collection)
    logger.info(
        "[INFO] Generate doc-summary for collection='%s' source='%s' version_id=%r",
        collection_id,
        source,
        version_id,
    )
    from src.tasks import task_manager as _tm

    # Managed files: only allow summarize for current version
    if (source or "").startswith("__file__:"):
        file_id = source[len("__file__:") :]
        try:
            from src.file_mgmt.store import get_db

            conn = get_db(collection_id)
            try:
                row = conn.execute(
                    "SELECT current_version_id FROM files WHERE file_id=?",
                    (file_id,),
                ).fetchone()
            finally:
                conn.close()
            if row:
                current_vid = row["current_version_id"] or ""
                if version_id and current_vid and version_id != current_vid:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Cannot re-summarize a historical version. "
                            "Open the current version to generate or refresh the summary."
                        ),
                    )
        except HTTPException:
            raise
        except Exception:
            logger.debug(
                "current_version check skipped for %s/%s",
                collection_id,
                source,
                exc_info=True,
            )

    # Validate source file exists via file index
    from src.collections.file_index import load_for_read
    from src.collections.file_index import COLLECTIONS_DIR as _COL_DIR

    file_path = None
    idx = load_for_read(collection_id)
    for fid, entry in idx.items():
        if entry.get("source") == source:
            # Prefer current version dir (v2 layout); fall back to flat legacy
            try:
                from src.file_mgmt.storage_paths import (
                    ensure_layout_migrated,
                    resolve_version_blob,
                )
                from src.file_mgmt.store import get_db

                ensure_layout_migrated(collection_id)
                conn = get_db(collection_id)
                try:
                    row = conn.execute(
                        """SELECT fv.version_id, fv.storage_file_id
                           FROM files f
                           JOIN file_versions fv ON fv.version_id = f.current_version_id
                           WHERE f.file_id=?""",
                        (fid,),
                    ).fetchone()
                finally:
                    conn.close()
                if row:
                    blob = resolve_version_blob(
                        collection_id,
                        fid,
                        row["version_id"],
                        row["storage_file_id"],
                    )
                    if blob is not None:
                        parsed = blob.parent / "parsed.txt"
                        file_path = parsed if parsed.is_file() else blob
            except Exception:
                pass
            if file_path is None:
                fd = _COL_DIR / collection_id / "files" / fid
                if fd.is_dir():
                    if (fd / "parsed.txt").is_file():
                        file_path = fd / "parsed.txt"
                    else:
                        for f in sorted(fd.iterdir()):
                            if f.is_file() and f.name != "parsed.txt":
                                file_path = f
                                break
            break

    if not file_path:
        raise HTTPException(status_code=404, detail=f"Source file '{source}' not found in files index for collection '{collection}'")

    task = _tm.create_task(
        filename=f"doc_summary:{collection_id}:{source}",
        task_type="doc_summary",
        collection=collection_id,
        source=source,
    )
    logger.info("[INFO] Doc summary task created: task_id='%s'", task.id)
    return {"message": "Generation started", "task": task.to_dict(), "source": source}


def _get_enriching_llm(config: dict):
    """Get LLM for enrichment from config."""
    from src.providers.llm import create_llm_for_provider
    from src.config import get_config
    provider_id = config.get("enriching_llm_provider")
    if provider_id:
        for p in get_config().llm.providers:
            if p.id == provider_id:
                model = config.get("enriching_llm_model")
                return create_llm_for_provider(p, model=model)
    cfg = get_config()
    if cfg.llm.providers:
        default_p = next((p for p in cfg.llm.providers if p.is_default), cfg.llm.providers[0])
        return create_llm_for_provider(default_p)
    return services.llm


# ── Consolidation trigger ───────────────────────────────────


@router.post("/collections/{collection}/info/consolidate")
async def trigger_consolidation(collection: str):
    """Manually trigger consolidation."""
    collection_id = _resolve_collection_id(collection)
    logger.info("[INFO] POST consolidate triggered for collection='%s'", collection_id)
    try:
        task = task_manager.create_task(
            filename=f"consolidate:{collection_id}",
            task_type="consolidate",
            collection=collection_id,
        )
        logger.info("[INFO] Consolidation task created: task_id='%s' for collection='%s'", task.id, collection_id)
        return {"message": f"Consolidation queued for '{collection}'", "task": task.to_dict()}
    except Exception as e:
        logger.error("[INFO] Failed to create consolidation task for collection='%s': %s", collection_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Meeting log ─────────────────────────────────────────────


@router.get("/collections/{collection}/info/meeting-log")
def get_meeting_log(collection: str):
    """Get meetings linked to this collection."""
    collection_id = _resolve_collection_id(collection)
    logger.info("[INFO] GET meeting-log for collection='%s' (resolved='%s')", collection, collection_id)
    meeting_ids: set[str] = set()

    # Primary: Scan meeting meta.json files (fast, file-based)
    if MEETINGS_DIR.exists():
        for entry in MEETINGS_DIR.iterdir():
            if not entry.is_dir():
                continue
            meta_path = entry / "meta.json"
            if not meta_path.exists():
                continue
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            mid = data.get("id", entry.name)

            # Check new format: allocated_collections list
            allocated = data.get("allocated_collections", [])
            if not allocated and data.get("allocated_collection"):
                allocated = [data["allocated_collection"]]

            if collection_id in allocated:
                # Verify chunks actually exist (file_id field on chunk)
                file_ids = data.get("allocated_file_ids", [])
                if not file_ids and data.get("allocated_file_id"):
                    file_ids = [data["allocated_file_id"]]
                for fid in file_ids:
                    try:
                        from qdrant_client.models import FieldCondition, Filter as QFilter, MatchValue
                        results, _ = services.db.scroll_points(
                            collection=collection_id,
                            scroll_filter=QFilter(must=[
                                FieldCondition(key="file_id", match=MatchValue(value=fid)),
                            ]),
                            limit=1,
                            with_payload=["source", "source_label"],
                            with_vectors=False,
                        )
                        if results:
                            meeting_ids.add(mid)
                            break
                    except Exception:
                        pass

    # Secondary: Scan chunks for meeting_id field (new format, quick check)
    try:
        from qdrant_client.models import FieldCondition, Filter as QFilter, MatchValue
        if services.db.collection_exists(collection_id):
            # Only scan a small sample to check if any meeting_id fields exist
            results, _ = services.db.scroll_points(
                collection=collection_id,
                scroll_filter=QFilter(must=[
                    FieldCondition(key="chunk_type", match=MatchValue(value="normal")),
                ]),
                limit=100,
                with_payload=["meeting_id"],
                with_vectors=False,
            )
            for point in results:
                mid = point.get("payload", {}).get("meeting_id")
                if mid:
                    meeting_ids.add(mid)
    except Exception as e:
        logger.warning("[INFO] Failed to scan collection='%s' for meeting_ids: %s", collection_id, e)

    # Build meeting list with allocated file info
    meetings = []
    for mid in meeting_ids:
        meta_path = MEETINGS_DIR / mid / "meta.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                # Find which file_ids are allocated to this collection
                alloc_collections = data.get("allocated_collections", [])
                alloc_file_ids = data.get("allocated_file_ids", [])
                file_ids_for_collection = []
                file_labels: dict[str, str] = {}
                # Try to get display labels and source identifiers from files.json
                try:
                    from src.collections.file_index import load_for_read
                    idx = load_for_read(collection_id)
                    for col, fid in zip(alloc_collections, alloc_file_ids):
                        if col == collection_id:
                            entry = idx.get(fid, {})
                            # Use source (e.g. "__meeting__:mid:tab") for chunk lookup,
                            # not the opaque file_id UUID
                            source = entry.get("source", fid)
                            file_ids_for_collection.append(source)
                            label = entry.get("source_label", fid[:8])
                            file_labels[source] = label
                except Exception:
                    for col, fid in zip(alloc_collections, alloc_file_ids):
                        if col == collection_id:
                            file_ids_for_collection.append(fid)
                meetings.append({
                    "id": data.get("id", mid),
                    "title": data.get("title", ""),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "file_ids": file_ids_for_collection,
                    "file_labels": file_labels,
                })
            except (json.JSONDecodeError, OSError):
                meetings.append({"id": mid, "title": mid, "created_at": None, "updated_at": None, "file_ids": []})
        else:
            meetings.append({"id": mid, "title": mid, "created_at": None, "updated_at": None, "file_ids": []})

    meetings.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
    logger.info("[INFO] Returning %d meetings for collection='%s'", len(meetings), collection_id)
    return {"collection": collection_id, "meetings": meetings}


@router.get("/collections/{collection}/info/active-tasks")
def get_active_tasks(collection: str, task_type: str | None = None):
    """Get active (pending/processing) tasks for a collection, optionally filtered by type."""
    collection_id = _resolve_collection_id(collection)
    from src.tasks import task_manager as _tm
    tasks = _tm.get_active_tasks(collection=collection_id, task_type=task_type)
    has_consolidation = _tm.has_active_task(collection_id, "consolidate")
    has_upload = _tm.has_active_task(collection_id, "upload")
    return {
        "collection": collection_id,
        "active_tasks": tasks,
        "consolidating": has_consolidation,
        "uploading": has_upload,
    }
