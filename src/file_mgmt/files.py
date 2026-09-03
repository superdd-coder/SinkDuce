"""File index, upload, versions, archive, attach/detach."""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import IO

from fastapi import HTTPException

from src.file_mgmt.access import _actor_for, _actor_id, _now_iso, _open_db
from src.file_mgmt.events import emit_event
from src.file_mgmt.folders import get_folder_tree
from src.file_mgmt.layout import (
    _assert_file_name_free,
    _assert_folder_name_free,
    _file_display_names_in_folder,
    _raise_name_conflict,
    _row_to_folder,
    suggest_unique_name,
)
from src.file_mgmt.messages import _row_to_message
from src.file_mgmt.models import (
    ArchiveToggle,
    FileDetail,
    FileOut,
    FilePathOut,
    FileSummary,
    FileVersionOut,
    MessageOut,
    OldVersionOut,
)
from src.file_mgmt.store import COLLECTIONS_DIR, get_db

logger = logging.getLogger("file_mgmt.service")

MAX_VERSIONS = 20




# ════════════════════════════════════════════════════════════════════
# Phase 3: File Paths + Attachments + Messages
# ════════════════════════════════════════════════════════════════════
def _load_file_index(collection_id: str | None) -> dict[str, dict]:
    """Load files.json index for *collection_id* (empty dict on miss/error)."""
    if not collection_id:
        return {}
    try:
        from src.collections.file_index import load_for_read
        return load_for_read(collection_id) or {}
    except Exception:
        logger.warning(
            "Failed to load files.json index for %s", collection_id, exc_info=True
        )
        return {}


def _index_entry_display(entry: dict | None) -> tuple[str, str]:
    """Return (display_name, source) from one files.json entry."""
    if not entry:
        return "", ""
    label = (entry.get("source_label") or "").strip()
    # Normalize legacy "Meeting: Title / Section" → "Title / Section"
    if label.startswith("Meeting: "):
        label = label[len("Meeting: ") :].strip()
    if label.startswith("Note: "):
        label = label[len("Note: ") :].strip()
    src = (entry.get("source") or "").strip()
    return label, src


def _attachment_display_fields(
    collection_id: str,
    file_id: str,
    storage_file_id: str | None,
    *,
    index: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Names for node attachment rows: prefer human display_name over storage.

    ``filename`` remains the on-disk storage basename (e.g. tab_02.md).
    ``display_name`` is source_label when present (e.g. meeting title / section).
    """
    from pathlib import Path

    storage = Path(storage_file_id or "").name if storage_file_id else ""
    idx = index if index is not None else _load_file_index(collection_id)
    entry = idx.get(file_id) if idx else None
    label, _ = _index_entry_display(entry)
    display = (label or "").strip() or storage or file_id
    return {
        "filename": storage or file_id,
        "display_name": display,
    }


def _doc_kind_from_source(src: str) -> str:
    if (src or "").startswith("__meeting__:"):
        return "meeting"
    if (src or "").startswith("__note__:"):
        return "note"
    return "file"


def _row_to_file_out(
    row,
    conn=None,
    collection_id: str | None = None,
    *,
    index: dict[str, dict] | None = None,
) -> FileOut:
    from src.file_mgmt.models import FileOut

    file_id = row["file_id"]
    f = FileOut(
        file_id=file_id,
        current_version_id=row["current_version_id"],
        is_definitive=bool(row["is_definitive"]),
        archived=bool(row["archived"]),
        unsupported=bool(row["unsupported"]),
        created_by=row["created_by"],
        version=row["version"],
        filename=row["current_version_id"] or "",
        created_at="",
        updated_at="",
        is_greyed=bool(row["archived"]),
    )
    # Filename + timestamps from version history
    if conn and f.current_version_id:
        ver = conn.execute(
            "SELECT storage_file_id, created_at FROM file_versions WHERE version_id=?",
            (f.current_version_id,),
        ).fetchone()
        if ver:
            f.filename = ver["storage_file_id"]
            from pathlib import Path
            f.original_ext = Path(ver["storage_file_id"]).suffix.lstrip(".") if Path(ver["storage_file_id"]).suffix else ""
            # updated_at = current (latest) version time
            f.updated_at = ver["created_at"] or ""
        # created_at = first version time
        first = conn.execute(
            "SELECT MIN(created_at) AS min_ts FROM file_versions WHERE file_id=?",
            (file_id,),
        ).fetchone()
        if first and first["min_ts"]:
            f.created_at = first["min_ts"]
        elif f.updated_at:
            f.created_at = f.updated_at

    # Prefer preloaded index (list endpoints); else load single entry
    if index is not None:
        entry = index.get(file_id)
    else:
        entry = _load_file_index(collection_id).get(file_id) if collection_id else None
    label, src = _index_entry_display(entry)
    f.source = src or f"__file__:{file_id}"
    f.doc_kind = _doc_kind_from_source(f.source)
    # Managed files: SQLite current storage name is canonical (survives rename
    # even if files.json was briefly stale). Notes/meetings keep index titles.
    if f.doc_kind == "file" and (f.filename or "").strip():
        f.display_name = f.filename.strip()
    else:
        f.display_name = label or f.filename or file_id
    return f


def _row_to_file_path(row, folder_path: str = "", is_greyed: bool = False) -> FilePathOut:
    from src.file_mgmt.models import FilePathOut

    path_archived = bool(row["archived"]) if "archived" in row.keys() else False
    return FilePathOut(
        path_id=row["path_id"],
        file_id=row["file_id"],
        folder_id=row["folder_id"],
        is_primary=bool(row["is_primary"]),
        source_node_id=row["source_node_id"],
        created_by=row["created_by"],
        archived=path_archived,
        folder_path=folder_path,
        is_greyed=is_greyed or path_archived,
    )


def _compute_folder_path(conn, folder_id: str | None) -> str:
    """Build breadcrumb path like /Financial/Sub by walking up parent_folder_id."""
    if not folder_id:
        return ""
    parts: list[str] = []
    current = folder_id
    visited: set[str] = set()
    while current and current not in visited:
        row = conn.execute(
            "SELECT name, parent_folder_id FROM folders WHERE folder_id=?",
            (current,),
        ).fetchone()
        if not row:
            break
        parts.append(row["name"])
        visited.add(current)
        current = row["parent_folder_id"]
    parts.reverse()
    return "/" + "/".join(parts)


def _compute_is_greyed(conn, file_row, path_row) -> bool:
    """Compute is_greyed for a path entry.

    Only two archive layers (no attachment greyed):
    - files.archived=1 → True (global exclude-from-search)
    - file_paths.archived=1 → True (this folder mount)
    """
    if file_row["archived"]:
        return True
    try:
        if path_row["archived"]:
            return True
    except (KeyError, IndexError):
        pass
    return False


def _ensure_path_archive_column(conn) -> None:
    from src.file_mgmt.store import _ensure_file_paths_archived

    _ensure_file_paths_archived(conn)


def _path_has_active_mount(conn, file_id: str) -> bool:
    """True if file has at least one non-archived path."""
    row = conn.execute(
        "SELECT 1 FROM file_paths WHERE file_id=? AND COALESCE(archived, 0)=0 LIMIT 1",
        (file_id,),
    ).fetchone()
    return row is not None


def _archive_paths_on_folder(
    conn, file_id: str, folder_id: str
) -> list[str]:
    """Set archived=1 on all paths of file_id in folder_id. Returns path_ids."""
    rows = conn.execute(
        """SELECT path_id FROM file_paths
           WHERE file_id=? AND folder_id=? AND COALESCE(archived, 0)=0""",
        (file_id, folder_id),
    ).fetchall()
    path_ids = [r["path_id"] for r in rows]
    if path_ids:
        conn.execute(
            """UPDATE file_paths SET archived=1
               WHERE file_id=? AND folder_id=? AND COALESCE(archived, 0)=0""",
            (file_id, folder_id),
        )
    return path_ids


def _archive_paths_by_ids(
    conn, file_id: str, path_ids: list[str]
) -> list[str]:
    """Set archived=1 on specific path rows owned by file_id. Returns touched ids."""
    touched: list[str] = []
    for pid in path_ids:
        pid = (pid or "").strip()
        if not pid:
            continue
        row = conn.execute(
            """SELECT path_id, COALESCE(archived, 0) AS archived
               FROM file_paths WHERE path_id=? AND file_id=?""",
            (pid, file_id),
        ).fetchone()
        if not row:
            raise HTTPException(
                404, f"Path '{pid}' not found for file '{file_id}'"
            )
        if int(row["archived"] or 0) == 0:
            conn.execute(
                "UPDATE file_paths SET archived=1 WHERE path_id=?",
                (pid,),
            )
            touched.append(pid)
    return touched


def _promote_file_archive_if_needed(conn, collection_id: str, file_id: str) -> bool:
    """If no active paths remain, set files.archived=1 and mark Qdrant.

    Returns True only when this call newly promotes the file (not already archived).
    """
    if _path_has_active_mount(conn, file_id):
        return False
    fr = conn.execute(
        "SELECT archived FROM files WHERE file_id=?", (file_id,)
    ).fetchone()
    if not fr or fr["archived"]:
        # Already file-archived (e.g. user manual) — do not claim as merge promote
        return False
    conn.execute(
        "UPDATE files SET archived=1, version=version+1 WHERE file_id=?",
        (file_id,),
    )
    try:
        _mark_qdrant_chunks_archived(collection_id, file_id)
    except Exception:
        pass
    return True


def _unarchive_paths_by_ids(conn, path_ids: list[str]) -> int:
    """Clear path-level archive for given path_ids (only if still archived). Returns count."""
    n = 0
    for pid in path_ids:
        cur = conn.execute(
            "UPDATE file_paths SET archived=0 WHERE path_id=? AND COALESCE(archived, 0)=1",
            (pid,),
        )
        n += cur.rowcount
    return n


def _unarchive_paths_on_folder(conn, file_id: str, folder_id: str) -> list[str]:
    """Clear path-level archive for file_id in folder_id. Returns path_ids cleared."""
    rows = conn.execute(
        """SELECT path_id FROM file_paths
           WHERE file_id=? AND folder_id=? AND COALESCE(archived, 0)=1""",
        (file_id, folder_id),
    ).fetchall()
    path_ids = [r["path_id"] for r in rows]
    if path_ids:
        conn.execute(
            """UPDATE file_paths SET archived=0
               WHERE file_id=? AND folder_id=? AND COALESCE(archived, 0)=1""",
            (file_id, folder_id),
        )
    return path_ids


def _unarchive_file_if_archived(conn, collection_id: str, file_id: str) -> bool:
    """Undo file-level archive when still archived. Returns True if changed."""
    fr = conn.execute(
        "SELECT archived FROM files WHERE file_id=?", (file_id,)
    ).fetchone()
    if not fr or not fr["archived"]:
        return False
    conn.execute(
        "UPDATE files SET archived=0, version=version+1 WHERE file_id=?",
        (file_id,),
    )
    try:
        _restore_qdrant_chunks_for_file(collection_id, file_id)
    except Exception:
        pass
    return True


# --- File queries (without upload) ---


def get_file_detail(collection_id: str, file_id: str) -> FileDetail:
    from src.file_mgmt.models import FileDetail, MessageOut

    conn = _open_db(collection_id)
    try:
        file_row = conn.execute(
            "SELECT * FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        if not file_row:
            raise HTTPException(404, f"File '{file_id}' not found")

        base = _row_to_file_out(file_row, conn, collection_id)

        # file_paths
        path_rows = conn.execute(
            "SELECT * FROM file_paths WHERE file_id=?", (file_id,)
        ).fetchall()
        paths: list[FilePathOut] = []
        for pr in path_rows:
            fp = _compute_folder_path(conn, pr["folder_id"])
            greyed = _compute_is_greyed(conn, file_row, pr)
            paths.append(_row_to_file_path(pr, folder_path=fp, is_greyed=greyed))

        # file_versions
        ver_rows = conn.execute(
            "SELECT * FROM file_versions WHERE file_id=? ORDER BY version_no",
            (file_id,),
        ).fetchall()
        versions = [_row_to_file_version(r, collection_id) for r in ver_rows]

        # node associations (group + chain labels for file-detail UI)
        node_rows = conn.execute(
            """SELECT n.node_id, n.title, n.node_type, n.group_id, n.chain_id,
                      fn.greyed,
                      g.name AS group_name,
                      c.title AS chain_title,
                      c.parent_chain_id
               FROM file_nodes fn
               JOIN nodes n ON n.node_id = fn.node_id
               LEFT JOIN node_groups g ON g.group_id = n.group_id
               LEFT JOIN chains c ON c.chain_id = n.chain_id
               WHERE fn.file_id=?""",
            (file_id,),
        ).fetchall()
        nodes = [
            {
                "node_id": nr["node_id"],
                "title": nr["title"],
                "node_type": nr["node_type"],
                "group_id": nr["group_id"],
                "chain_id": nr["chain_id"],
                "group_name": nr["group_name"],
                "chain_title": (
                    nr["chain_title"]
                    if nr["chain_title"]
                    else ("Main" if nr["chain_id"] and not nr["parent_chain_id"] else None)
                ),
                "greyed": bool(nr["greyed"]),
            }
            for nr in node_rows
        ]

        # messages: user file messages + system version messages
        msg_rows = conn.execute(
            """SELECT * FROM messages
               WHERE owner_id=?
                 AND owner_type IN ('file', 'system_version')
               ORDER BY created_at DESC""",
            (file_id,),
        ).fetchall()
        messages = [MessageOut(**_row_to_message(r, conn).model_dump()) for r in msg_rows]

        return FileDetail(
            **base.model_dump(),
            paths=paths,
            versions=versions,
            nodes=nodes,
            messages=messages,
        )
    finally:
        conn.close()


def list_files(
    collection_id: str,
    folder_id: str | None = None,
    archived: bool | None = None,
    is_definitive: bool | None = None,
) -> list[FileSummary]:
    """List files.

    When ``is_definitive`` is True/False, returns all files with that flag
    (collection-wide), ignoring folder_id root/orphan scoping.
    """
    conn = _open_db(collection_id)
    try:
        if is_definitive is not None:
            rows = conn.execute(
                """SELECT f.* FROM files f
                   WHERE f.is_definitive=?
                   ORDER BY f.file_id""",
                (1 if is_definitive else 0,),
            ).fetchall()
        elif folder_id:
            rows = conn.execute(
                """SELECT DISTINCT f.* FROM files f
                   JOIN file_paths fp ON fp.file_id = f.file_id
                   WHERE fp.folder_id=?""",
                (folder_id,),
            ).fetchall()
        else:
            # Root level: only orphan files (no file_paths entries)
            # Lazily sync any new files from files.json
            from src.file_mgmt.store import _migrate_files_json_import
            _migrate_files_json_import(collection_id)

            rows = conn.execute(
                """SELECT f.* FROM files f
                   WHERE f.file_id NOT IN (SELECT file_id FROM file_paths)
                   ORDER BY f.file_id"""
            ).fetchall()

        idx = _load_file_index(collection_id)
        results: list[FileSummary] = []
        for r in rows:
            if archived is not None and bool(r["archived"]) != archived:
                continue
            fs = _row_to_file_out(r, conn, collection_id, index=idx)
            # For list_files, compute per-file is_greyed from archived flag
            fs.is_greyed = bool(r["archived"])
            results.append(fs)
        return results
    finally:
        conn.close()


def _mounts_for_files(conn, file_ids: list[str]) -> dict[str, list[dict]]:
    """Map file_id → list of mount dicts (folder_id, name, path, path_id, …)."""
    if not file_ids:
        return {}
    _ensure_path_archive_column(conn)
    # folder name lookup
    folder_names = {
        r["folder_id"]: r["name"]
        for r in conn.execute("SELECT folder_id, name FROM folders").fetchall()
    }
    out: dict[str, list[dict]] = {fid: [] for fid in file_ids}
    # Batch path rows
    placeholders = ",".join("?" * len(file_ids))
    path_rows = conn.execute(
        f"""SELECT * FROM file_paths
            WHERE file_id IN ({placeholders})
            ORDER BY file_id, is_primary DESC""",
        tuple(file_ids),
    ).fetchall()
    for pr in path_rows:
        fid = pr["file_id"]
        folder_id = pr["folder_id"]
        out.setdefault(fid, []).append(
            {
                "path_id": pr["path_id"],
                "folder_id": folder_id,
                "folder_name": folder_names.get(folder_id) or "",
                "folder_path": _compute_folder_path(conn, folder_id),
                "is_primary": bool(pr["is_primary"]),
                "archived": bool(pr["archived"]) if "archived" in pr.keys() else False,
                "source_node_id": pr["source_node_id"],
            }
        )
    return out


def list_files_with_mounts(
    collection_id: str,
    *,
    folder_id: str | None = None,
    archived: bool | None = None,
    is_definitive: bool | None = None,
    scope: str = "all",
) -> list[dict]:
    """List files as plain dicts, each with ``mounts`` and ``folder_ids``.

    ``scope`` (only when ``folder_id`` is None and ``is_definitive`` is None):
    - ``\"all\"`` (default): every unique file in the collection
    - ``\"orphans\"``: only files with no ``file_paths`` row (HTTP root view)

    HTTP :func:`list_files` is unchanged (empty folder_id still means orphans).
    """
    if scope not in ("all", "orphans"):
        raise HTTPException(400, "scope must be 'all' or 'orphans'")

    try:
        from src.file_mgmt.store import _migrate_files_json_import

        _migrate_files_json_import(collection_id)
    except Exception:
        logger.debug("files.json migration skipped for mounts list", exc_info=True)

    conn = _open_db(collection_id)
    try:
        if is_definitive is not None:
            rows = conn.execute(
                """SELECT f.* FROM files f
                   WHERE f.is_definitive=?
                   ORDER BY f.file_id""",
                (1 if is_definitive else 0,),
            ).fetchall()
        elif folder_id:
            rows = conn.execute(
                """SELECT DISTINCT f.* FROM files f
                   JOIN file_paths fp ON fp.file_id = f.file_id
                   WHERE fp.folder_id=?
                   ORDER BY f.file_id""",
                (folder_id,),
            ).fetchall()
        elif scope == "orphans":
            rows = conn.execute(
                """SELECT f.* FROM files f
                   WHERE f.file_id NOT IN (SELECT file_id FROM file_paths)
                   ORDER BY f.file_id"""
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT f.* FROM files f ORDER BY f.file_id"
            ).fetchall()

        idx = _load_file_index(collection_id)
        filtered = []
        for r in rows:
            if archived is not None and bool(r["archived"]) != archived:
                continue
            filtered.append(r)

        file_ids = [r["file_id"] for r in filtered]
        mounts_map = _mounts_for_files(conn, file_ids)

        results: list[dict] = []
        for r in filtered:
            fs = _row_to_file_out(r, conn, collection_id, index=idx)
            fs.is_greyed = bool(r["archived"])
            d = fs.model_dump()
            mounts = mounts_map.get(r["file_id"], [])
            d["mounts"] = mounts
            d["folder_ids"] = [m["folder_id"] for m in mounts if m.get("folder_id")]
            # Optimistic-lock field is ``version``; expose ASR-facing current version_no
            cur_vid = d.get("current_version_id")
            if cur_vid:
                vn = conn.execute(
                    "SELECT version_no FROM file_versions WHERE version_id=?",
                    (cur_vid,),
                ).fetchone()
                d["current_version_no"] = int(vn["version_no"]) if vn else None
            else:
                d["current_version_no"] = None
            # How many version rows exist (including archived history)
            vc = conn.execute(
                "SELECT COUNT(*) AS c FROM file_versions WHERE file_id=?",
                (r["file_id"],),
            ).fetchone()
            d["version_count"] = int(vc["c"] or 0)
            # Rename optimistic lock for agents (keep ``version`` alias for compat)
            d["lock_version"] = d.get("version")
            results.append(d)
        return results
    finally:
        conn.close()


def _compact_file_ref(f: dict, fields: str) -> dict:
    """Shrink file dict for tree embedding."""
    if fields == "minimal":
        return {
            "file_id": f.get("file_id"),
            "filename": f.get("filename") or f.get("display_name"),
            "display_name": f.get("display_name"),
            "doc_kind": f.get("doc_kind"),
            "current_version_no": f.get("current_version_no"),
            "folder_ids": f.get("folder_ids") or [],
        }
    if fields == "summary":
        return {
            "file_id": f.get("file_id"),
            "filename": f.get("filename"),
            "display_name": f.get("display_name"),
            "doc_kind": f.get("doc_kind"),
            "is_definitive": f.get("is_definitive"),
            "archived": f.get("archived"),
            "current_version_no": f.get("current_version_no"),
            "version_count": f.get("version_count"),
            "lock_version": f.get("lock_version", f.get("version")),
            "folder_ids": f.get("folder_ids") or [],
            # mounts once at file level is enough; tree multi-embed skips full mounts
            "mount_count": len(f.get("mounts") or []),
        }
    return f


def build_library_tree(
    collection_id: str,
    *,
    max_depth: int | None = None,
    include_orphans: bool = True,
    include_archived_files: bool = True,
    fields: str = "summary",
) -> dict:
    """One-shot nested folder tree with files under each folder + orphans.

    ``fields``:
    - ``minimal``: each embedded file is ``{file_id, filename, doc_kind, …}``
    - ``summary`` (default): + definitive/version meta, no full mounts[] per embed
    - ``full``: full file dict with mounts (can be large when multi-mounted)

    Multi-mount files still appear under each folder, but minimal/summary
    avoids repeating the full mounts[] payload at every mount site.

    Each folder always has **real** ``file_count`` / ``unique_file_count`` /
    ``mount_count`` (library truth for that folder). ``files`` is the **payload**
    of this response only:

    - ``max_depth=None`` / unlimited: every folder includes ``files``.
    - ``max_depth=N``: folders with depth ``>= N`` set ``truncated=true``,
      ``files=[]``, ``files_omitted=<real unique count>``, but counts stay real.
      Deeper folder **skeleton** is still returned (grandchildren keep
      ``truncated`` / real counts) so agents can navigate without mistaking
      truncated folders for empty ones.

    Root depth is ``0``. Example: ``max_depth=1`` expands files for root
    folders only; descendants remain as truncated stubs with real counts.
    """
    if fields not in ("minimal", "summary", "full"):
        raise HTTPException(400, "fields must be 'minimal', 'summary', or 'full'")

    try:
        from src.file_mgmt.store import _migrate_files_json_import

        _migrate_files_json_import(collection_id)
    except Exception:
        logger.debug("files.json migration skipped for library tree", exc_info=True)

    tree = get_folder_tree(collection_id)
    all_mounted = list_files_with_mounts(
        collection_id,
        scope="all",
        archived=None if include_archived_files else False,
    )
    # Index files by folder_id for O(1) attach (same file may appear in multiple folders)
    by_folder: dict[str, list[dict]] = {}
    for f in all_mounted:
        for mid in f.get("folder_ids") or []:
            by_folder.setdefault(mid, []).append(f)

    def attach(nodes: list, depth: int) -> list[dict]:
        out: list[dict] = []
        for n in nodes:
            d = n.model_dump() if hasattr(n, "model_dump") else dict(n)
            # Drop nested children from model_dump — we rebuild via attach
            d.pop("children", None)
            fid = d.get("folder_id")
            files_here = list(by_folder.get(fid, []))
            if not include_archived_files:
                files_here = [x for x in files_here if not x.get("archived")]
            unique_count = len({x["file_id"] for x in files_here})
            # Counts are always library truth for this folder
            d["unique_file_count"] = unique_count
            d["mount_count"] = len(files_here)
            d["file_count"] = unique_count

            omit_files = max_depth is not None and depth >= max_depth
            if omit_files:
                # Payload only — do not expand files past max_depth
                d["files"] = []
                d["files_omitted"] = unique_count
                d["truncated"] = True
            else:
                d["files"] = [_compact_file_ref(x, fields) for x in files_here]
                d["files_omitted"] = 0
                d["truncated"] = False

            # Always recurse folder skeleton so agents see grandchildren
            # (each deeper node still carries real counts + truncated when needed)
            raw_kids = getattr(n, "children", None) or []
            d["children"] = attach(raw_kids, depth + 1) if raw_kids else []
            out.append(d)
        return out

    folders = attach(tree, 0)

    orphans: list[dict] = []
    if include_orphans:
        orphans_raw = list_files_with_mounts(
            collection_id,
            scope="orphans",
            archived=None if include_archived_files else False,
        )
        orphans = [_compact_file_ref(x, fields) for x in orphans_raw]

    # Summary over unique file_ids in collection
    unique_ids = {f["file_id"] for f in all_mounted}
    by_kind: dict[str, int] = {}
    for f in all_mounted:
        k = f.get("doc_kind") or "file"
        by_kind[k] = by_kind.get(k, 0) + 1

    def count_folders(nodes: list) -> int:
        n = 0
        for x in nodes:
            n += 1
            n += count_folders(x.get("children") or [])
        return n

    # Optional flat index: full mounts once (agents resolve multi-mount without fat tree)
    files_index = None
    if fields in ("minimal", "summary"):
        files_index = {
            f["file_id"]: {
                "file_id": f["file_id"],
                "filename": f.get("filename"),
                "display_name": f.get("display_name"),
                "doc_kind": f.get("doc_kind"),
                "current_version_no": f.get("current_version_no"),
                "version_count": f.get("version_count"),
                "lock_version": f.get("lock_version", f.get("version")),
                "mounts": f.get("mounts") or [],
                "folder_ids": f.get("folder_ids") or [],
            }
            for f in all_mounted
        }

    return {
        "folders": folders,
        "orphans": orphans,
        "files_index": files_index,
        "fields": fields,
        "summary": {
            "folder_count": count_folders(folders),
            "unique_file_count": len(unique_ids),
            "orphan_count": len(orphans),
            "by_doc_kind": by_kind,
            "files": by_kind.get("file", 0),
            "notes": by_kind.get("note", 0),
            "meetings": by_kind.get("meeting", 0),
        },
        "read_hint": (
            "Tree embeds compact file refs under each folder. "
            "Use files_index[file_id].mounts for full multi-mount detail, "
            "or list_files / get_file for more."
            if files_index is not None
            else "fields=full embeds full file objects (can be large)."
        ),
    }


def list_files_in_folder(collection_id: str, folder_id: str) -> list[FileSummary]:
    """List files in a folder with greyed status.

    Deduplicates: same file with multiple paths in this folder shows once.

    Both path-level archive (``file_paths.archived=1``, e.g. branch merge) and
    file-level archive (``files.archived=1``) **remain listed** here with
    ``is_greyed=True`` — they are not removed from the folder. The virtual
    ``/Archived`` view still lists all file-level archives globally.
    """
    # Lazy import notes/meetings from files.json (not only root orphans).
    try:
        from src.file_mgmt.store import _migrate_files_json_import

        _migrate_files_json_import(collection_id)
    except Exception:
        logger.debug(
            "files.json migration skipped for folder list %s",
            collection_id,
            exc_info=True,
        )

    conn = _open_db(collection_id)
    try:
        # Check folder exists
        fld = conn.execute(
            "SELECT folder_id FROM folders WHERE folder_id=?", (folder_id,)
        ).fetchone()
        if not fld:
            raise HTTPException(404, f"Folder '{folder_id}' not found")

        _ensure_path_archive_column(conn)
        # One row per file: has_active_path=1 if any non-archived path in this folder
        rows = conn.execute(
            """SELECT f.*,
                      MAX(CASE WHEN COALESCE(fp.archived, 0)=0 THEN 1 ELSE 0 END)
                        AS has_active_path
               FROM files f
               JOIN file_paths fp ON fp.file_id = f.file_id
               WHERE fp.folder_id=?
               GROUP BY f.file_id
               ORDER BY f.file_id""",
            (folder_id,),
        ).fetchall()
        # Load files.json once — source/doc_kind for Meeting/Note badges
        idx = _load_file_index(collection_id)
        result: list = []
        for r in rows:
            fs = _row_to_file_out(r, conn, collection_id, index=idx)
            try:
                has_active = int(r["has_active_path"] or 0) == 1
            except (KeyError, IndexError, TypeError):
                has_active = True
            # File-level or path-level archive → stay in place, show greyed
            if bool(r["archived"]) or not has_active:
                fs.is_greyed = True
            result.append(fs)
        return result
    finally:
        conn.close()


def list_archived_files(collection_id: str) -> list[FileSummary]:
    """Virtual /Archived view — all files with file-level archive (files.archived=1).

    Path-only archives stay in their folders (greyed) and do not appear here.
    """
    conn = _open_db(collection_id)
    try:
        rows = conn.execute(
            "SELECT * FROM files WHERE archived=1 ORDER BY file_id"
        ).fetchall()
        idx = _load_file_index(collection_id)
        results: list[FileSummary] = []
        for r in rows:
            fs = _row_to_file_out(r, conn, collection_id, index=idx)
            fs.is_greyed = True
            results.append(fs)
        return results
    finally:
        conn.close()


# --- File Paths ---


def add_file_path(
    collection_id: str, file_id: str, folder_id: str, is_primary: bool = False
) -> FilePathOut:
    actor = _actor_for("file_path.add", collection_id, file_id=file_id)
    conn = _open_db(collection_id)
    try:
        with conn:
            # validate file exists
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")

            # validate folder exists
            fld = conn.execute(
                "SELECT folder_id FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()
            if not fld:
                raise HTTPException(404, f"Folder '{folder_id}' not found")

            # check UNIQUE constraint: (file_id, folder_id, NULL) — only for persistent paths
            existing = conn.execute(
                """SELECT path_id FROM file_paths
                   WHERE file_id=? AND folder_id=? AND source_node_id IS NULL""",
                (file_id, folder_id),
            ).fetchone()
            if existing:
                raise HTTPException(
                    409,
                    f"File already has a persistent path in folder '{folder_id}'",
                )

            # Display-name uniqueness in destination folder
            ver = None
            if file_row["current_version_id"]:
                ver = conn.execute(
                    "SELECT storage_file_id FROM file_versions WHERE version_id=?",
                    (file_row["current_version_id"],),
                ).fetchone()
            display = Path(ver["storage_file_id"]).name if ver and ver["storage_file_id"] else file_id
            _assert_file_name_free(
                conn, folder_id, display, exclude_file_id=file_id
            )

            path_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO file_paths
                   (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                   VALUES (?, ?, ?, ?, NULL, ?)""",
                (path_id, file_id, folder_id, 1 if is_primary else 0, actor.id),
            )
            row = conn.execute(
                "SELECT * FROM file_paths WHERE path_id=?", (path_id,)
            ).fetchone()

        emit_event("file_path.added", collection_id, {"path_id": path_id, "file_id": file_id})
        fp = _compute_folder_path(conn, folder_id)
        return _row_to_file_path(row, folder_path=fp, is_greyed=bool(file_row["archived"]))
    finally:
        conn.close()


def remove_file_path(collection_id: str, file_id: str, path_id: str) -> None:
    _actor_for("file_path.remove", collection_id, file_id=file_id, path_id=path_id)
    conn = _open_db(collection_id)
    try:
        with conn:
            path = conn.execute(
                "SELECT * FROM file_paths WHERE path_id=? AND file_id=?",
                (path_id, file_id),
            ).fetchone()
            if not path:
                raise HTTPException(
                    404, f"Path '{path_id}' not found for file '{file_id}'"
                )
            conn.execute("DELETE FROM file_paths WHERE path_id=?", (path_id,))

        emit_event(
            "file_path.removed",
            collection_id,
            {"path_id": path_id, "file_id": file_id},
        )
    finally:
        conn.close()


def promote_file_path(collection_id: str, file_id: str, path_id: str) -> FilePathOut:
    _actor_for("file_path.promote", collection_id, file_id=file_id, path_id=path_id)
    conn = _open_db(collection_id)
    try:
        with conn:
            path = conn.execute(
                "SELECT * FROM file_paths WHERE path_id=? AND file_id=?",
                (path_id, file_id),
            ).fetchone()
            if not path:
                raise HTTPException(
                    404, f"Path '{path_id}' not found for file '{file_id}'"
                )

            if path["source_node_id"] is None:
                raise HTTPException(400, "Path is already a persistent path")

            conn.execute(
                "UPDATE file_paths SET source_node_id=NULL WHERE path_id=?",
                (path_id,),
            )
            row = conn.execute(
                "SELECT * FROM file_paths WHERE path_id=?", (path_id,)
            ).fetchone()

        emit_event(
            "file_path.promoted",
            collection_id,
            {"path_id": path_id, "file_id": file_id},
        )
        file_row = conn.execute(
            "SELECT * FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        fp = _compute_folder_path(conn, row["folder_id"])
        return _row_to_file_path(
            row, folder_path=fp, is_greyed=_compute_is_greyed(conn, file_row, row) if file_row else False,
        )
    finally:
        conn.close()


def _node_derived_target_folders(conn, node_id: str) -> set[str]:
    """Folders where *node_id* should place derived file paths (group + branch)."""
    node = conn.execute(
        "SELECT * FROM nodes WHERE node_id=?", (node_id,)
    ).fetchone()
    if not node:
        return set()
    targets: set[str] = set()
    if node["group_id"]:
        grp = conn.execute(
            "SELECT folder_id FROM node_groups WHERE group_id=?",
            (node["group_id"],),
        ).fetchone()
        if grp and grp["folder_id"]:
            targets.add(grp["folder_id"])
    targets |= _branch_folder_ids_for_node(conn, node_id)
    return targets


def demote_file_path(collection_id: str, file_id: str, path_id: str) -> FilePathOut:
    """Revert a persistent (pinned) path back to a derived path.

    Finds a timeline node linked to this file that would place a derived path
    in the same folder, and restores ``source_node_id``.

    Special cases (same folder can hold both pinned NULL + derived N rows):
    - If a derived path for this folder already exists, **delete** the pinned
      row only (unpin success — folder stays via the derived link).
    - If no node can re-own this folder and no derived sibling exists, raise
      400 and **keep** the path. Plain folder mounts are not timeline pins;
      use remove-path to unlink from the folder.
    """
    conn = _open_db(collection_id)
    try:
        with conn:
            path = conn.execute(
                "SELECT * FROM file_paths WHERE path_id=? AND file_id=?",
                (path_id, file_id),
            ).fetchone()
            if not path:
                raise HTTPException(
                    404, f"Path '{path_id}' not found for file '{file_id}'"
                )
            if path["source_node_id"] is not None:
                raise HTTPException(400, "Path is already a derived path")

            folder_id = path["folder_id"]
            if not folder_id:
                raise HTTPException(
                    400, "Cannot demote a path without a folder"
                )

            node_ids = [
                r["node_id"]
                for r in conn.execute(
                    "SELECT node_id FROM file_nodes WHERE file_id=?",
                    (file_id,),
                ).fetchall()
            ]
            candidate: str | None = None
            # Derived row already covering this folder (any node) — unpin = drop pin only
            existing_derived = conn.execute(
                """SELECT path_id, source_node_id FROM file_paths
                   WHERE file_id=? AND folder_id=? AND source_node_id IS NOT NULL
                     AND path_id!=?
                   LIMIT 1""",
                (file_id, folder_id, path_id),
            ).fetchone()

            for nid in node_ids:
                if folder_id in _node_derived_target_folders(conn, nid):
                    # Avoid UNIQUE collision if another row already holds this derived path
                    clash = conn.execute(
                        """SELECT path_id FROM file_paths
                           WHERE file_id=? AND folder_id=? AND source_node_id=?
                             AND path_id!=?""",
                        (file_id, folder_id, nid, path_id),
                    ).fetchone()
                    if clash:
                        continue
                    candidate = nid
                    break

            removed_pin = False
            if candidate:
                conn.execute(
                    "UPDATE file_paths SET source_node_id=? WHERE path_id=?",
                    (candidate, path_id),
                )
                row = conn.execute(
                    "SELECT * FROM file_paths WHERE path_id=?", (path_id,)
                ).fetchone()
            elif existing_derived:
                # Sibling derived covers the folder — drop the pin row only.
                conn.execute(
                    "DELETE FROM file_paths WHERE path_id=?", (path_id,)
                )
                removed_pin = True
                row = conn.execute(
                    "SELECT * FROM file_paths WHERE path_id=?",
                    (existing_derived["path_id"],),
                ).fetchone()
                candidate = existing_derived["source_node_id"]
            else:
                # No reclaimable node and no derived sibling — keep the path.
                # Deleting here made plain folder mounts vanish from file detail
                # after Unpin (UI treats source_node_id NULL as "pinned").
                raise HTTPException(
                    400,
                    "Cannot unpin: no timeline node places this file in this "
                    "folder. Use Remove from folder to unlink.",
                )

        emit_event(
            "file_path.demoted",
            collection_id,
            {
                "path_id": path_id,
                "file_id": file_id,
                "source_node_id": candidate,
                "removed_pin": removed_pin,
            },
        )
        file_row = conn.execute(
            "SELECT * FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        fp = _compute_folder_path(conn, row["folder_id"])
        return _row_to_file_path(
            row,
            folder_path=fp,
            is_greyed=_compute_is_greyed(conn, file_row, row) if file_row else False,
        )
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# Note / Meeting ingest → file-mgmt (immediate, not only lazy migration)
# ════════════════════════════════════════════════════════════════════





def _system_folder_id(conn, name: str) -> str | None:
    row = conn.execute(
        "SELECT folder_id FROM folders WHERE name=? AND is_system=1 LIMIT 1",
        (name,),
    ).fetchone()
    return row["folder_id"] if row else None


def _purge_file_sqlite_rows(conn, file_id: str) -> None:
    """Remove a managed file from meta.db only (disk/Qdrant left to caller)."""
    conn.execute("DELETE FROM file_nodes WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM file_paths WHERE file_id=?", (file_id,))
    conn.execute(
        "DELETE FROM messages WHERE owner_id=? AND owner_type IN ('file','system_version')",
        (file_id,),
    )
    conn.execute(
        "UPDATE files SET current_version_id=NULL WHERE file_id=?", (file_id,)
    )
    conn.execute("DELETE FROM file_versions WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM files WHERE file_id=?", (file_id,))


def register_ingested_source_file(
    collection_id: str,
    *,
    file_id: str,
    source: str,
    storage_name: str,
    system_folder_name: str = "Notes",
    source_label: str | None = None,
) -> None:
    """Idempotently place an ingested note/meeting snapshot into file-mgmt.

    - Ensures a row under the system folder (Notes / Meeting).
    - Persists ``source`` + ``source_label`` so folder/attachment names
      stay human after files.json stop-write.
    - Drops older SQLite rows for the same ``source`` (re-ingest).
    """
    _actor_for("file.register_ingested", collection_id, file_id=file_id)
    if not file_id or not source:
        return
    try:
        conn = _open_db(collection_id)
    except Exception:
        logger.warning(
            "register_ingested_source_file: open db failed col=%s",
            collection_id,
            exc_info=True,
        )
        return

    now = _now_iso()
    name = (storage_name or file_id).strip() or f"{file_id}.md"
    display = (source_label or "").strip() or None
    try:
        with conn:
            conn.execute("PRAGMA defer_foreign_keys=ON")
            folder_id = _system_folder_id(conn, system_folder_name)
            if not folder_id:
                logger.warning(
                    "register_ingested_source_file: no system folder %r in %s",
                    system_folder_name,
                    collection_id,
                )
                return

            stale_ids: set[str] = set()
            try:
                rows = conn.execute(
                    "SELECT file_id FROM files WHERE source=? AND file_id!=?",
                    (source, file_id),
                ).fetchall()
                stale_ids.update(str(r["file_id"]) for r in rows)
            except Exception:
                logger.debug(
                    "stale source sqlite lookup skipped for %s", source, exc_info=True
                )
            try:
                from src.collections.file_index import load as load_file_index

                idx = load_file_index(collection_id) or {}
                for fid, entry in list(idx.items()):
                    if entry.get("source") == source and fid != file_id:
                        stale_ids.add(fid)
            except Exception:
                logger.debug(
                    "stale source json lookup skipped for %s", source, exc_info=True
                )
            for fid in stale_ids:
                if conn.execute(
                    "SELECT 1 FROM files WHERE file_id=?", (fid,)
                ).fetchone():
                    _purge_file_sqlite_rows(conn, fid)
                    logger.info(
                        "Purged stale file-mgmt row %s for source %s",
                        fid,
                        source,
                    )

            existing = conn.execute(
                "SELECT file_id, current_version_id FROM files WHERE file_id=?",
                (file_id,),
            ).fetchone()

            if existing:
                cvid = existing["current_version_id"]
                if cvid:
                    conn.execute(
                        "UPDATE file_versions SET storage_file_id=? WHERE version_id=?",
                        (name, cvid),
                    )
                else:
                    version_id = uuid.uuid4().hex
                    conn.execute(
                        """INSERT INTO file_versions
                           (version_id, file_id, version_no, storage_file_id,
                            archived, commit_message, created_by, created_at)
                           VALUES (?, ?, 1, ?, 0, NULL, ?, ?)""",
                        (version_id, file_id, name, _actor_id(), now),
                    )
                    conn.execute(
                        "UPDATE files SET current_version_id=?, unsupported=0 "
                        "WHERE file_id=?",
                        (version_id, file_id),
                    )
                if display:
                    conn.execute(
                        "UPDATE files SET unsupported=0, source=?, source_label=? "
                        "WHERE file_id=?",
                        (source, display, file_id),
                    )
                else:
                    conn.execute(
                        "UPDATE files SET unsupported=0, source=? WHERE file_id=?",
                        (source, file_id),
                    )
                has_path = conn.execute(
                    "SELECT 1 FROM file_paths WHERE file_id=? AND folder_id=? LIMIT 1",
                    (file_id, folder_id),
                ).fetchone()
                if not has_path:
                    conn.execute(
                        """INSERT INTO file_paths
                           (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                           VALUES (?, ?, ?, 1, NULL, ?)""",
                        (uuid.uuid4().hex, file_id, folder_id, _actor_id()),
                    )
            else:
                version_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO files
                       (file_id, current_version_id, is_definitive, archived,
                        unsupported, created_by, version, source, source_label)
                       VALUES (?, NULL, 0, 0, 0, ?, 1, ?, ?)""",
                    (file_id, _actor_id(), source, display),
                )
                conn.execute(
                    """INSERT INTO file_versions
                       (version_id, file_id, version_no, storage_file_id,
                        archived, commit_message, created_by, created_at)
                       VALUES (?, ?, 1, ?, 0, NULL, ?, ?)""",
                    (version_id, file_id, name, _actor_id(), now),
                )
                conn.execute(
                    "UPDATE files SET current_version_id=? WHERE file_id=?",
                    (version_id, file_id),
                )
                conn.execute(
                    """INSERT INTO file_paths
                       (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                       VALUES (?, ?, ?, 1, NULL, ?)""",
                    (uuid.uuid4().hex, file_id, folder_id, _actor_id()),
                )

        logger.info(
            "Registered ingested source file col=%s file_id=%s source=%s folder=%s",
            collection_id,
            file_id[:12],
            source,
            system_folder_name,
        )
    except Exception:
        logger.warning(
            "register_ingested_source_file failed col=%s file_id=%s",
            collection_id,
            file_id,
            exc_info=True,
        )
    finally:
        conn.close()


def unregister_files_for_source(
    collection_id: str,
    source: str,
    *,
    remove_disk: bool = True,
    remove_index: bool = False,
) -> list[str]:
    """Remove managed files for a document *source* (e.g. ``__note__:{id}``).

    Cleans:
      - meta.db rows (files / versions / paths)
      - optional on-disk ``files/{file_id}/`` dirs
      - optional files.json entries (when *remove_index*)

    Returns the list of file_ids removed (best-effort).
    """
    if not source:
        return []

    fids: list[str] = []
    try:
        from src.collections.file_index import load as load_file_index

        idx = load_file_index(collection_id) or {}
        fids = [fid for fid, e in idx.items() if e.get("source") == source]
    except Exception:
        logger.debug(
            "unregister: load files.json failed col=%s", collection_id, exc_info=True
        )

    removed: list[str] = []
    try:
        conn = _open_db(collection_id)
    except Exception:
        logger.warning(
            "unregister_files_for_source: open db failed col=%s",
            collection_id,
            exc_info=True,
        )
        conn = None

    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT file_id FROM files WHERE source=?", (source,)
            ).fetchall()
            for r in rows:
                fid = str(r["file_id"])
                if fid not in fids:
                    fids.append(fid)
        except Exception:
            logger.debug(
                "unregister: files.source lookup skipped col=%s",
                collection_id,
                exc_info=True,
            )
        try:
            with conn:
                conn.execute("PRAGMA defer_foreign_keys=ON")
                for fid in fids:
                    if conn.execute(
                        "SELECT 1 FROM files WHERE file_id=?", (fid,)
                    ).fetchone():
                        _purge_file_sqlite_rows(conn, fid)
                        removed.append(fid)
                        logger.info(
                            "Purged file-mgmt row file_id=%s source=%s col=%s",
                            fid[:16],
                            source,
                            collection_id,
                        )
                    else:
                        # Not in SQLite but still drop disk/index
                        removed.append(fid)
        except Exception:
            logger.warning(
                "unregister_files_for_source SQLite failed col=%s source=%s",
                collection_id,
                source,
                exc_info=True,
            )
        finally:
            conn.close()

    if remove_disk:
        for fid in fids:
            try:
                file_dir = _files_dir(collection_id) / fid
                if file_dir.is_dir():
                    shutil.rmtree(file_dir, ignore_errors=True)
                    logger.info(
                        "Removed disk snapshot files/%s for source %s",
                        fid[:16],
                        source,
                    )
            except Exception:
                logger.warning(
                    "Failed removing disk for file_id=%s", fid, exc_info=True
                )

    if remove_index:
        try:
            from src.collections.file_index import remove_by_source as remove_file_index

            remove_file_index(collection_id, source)
        except Exception:
            logger.warning(
                "Failed removing files.json for source %s", source, exc_info=True
            )

    if not fids:
        logger.info(
            "unregister_files_for_source: no managed files for %s in %s",
            source,
            collection_id,
        )
    else:
        logger.info(
            "Unregistered source %s in %s — file_ids=%s",
            source,
            collection_id,
            [f[:12] for f in fids],
        )
    return fids


# ════════════════════════════════════════════════════════════════════
# Phase 4: File Upload Pipeline
# ════════════════════════════════════════════════════════════════════


def _files_dir(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "files"


def _get_supported_file_types(collection_id: str) -> list[str]:
    """Get supported file types for a collection from config."""
    from src.config import get_config
    cfg = get_config()
    return cfg.parsing.supported_file_types


def _is_file_supported(filename: str, collection_id: str) -> bool:
    """Check if a file type is supported for embedding."""
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext in _get_supported_file_types(collection_id)


def _write_upload_file(
    collection_id: str,
    file_id: str,
    file_bytes: bytes,
    filename: str,
    *,
    version_id: str,
    storage_name: str | None = None,
) -> tuple[Path, str]:
    """Write upload bytes under ``files/{file_id}/{version_id}/{basename}``.

    Returns ``(file_path, safe_name)`` where *safe_name* is stored as
    ``file_versions.storage_file_id`` (basename only; version lives in the path).
    """
    from src.file_mgmt.storage_paths import write_version_blob

    return write_version_blob(
        collection_id,
        file_id,
        version_id,
        file_bytes,
        storage_name or filename,
    )


def _mark_qdrant_chunks_archived(collection_id: str, file_id: str) -> int:
    """Set archived=true on all Qdrant chunks for a managed file.

    Matches both ``file_id`` payload and ``source=__file__:{file_id}`` so
    legacy points that only have ``source`` still leave the current index.
    Returns updated count.
    """
    _log = logging.getLogger("file_mgmt.service")
    try:
        from src.services import services
        if services.db is None:
            _log.warning("Qdrant not available, skipping archive mark for %s", file_id)
            return 0
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        source_key = f"__file__:{file_id}"
        all_points: list[tuple[str, object, dict]] = []
        seen_ids: set[str] = set()

        def _scroll_and_collect(filt: Filter) -> None:
            offset = None
            while True:
                pts, offset = services.db.client.scroll(
                    collection_name=collection_id,
                    scroll_filter=filt,
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                for p in pts:
                    pid = str(p.id)
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    payload = dict(p.payload or {})
                    payload["archived"] = True
                    payload["is_current"] = False
                    all_points.append((pid, p.vector, payload))
                if offset is None:
                    break

        # Two passes: file_id payload + source=__file__:{id} (legacy rows)
        _scroll_and_collect(
            Filter(
                must=[
                    FieldCondition(key="file_id", match=MatchValue(value=file_id))
                ]
            )
        )
        _scroll_and_collect(
            Filter(
                must=[
                    FieldCondition(
                        key="source", match=MatchValue(value=source_key)
                    )
                ]
            )
        )

        if all_points:
            from qdrant_client.models import PointStruct

            points = [
                PointStruct(id=id_, vector=vec, payload=pl)
                for id_, vec, pl in all_points
            ]
            services.db.client.upsert(collection_name=collection_id, points=points)

        return len(all_points)
    except Exception:
        _log.warning("Failed to mark Qdrant chunks archived for %s", file_id, exc_info=True)
        return 0


def _restore_qdrant_chunks_for_file(collection_id: str, file_id: str) -> int:
    """Restore Qdrant chunks for the CURRENT version of a file (archived→false, is_current→true).

    Only touches chunks belonging to the current version_id. Old versions keep archived=true.
    Returns updated count.
    """
    _log = logging.getLogger("file_mgmt.service")
    try:
        from src.services import services
        if services.db is None:
            _log.warning("Qdrant not available, skipping restore for %s", file_id)
            return 0
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        # Get current version_id
        conn = get_db(collection_id)
        try:
            file_row = conn.execute(
                "SELECT current_version_id FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row or not file_row["current_version_id"]:
                return 0
            current_version_id = file_row["current_version_id"]
        finally:
            conn.close()

        # Scroll all chunks for this file, restore only current version
        filter_cond = Filter(
            must=[FieldCondition(key="file_id", match=MatchValue(value=file_id))]
        )
        all_pts = []
        offset = None
        restored = 0
        while True:
            pts, offset = services.db.client.scroll(
                collection_name=collection_id,
                scroll_filter=filter_cond,
                limit=1000,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for p in pts:
                payload = dict(p.payload or {})
                if payload.get("version_id") == current_version_id:
                    payload["archived"] = False
                    payload["is_current"] = True
                    restored += 1
                # old versions: keep archived=true, is_current=false
                all_pts.append((str(p.id), p.vector, payload))
            if offset is None:
                break

        if all_pts:
            from qdrant_client.models import PointStruct
            points = [
                PointStruct(id=id_, vector=vec, payload=pl)
                for id_, vec, pl in all_pts
            ]
            services.db.client.upsert(collection_name=collection_id, points=points)

        return restored
    except Exception:
        _log.warning("Failed to restore Qdrant chunks for %s", file_id, exc_info=True)
        return 0


def _delete_qdrant_chunks_by_file_id(collection_id: str, file_id: str) -> int:
    """Delete all Qdrant chunks for a given file_id. Returns deleted count."""
    import logging as _logging
    _log = _logging.getLogger("file_mgmt.service")

    try:
        from src.services import services
        if services.db is None:
            _log.warning("Qdrant not available (services.db is None), skipping delete for %s", file_id)
            return 0
        return services.db.delete_by_filter(collection_id, "file_id", file_id)
    except Exception:
        _log.warning("Failed to delete Qdrant chunks for %s", file_id, exc_info=True)
        return 0


# ── upload_file_to_folder ────────────────────────────────────────


def upload_file_to_folder(
    collection_id: str,
    folder_id: str | None,
    file_bytes: bytes,
    filename: str,
    source_node_id: str | None = None,
    *,
    on_name_conflict: str = "error",
) -> FileSummary:
    """Upload a file to a folder (or root when ``folder_id`` is empty/None).

    Empty ``folder_id`` creates a root orphan (no ``file_paths`` row).

    ``on_name_conflict``: ``"error"`` (default, 409 + suggested_name) or
    ``"auto_rename"`` (use ``report (1).pdf`` style without failing).
    """
    _actor_for("file.upload", collection_id, folder_id=folder_id)
    if on_name_conflict not in ("error", "auto_rename"):
        raise HTTPException(400, "on_name_conflict must be 'error' or 'auto_rename'")

    # Normalize empty / whitespace folder id → root (orphan)
    if isinstance(folder_id, str):
        folder_id = folder_id.strip() or None

    file_id_for_cleanup: str | None = None
    conn = _open_db(collection_id)
    try:
        # PRAGMA must be set before BEGIN
        conn.execute("PRAGMA defer_foreign_keys=ON")
        with conn:
            # 1. Validate folder when not root
            if folder_id is not None:
                fld = conn.execute(
                    "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
                ).fetchone()
                if not fld:
                    raise HTTPException(404, f"Folder '{folder_id}' not found")

            # Display-name uniqueness in this folder / root
            base_name = Path((filename or "unnamed").strip() or "unnamed").name
            existing_names = _file_display_names_in_folder(conn, folder_id)
            if base_name.casefold() in {n.casefold() for n in existing_names}:
                suggested = suggest_unique_name(base_name, existing_names)
                if on_name_conflict == "auto_rename":
                    # Keep relative dir prefix if bulk-upload path style
                    parent_rel = str(Path(filename).parent)
                    if parent_rel and parent_rel != ".":
                        filename = str(Path(parent_rel) / suggested)
                    else:
                        filename = suggested
                else:
                    _raise_name_conflict("file", base_name, suggested)

            # 2. Generate IDs and store file
            file_id = uuid.uuid4().hex
            file_id_for_cleanup = file_id
            version_id = uuid.uuid4().hex

            save_path, safe_name = _write_upload_file(
                collection_id,
                file_id,
                file_bytes,
                filename,
                version_id=version_id,
            )
            now = _now_iso()

            # 3. Check supported
            supported = _is_file_supported(safe_name, collection_id)
            unsupported = 0 if supported else 1

            # 4. Create files record first (current_version_id=NULL to avoid circular FK)
            conn.execute(
                """INSERT INTO files
                   (file_id, current_version_id, is_definitive, archived,
                    unsupported, created_by, version)
                   VALUES (?, NULL, 0, 0, ?, ?, 1)""",
                (file_id, unsupported, _actor_id()),
            )

            # 5. Create file_versions (now files row exists)
            conn.execute(
                """INSERT INTO file_versions
                   (version_id, file_id, version_no, storage_file_id,
                    archived, commit_message, created_by, created_at)
                   VALUES (?, ?, 1, ?, 0, NULL, ?, ?)""",
                (version_id, file_id, safe_name, _actor_id(), now),
            )

            # 6. Update files.current_version_id (now version row exists)
            conn.execute(
                "UPDATE files SET current_version_id=? WHERE file_id=?",
                (version_id, file_id),
            )

            # 6. Write file_paths only when mounted in a folder
            if folder_id is not None:
                path_id = uuid.uuid4().hex
                is_primary = 1 if source_node_id is None else 0
                conn.execute(
                    """INSERT INTO file_paths
                       (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (path_id, file_id, folder_id, is_primary, source_node_id, _actor_id()),
                )

            # 7. Queue async ingest task via the existing upload pipeline
            chunk_count = 0
            task_id: str | None = None
            if supported:
                try:
                    from src.tasks.task_manager import task_manager
                    file_source = f"__file__:{file_id}"
                    task = task_manager.create_task(
                        filename=safe_name,
                        task_type="upload",
                        file_path=str(save_path),
                        collection=collection_id,
                        filename_param=file_source,
                        source_label=safe_name,
                        file_id=file_id,
                        version_id=version_id,
                    )
                    task_id = task.id
                    chunk_count = -1  # pending, actual count unknown yet
                except Exception as e:
                    # No second system_version row — "Initial upload" is created below.
                    logger.warning(
                        "Failed to queue ingest task for file %s (%s): %s",
                        file_id, safe_name, e,
                    )

            # 8. Exactly one system_version message for v1 (never a separate file message)
            message_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO messages
                   (message_id, owner_type, owner_id, source_node_id, body,
                    author_type, author_id, created_at, edited_at, edited_by, version)
                   VALUES (?, 'system_version', ?, NULL, 'Initial upload',
                    'system', ?, ?, NULL, NULL, 1)""",
                (message_id, file_id, _actor_id(), now),
            )

            row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()

        emit_event(
            "file.uploaded",
            collection_id,
            {
                "file_id": file_id,
                "folder_id": folder_id,
                "chunk_count": chunk_count,
            },
        )
        result = _row_to_file_out(row, conn, collection_id)
        result.unsupported = bool(unsupported)
        result.task_id = task_id
        return result
    except Exception:
        if file_id_for_cleanup:
            file_dir = _files_dir(collection_id) / file_id_for_cleanup
            if file_dir.exists():
                shutil.rmtree(file_dir, ignore_errors=True)
        raise
    finally:
        conn.close()


# ── upload_folder ────────────────────────────────────────────────


def upload_folder(
    collection_id: str,
    parent_folder_id: str | None,
    files_data: Iterable[tuple[bytes, str]],
) -> list[FileSummary]:
    """Upload an entire folder preserving relative paths.

    *files_data* may be any iterable — the folder-upload route passes a
    generator that reads one file at a time so a large folder never sits
    in RAM all at once.

    Args:
        parent_folder_id: destination folder, or empty/None for collection root
        files_data: iterable of (bytes_content, relative_filename) tuples
    """
    _actor_for("file.upload_folder", collection_id, folder_id=parent_folder_id)
    if isinstance(parent_folder_id, str):
        parent_folder_id = parent_folder_id.strip() or None

    now = _now_iso()

    # Validate parent folder when not root
    if parent_folder_id is not None:
        conn = _open_db(collection_id)
        try:
            parent = conn.execute(
                "SELECT * FROM folders WHERE folder_id=?", (parent_folder_id,)
            ).fetchone()
            if not parent:
                raise HTTPException(
                    404, f"Folder '{parent_folder_id}' not found"
                )
        finally:
            conn.close()

    # 1. Group files by relative path, create folders
    folder_cache: dict[str, str] = {}  # relative_dir -> folder_id

    def _ensure_folder(rel_dir: str) -> str | None:
        """Ensure folder chain exists; returns folder_id or None for root."""
        if not rel_dir or rel_dir == ".":
            return parent_folder_id
        if rel_dir in folder_cache:
            return folder_cache[rel_dir]

        parts = Path(rel_dir).parts
        current_parent: str | None = parent_folder_id
        accumulated = ""
        for part in parts:
            accumulated = str(Path(accumulated) / part) if accumulated else part
            if accumulated in folder_cache:
                current_parent = folder_cache[accumulated]
                continue

            conn2 = _open_db(collection_id)
            try:
                with conn2:
                    # Reuse existing sibling with same name (case-insensitive)
                    if current_parent is None:
                        found = conn2.execute(
                            """SELECT folder_id FROM folders
                               WHERE parent_folder_id IS NULL
                                 AND lower(name) = lower(?)
                               LIMIT 1""",
                            (part,),
                        ).fetchone()
                    else:
                        found = conn2.execute(
                            """SELECT folder_id FROM folders
                               WHERE parent_folder_id=?
                                 AND lower(name) = lower(?)
                               LIMIT 1""",
                            (current_parent, part),
                        ).fetchone()
                    if found:
                        fid = found["folder_id"]
                    else:
                        fid = uuid.uuid4().hex
                        conn2.execute(
                            """INSERT INTO folders
                               (folder_id, parent_folder_id, name, kind, is_system,
                                created_by, created_at, updated_at, version)
                               VALUES (?, ?, ?, 'plain', 0, ?, ?, ?, 1)""",
                            (fid, current_parent, part, _actor_id(), now, now),
                        )
            finally:
                conn2.close()
            folder_cache[accumulated] = fid
            current_parent = fid
        return current_parent

    results: list[FileSummary] = []
    for file_bytes, relative_path in files_data:
        # Extract relative dir
        rel_dir = str(Path(relative_path).parent) if relative_path else "."
        target_folder_id = _ensure_folder(rel_dir)

        result = upload_file_to_folder(
            collection_id,
            target_folder_id,
            file_bytes,
            relative_path,
            source_node_id=None,
            on_name_conflict="auto_rename",
        )
        results.append(result)

    return results


# ── upload_file_version ──────────────────────────────────────────


def upload_file_version(
    collection_id: str,
    file_id: str,
    file_bytes: bytes,
    filename: str,
    commit_message: str = "",
    *,
    document_source: str | None = None,
    source_label: str | None = None,
    file_type: str = "file",
) -> FileSummary:
    """Upload a new version of an existing file (non-blocking ingest).

    Same full upload pipeline as folder upload (MinerU / collection chunk
    config / contextual / embed) via async ``task_type="upload"``.

    *document_source* / *source_label*: when set (e.g. note reingest),
    Qdrant/files.json keep ``__note__:{id}`` identity instead of ``__file__:{file_id}``.
    """
    _actor_for("file.version_upload", collection_id, file_id=file_id)
    conn = _open_db(collection_id)
    try:
        with conn:
            # 1. Check file exists
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")

            old_version = conn.execute(
                "SELECT * FROM file_versions WHERE version_id=?",
                (file_row["current_version_id"],),
            ).fetchone()

            max_row = conn.execute(
                "SELECT MAX(version_no) AS m FROM file_versions WHERE file_id=?",
                (file_id,),
            ).fetchone()
            new_version_no = (max_row["m"] or 0) + 1
            new_version_id = uuid.uuid4().hex
            now = _now_iso()

            # 2. Store new version under files/{file_id}/{version_id}/{basename}
            # Isolation is by version_id directory — same display name is fine.
            base = Path(filename).name or "upload.bin"
            save_path, safe_name = _write_upload_file(
                collection_id,
                file_id,
                file_bytes,
                filename,
                version_id=new_version_id,
                storage_name=base,
            )
            commit_body = (commit_message or "").strip() or "version update"
            # Re-evaluate support from new filename (ext may change)
            supported = _is_file_supported(safe_name, collection_id)
            unsupported = 0 if supported else 1
            # parsed.txt lives inside the *new* version dir (empty until ingest).
            # Old versions keep their own parsed.txt under their version_id dirs.
            conn.execute(
                """INSERT INTO file_versions
                   (version_id, file_id, version_no, storage_file_id,
                    archived, commit_message, created_by, created_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
                (new_version_id, file_id, new_version_no, safe_name, commit_body, _actor_id(), now),
            )

            # 3. Archive old version in DB
            if old_version:
                conn.execute(
                    "UPDATE file_versions SET archived=1 WHERE version_id=?",
                    (old_version["version_id"],),
                )

            # Archive old Qdrant chunks so search uses only the new version after ingest
            _mark_qdrant_chunks_archived(collection_id, file_id)

            # 4. Update current_version_id + unsupported flag for new file type
            conn.execute(
                """UPDATE files SET current_version_id=?, unsupported=?
                   WHERE file_id=?""",
                (new_version_id, unsupported, file_id),
            )
            # Keep meeting/note identity after files.json stop-write
            ver_source = (document_source or "").strip()
            ver_label = (source_label or "").strip()
            if ver_source or ver_label:
                if ver_source and ver_label:
                    conn.execute(
                        "UPDATE files SET source=?, source_label=? WHERE file_id=?",
                        (ver_source, ver_label, file_id),
                    )
                elif ver_source:
                    conn.execute(
                        "UPDATE files SET source=? WHERE file_id=?",
                        (ver_source, file_id),
                    )
                else:
                    conn.execute(
                        "UPDATE files SET source_label=? WHERE file_id=?",
                        (ver_label, file_id),
                    )

            # 5. Create exactly ONE system_version message for this version.
            # User's Update-dialog note (commit_message) becomes this message body —
            # never create a separate owner_type=file message for it.
            message_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO messages
                   (message_id, owner_type, owner_id, source_node_id, body,
                    author_type, author_id, created_at, edited_at, edited_by, version)
                   VALUES (?, 'system_version', ?, NULL, ?,
                    'system', ?, ?, NULL, NULL, 1)""",
                (message_id, file_id, commit_body, _actor_id(), now),
            )

            # 6. Queue async ingest — same pipeline as folder upload (MinerU, chunk config, …)
            task_id: str | None = None
            file_source = (document_source or "").strip() or f"__file__:{file_id}"
            label = (source_label or "").strip() or safe_name
            # Notes always ingest (markdown snapshot); treat as supported when source is note
            force_supported = file_source.startswith("__note__:") or file_source.startswith(
                "__meeting__:"
            )
            will_ingest = supported or force_supported
            if force_supported:
                unsupported = 0
                conn.execute(
                    "UPDATE files SET unsupported=0 WHERE file_id=?", (file_id,)
                )
            if will_ingest:
                try:
                    from src.tasks.task_manager import task_manager

                    task = task_manager.create_task(
                        filename=safe_name,
                        task_type="upload",
                        file_path=str(save_path),
                        collection=collection_id,
                        filename_param=file_source,
                        source_label=label,
                        file_id=file_id,
                        version_id=new_version_id,
                    )
                    task_id = task.id
                except Exception as e:
                    # Do NOT insert a second system_version row — that would look
                    # like an "extra" message next to the user's version note.
                    logger.warning(
                        "Failed to queue version ingest for file %s (%s): %s",
                        file_id,
                        safe_name,
                        e,
                    )

            row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()

        # 7. Version limit warning
        warning = None
        if new_version_no > MAX_VERSIONS:
            warning = (
                f"File has {new_version_no} versions (limit is {MAX_VERSIONS}). "
                "Consider cleaning up old versions."
            )

        emit_event(
            "file.updated",
            collection_id,
            {
                "file_id": file_id,
                "version_no": new_version_no,
                "task_id": task_id,
            },
        )

        result = _row_to_file_out(row, conn, collection_id)
        result.unsupported = bool(unsupported)
        result.task_id = task_id
        if warning:
            result.filename = f"[WARNING] {result.filename}"
        return result
    finally:
        conn.close()


def list_old_versions(collection_id: str) -> list[OldVersionOut]:
    """List non-current file versions across the collection (not Archive-folder).

    Old versions are version-level history (``file_versions.archived=1`` or
    simply not ``current_version_id``). They never appear in the system
    Archive folder (that is file-/path-level archive only).
    """
    conn = _open_db(collection_id)
    try:
        rows = conn.execute(
            """SELECT fv.version_id, fv.file_id, fv.version_no, fv.storage_file_id,
                      fv.archived, fv.commit_message, fv.created_at,
                      f.current_version_id,
                      cur.storage_file_id AS current_storage
               FROM file_versions fv
               JOIN files f ON f.file_id = fv.file_id
               LEFT JOIN file_versions cur ON cur.version_id = f.current_version_id
               WHERE fv.version_id != f.current_version_id
                  OR COALESCE(fv.archived, 0) = 1
               ORDER BY fv.created_at DESC"""
        ).fetchall()
        # Prefer files.json labels for parent display
        idx = _load_file_index(collection_id)
        out: list[OldVersionOut] = []
        seen: set[str] = set()
        for r in rows:
            vid = r["version_id"]
            if vid in seen:
                continue
            # Skip true current version even if archived flag wrong
            if r["version_id"] == r["current_version_id"]:
                continue
            seen.add(vid)
            fid = r["file_id"]
            storage = r["storage_file_id"] or ""
            entry = idx.get(fid) or {}
            cur_name = Path(r["current_storage"] or "").name
            parent_label = (entry.get("source_label") or "").strip() or cur_name
            safe_name = Path(storage).name if storage else ""
            blob_ok = False
            if safe_name or vid:
                try:
                    from src.file_mgmt.storage_paths import version_blob_exists

                    blob_ok = version_blob_exists(
                        collection_id, fid, vid, storage
                    )
                except Exception:
                    blob_ok = False
            out.append(
                OldVersionOut(
                    version_id=vid,
                    file_id=fid,
                    version_no=int(r["version_no"] or 0),
                    storage_file_id=storage,
                    archived=bool(r["archived"]),
                    commit_message=r["commit_message"],
                    created_at=r["created_at"] or "",
                    current_filename=cur_name,
                    current_display_name=parent_label,
                    filename=safe_name,
                    original_ext=Path(safe_name).suffix.lstrip(".") if safe_name else "",
                    blob_available=blob_ok,
                )
            )
        return out
    finally:
        conn.close()


def _purge_version_permanent(
    conn,
    collection_id: str,
    file_id: str,
    ver_row,
) -> str:
    """Hard-delete one version row: blob, Qdrant, system_version msg, DB row.

    *ver_row* is a sqlite Row / mapping for ``file_versions``.
    Returns the storage_file_id (for logging). Caller owns the transaction.
    """
    version_id = ver_row["version_id"]
    storage_name = ver_row["storage_file_id"] or ""
    ver_created = ver_row["created_at"] or ""
    commit_body = (ver_row["commit_message"] or "").strip()

    from src.file_mgmt.storage_paths import delete_version_storage

    delete_version_storage(collection_id, file_id, version_id, storage_name)
    _delete_qdrant_chunks_by_version_id(collection_id, version_id)

    msg = conn.execute(
        """SELECT message_id FROM messages
           WHERE owner_type='system_version' AND owner_id=?
             AND created_at=?""",
        (file_id, ver_created),
    ).fetchone()
    if not msg and commit_body:
        msg = conn.execute(
            """SELECT message_id FROM messages
               WHERE owner_type='system_version' AND owner_id=?
                 AND body=?
               ORDER BY created_at DESC LIMIT 1""",
            (file_id, commit_body),
        ).fetchone()
    if msg:
        conn.execute(
            "DELETE FROM messages WHERE message_id=?",
            (msg["message_id"],),
        )

    conn.execute(
        "DELETE FROM file_versions WHERE version_id=?", (version_id,)
    )
    return storage_name


def delete_file_version(
    collection_id: str, file_id: str, version_id: str
) -> dict:
    """Permanently delete one non-current version.

    - Refuses if *version_id* is the file's current version
    - Deletes version blob under ``files/{file_id}/``
    - Deletes Qdrant points with matching ``version_id`` (and archived legacy
      points cannot always be attributed; best-effort)
    - Deletes paired ``system_version`` message when identifiable
    - Removes the ``file_versions`` row

    Does **not** delete the managed file_id or other versions.
    """
    _actor_for("file.version_delete", collection_id, file_id=file_id, version_id=version_id)
    conn = _open_db(collection_id)
    try:
        with conn:
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")
            if file_row["current_version_id"] == version_id:
                raise HTTPException(
                    400,
                    "Cannot delete the current version. Upload a newer version first, "
                    "or delete the whole file.",
                )
            ver = conn.execute(
                "SELECT * FROM file_versions WHERE version_id=? AND file_id=?",
                (version_id, file_id),
            ).fetchone()
            if not ver:
                raise HTTPException(
                    404, f"Version '{version_id}' not found for file '{file_id}'"
                )

            storage_name = _purge_version_permanent(
                conn, collection_id, file_id, ver
            )

        emit_event(
            "file.version_deleted",
            collection_id,
            {
                "file_id": file_id,
                "version_id": version_id,
                "storage_file_id": storage_name,
            },
        )
        return {
            "file_id": file_id,
            "version_id": version_id,
            "deleted": True,
        }
    finally:
        conn.close()


def rollback_file_version(
    collection_id: str, file_id: str, version_id: str
) -> dict:
    """Make *version_id* the current version and hard-delete all later versions.

    Later = ``version_no`` greater than the target. Those rows are permanently
    removed (blob + Qdrant + system_version log), **not** archived.

    Target version is set ``archived=0`` and ``files.current_version_id``.
    Its Qdrant chunks are restored (``archived=false``, ``is_current=true``).
    Earlier versions (lower version_no) are left as history (archived).
    """
    conn = _open_db(collection_id)
    deleted_ids: list[str] = []
    target_storage = ""
    target_no = 0
    try:
        with conn:
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")

            target = conn.execute(
                "SELECT * FROM file_versions WHERE version_id=? AND file_id=?",
                (version_id, file_id),
            ).fetchone()
            if not target:
                raise HTTPException(
                    404, f"Version '{version_id}' not found for file '{file_id}'"
                )

            if file_row["current_version_id"] == version_id:
                raise HTTPException(
                    400,
                    "This version is already current. Nothing to roll back.",
                )

            target_no = int(target["version_no"] or 0)
            target_storage = target["storage_file_id"] or ""

            later = conn.execute(
                """SELECT * FROM file_versions
                   WHERE file_id=? AND version_no > ?
                   ORDER BY version_no DESC""",
                (file_id, target_no),
            ).fetchall()

            if not later:
                # Target not current but no higher version_no — still promote.
                logger.warning(
                    "rollback: target %s has no later versions but is not current",
                    version_id,
                )

            for ver in later:
                vid = ver["version_id"]
                # Temporarily clear current_version_id if it points at this row
                # so FK / constraints don't block purge of the live version.
                if file_row["current_version_id"] == vid:
                    conn.execute(
                        "UPDATE files SET current_version_id=NULL WHERE file_id=?",
                        (file_id,),
                    )
                _purge_version_permanent(conn, collection_id, file_id, ver)
                deleted_ids.append(vid)

            # Promote target to current
            conn.execute(
                "UPDATE file_versions SET archived=0 WHERE version_id=?",
                (version_id,),
            )
            conn.execute(
                """UPDATE files
                   SET current_version_id=?, version=COALESCE(version, 0)+1
                   WHERE file_id=?""",
                (version_id, file_id),
            )

            # Keep remaining older versions marked archived (history only)
            conn.execute(
                """UPDATE file_versions SET archived=1
                   WHERE file_id=? AND version_id != ?""",
                (file_id, version_id),
            )

        # Qdrant: restore target as searchable current; leave older as archived
        restored = _restore_qdrant_version_as_current(
            collection_id, file_id, version_id
        )



        emit_event(
            "file.version_rolled_back",
            collection_id,
            {
                "file_id": file_id,
                "version_id": version_id,
                "version_no": target_no,
                "deleted_version_ids": deleted_ids,
                "restored_chunks": restored,
            },
        )
        return {
            "file_id": file_id,
            "version_id": version_id,
            "version_no": target_no,
            "storage_file_id": target_storage,
            "deleted_version_ids": deleted_ids,
            "deleted_count": len(deleted_ids),
            "restored_chunks": restored,
            "current": True,
        }
    finally:
        conn.close()


def _restore_qdrant_version_as_current(
    collection_id: str, file_id: str, version_id: str
) -> int:
    """Mark chunks for *version_id* as current; other file_id chunks non-current.

    Does not create vectors — only flips payload flags for existing points.
    """
    _log = logging.getLogger("file_mgmt.service")
    try:
        from src.services import services
        if services.db is None:
            return 0
        from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

        source_key = f"__file__:{file_id}"
        all_points: list[tuple[str, object, dict]] = []
        seen_ids: set[str] = set()

        def _collect(filt: Filter) -> None:
            offset = None
            while True:
                pts, offset = services.db.client.scroll(
                    collection_name=collection_id,
                    scroll_filter=filt,
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                for p in pts:
                    pid = str(p.id)
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    payload = dict(p.payload or {})
                    if payload.get("version_id") == version_id:
                        payload["archived"] = False
                        payload["is_current"] = True
                    else:
                        # Older leftovers (or untagged): keep out of search
                        payload["archived"] = True
                        payload["is_current"] = False
                    all_points.append((pid, p.vector, payload))
                if offset is None:
                    break

        _collect(
            Filter(
                must=[
                    FieldCondition(key="file_id", match=MatchValue(value=file_id))
                ]
            )
        )
        _collect(
            Filter(
                must=[
                    FieldCondition(
                        key="source", match=MatchValue(value=source_key)
                    )
                ]
            )
        )

        if not all_points:
            return 0

        points = [
            PointStruct(id=id_, vector=vec, payload=pl)
            for id_, vec, pl in all_points
        ]
        services.db.client.upsert(collection_name=collection_id, points=points)
        restored = sum(
            1
            for _, _, pl in all_points
            if pl.get("version_id") == version_id and pl.get("is_current") is True
        )
        return restored
    except Exception:
        _log.warning(
            "Failed to restore Qdrant chunks for rollback file=%s version=%s",
            file_id,
            version_id,
            exc_info=True,
        )
        return 0


def _delete_qdrant_chunks_by_version_id(
    collection_id: str, version_id: str
) -> int:
    """Delete Qdrant points whose payload.version_id matches *version_id*."""
    try:
        from src.services import services

        if services.db is None:
            return 0
        return services.db.delete_by_filter(
            collection_id, "version_id", version_id
        )
    except Exception:
        logger.warning(
            "Failed to delete Qdrant chunks for version_id=%s",
            version_id,
            exc_info=True,
        )
        return 0


# ── delete_file ──────────────────────────────────────────────────


def delete_file(collection_id: str, file_id: str) -> None:
    """Permanently delete a file: Qdrant chunks, disk, DB records, messages, index.

    Cascading cleanup:
    1. Delete Qdrant chunks (by file_id and by document source)
    2. Delete disk directory
    3. Delete file_nodes / file_paths / messages / versions / files row
    4. Remove files.json index entry (All Files list source of truth)
    5. Delete doc summary for ``__file__:{file_id}``
    6. If the file was definitive → debounced consolidate
       (after window, if no definitive remain → clear consolidate results)
    7. emit_event
    """
    _actor_for("file.delete", collection_id, file_id=file_id)
    source = f"__file__:{file_id}"
    was_definitive = False
    pre_snapshot: dict | None = None
    conn = _open_db(collection_id)
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        with conn:
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")

            was_definitive = bool(file_row["is_definitive"])
            # Snapshot BEFORE delete so debounce can detect net change
            # (definitive membership drop → re-consolidate / clear).
            if was_definitive:
                try:
                    from src.api.routes.info import _snapshot_includes

                    pre_snapshot = _snapshot_includes(collection_id)
                except Exception:
                    pre_snapshot = {}

            # 1. Delete Qdrant chunks (payload may key by file_id and/or source)
            _delete_qdrant_chunks_by_file_id(collection_id, file_id)
            try:
                from src.services import services as _svc
                if _svc.db is not None:
                    _svc.db.delete_by_filter(collection_id, "source", source)
            except Exception:
                logger.warning(
                    "Failed to delete Qdrant chunks by source for %s",
                    file_id,
                    exc_info=True,
                )

            # 2. Delete disk directory
            file_dir = _files_dir(collection_id) / file_id
            if file_dir.exists():
                shutil.rmtree(file_dir, ignore_errors=True)

            # 3. Delete file_nodes (FK to files)
            conn.execute("DELETE FROM file_nodes WHERE file_id=?", (file_id,))

            # 4. Delete file_paths (FK to files)
            conn.execute("DELETE FROM file_paths WHERE file_id=?", (file_id,))

            # 5. Delete messages (FK to files)
            conn.execute(
                "DELETE FROM messages WHERE owner_id=? AND owner_type IN ('file','system_version')",
                (file_id,),
            )

            # 6. Nullify files.current_version_id to break circular FK
            conn.execute(
                "UPDATE files SET current_version_id=NULL WHERE file_id=?",
                (file_id,),
            )

            # 7. Delete file_versions (FK to files)
            conn.execute("DELETE FROM file_versions WHERE file_id=?", (file_id,))

            # 8. Delete files record
            conn.execute("DELETE FROM files WHERE file_id=?", (file_id,))

        # 9. Clean up file index (All Files reads this — remove by key AND source)
        try:
            from src.collections.file_index import (
                remove as remove_file_index_by_id,
                remove_by_source as remove_file_index_by_source,
            )
            remove_file_index_by_id(collection_id, file_id)
            remove_file_index_by_source(collection_id, source)
        except Exception:
            logger.warning("Failed to clean up file index for %s", file_id, exc_info=True)

        # 10. Doc summary (same source string All Files / classic delete use)
        try:
            from src.services import services as _svc
            from src.rag.summary_manager import SummaryManager
            if _svc.db is not None:
                SummaryManager(db=_svc.db).delete_doc_summary(collection_id, source)
        except Exception:
            logger.warning(
                "Failed to clean up doc_summary for %s", source, exc_info=True
            )

        # 11. Definitive membership changed → debounced consolidate.
        # After the window, consolidate_handler re-checks: if zero definitive
        # remain, it deletes collection summary / conflicts / project desc.
        # force_content_change when none left: membership may not appear in the
        # doc-summary snapshot (no summary row), so net-change would skip.
        if was_definitive:
            try:
                from src.api.routes.info import schedule_debounced_consolidate

                remaining = _count_definitive_files(collection_id)
                schedule_debounced_consolidate(
                    collection_id,
                    pre_snapshot if pre_snapshot is not None else {},
                    force_content_change=(remaining == 0),
                )
                logger.info(
                    "Deleted definitive file %s — scheduled debounced consolidate "
                    "(%d definitive remain; force=%s → clear results if still zero)",
                    source,
                    remaining,
                    remaining == 0,
                )
            except Exception:
                logger.warning(
                    "Failed to schedule consolidate after deleting definitive %s",
                    source,
                    exc_info=True,
                )

        emit_event("file.deleted", collection_id, {"file_id": file_id})
    finally:
        conn.close()


# ── update_file ──────────────────────────────────────────────────


def update_file(
    collection_id: str, file_id: str, req: dict
) -> FileSummary:
    """Update file metadata (is_definitive and/or display filename).

    Archive operations must use ``toggle_archive`` / PATCH .../archive
    so path-level and file-level stay consistent across views.
    """
    _actor_for("file.update", collection_id, file_id=file_id)
    if "archived" in req:
        raise HTTPException(
            400,
            "Use PATCH /files/{file_id}/archive for archive operations "
            "(scope=file|path). update_file no longer accepts archived.",
        )

    conn = _open_db(collection_id)
    try:
        with conn:
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")

            version = req.get("version")
            if version is None:
                raise HTTPException(400, "version is required for optimistic locking")

            has_def = "is_definitive" in req
            has_name = "filename" in req and req["filename"] is not None
            if not has_def and not has_name:
                raise HTTPException(400, "No updatable fields provided")

            if has_name:
                if not file_row["current_version_id"]:
                    raise HTTPException(400, "File has no current version to rename")
                ver_row = conn.execute(
                    "SELECT storage_file_id FROM file_versions WHERE version_id=?",
                    (file_row["current_version_id"],),
                ).fetchone()
                old_name = (ver_row["storage_file_id"] if ver_row else "") or "unnamed"
                old_suffix = Path(old_name).suffix  # e.g. ".pdf" (last suffix)
                new_base = Path(str(req["filename"]).strip() or "unnamed").name
                # Extension is immutable — always keep the current version's suffix
                if old_suffix:
                    if new_base.lower().endswith(old_suffix.lower()):
                        stem = new_base[: -len(old_suffix)]
                    else:
                        # Drop any other last suffix the client may have sent
                        stem = Path(new_base).stem if Path(new_base).suffix else new_base
                    stem = (stem or "").strip() or "unnamed"
                    new_base = stem + old_suffix
                # Must be free in every folder this file is mounted in.
                # Root orphans have no file_paths row — still unique among orphans.
                mounts = conn.execute(
                    "SELECT DISTINCT folder_id FROM file_paths WHERE file_id=?",
                    (file_id,),
                ).fetchall()
                folder_ids = [m["folder_id"] for m in mounts]
                if not folder_ids:
                    folder_ids = [None]
                for fid in folder_ids:
                    _assert_file_name_free(
                        conn,
                        fid,
                        new_base,
                        exclude_file_id=file_id,
                    )
                old_storage_for_disk = old_name
                conn.execute(
                    "UPDATE file_versions SET storage_file_id=? WHERE version_id=?",
                    (new_base, file_row["current_version_id"]),
                )
                from src.file_mgmt.storage_paths import rename_version_blob_on_disk

                rename_version_blob_on_disk(
                    collection_id,
                    file_id,
                    file_row["current_version_id"],
                    old_storage_for_disk,
                    new_base,
                )
            old_definitive = bool(file_row["is_definitive"])
            new_definitive = (
                bool(req["is_definitive"]) if has_def else old_definitive
            )
            definitive_changed = has_def and old_definitive != new_definitive
            pre_snapshot: dict | None = None
            if definitive_changed:
                # Snapshot BEFORE flipping is_definitive (debounce net-change)
                try:
                    from src.api.routes.info import _snapshot_includes

                    pre_snapshot = _snapshot_includes(collection_id)
                except Exception:
                    pre_snapshot = {}

            if has_def:
                cursor = conn.execute(
                    "UPDATE files SET is_definitive=?, version=version+1 "
                    "WHERE file_id=? AND version=?",
                    (1 if new_definitive else 0, file_id, version),
                )
            else:
                cursor = conn.execute(
                    "UPDATE files SET version=version+1 "
                    "WHERE file_id=? AND version=?",
                    (file_id, version),
                )
            if cursor.rowcount == 0:
                raise HTTPException(
                    409, "File was modified by another user (version conflict)"
                )

            row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()

        emit_event("file.updated", collection_id, {"file_id": file_id})
        result = _row_to_file_out(row, conn, collection_id)

        # Definitive is the sole user switch for Collection Summary.
        # - mark definitive + has summary → debounce consolidate
        # - mark definitive + no summary → generate summary, then consolidate (via handler)
        # - clear definitive → keep summary, debounce consolidate
        if definitive_changed:
            _on_definitive_changed(
                collection_id, file_id, new_definitive, pre_snapshot
            )

        return result
    finally:
        conn.close()


def _count_definitive_files(collection_id: str) -> int:
    """How many files currently have ``is_definitive=1`` in this collection."""
    conn = _open_db(collection_id)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM files WHERE is_definitive=1"
        ).fetchone()
        return int(row["c"] if row else 0)
    finally:
        conn.close()


def _on_definitive_changed(
    collection_id: str,
    file_id: str,
    is_definitive: bool,
    pre_snapshot: dict | None = None,
) -> None:
    """Sync summary flag + ensure summary if needed + schedule/clear consolidate."""
    source = f"__file__:{file_id}"
    try:
        from src.api.routes.info import (
            schedule_debounced_consolidate,
            _get_summary_manager,
        )
        from src.tasks.task_manager import task_manager
    except Exception:
        logger.warning(
            "definitive side-effects unavailable for %s/%s",
            collection_id,
            file_id,
            exc_info=True,
        )
        return

    sm = _get_summary_manager()
    existing = sm.get_doc_summary(collection_id, source)
    snap = pre_snapshot if pre_snapshot is not None else {}

    # Keep per-doc summary content; only flip participation flag for legacy readers
    if existing is not None:
        try:
            sm.set_doc_summary_include(collection_id, source, is_definitive)
        except Exception:
            logger.warning(
                "Failed to sync include_in_summary for %s", source, exc_info=True
            )

    if not is_definitive:
        # Debounce re-evaluate when a definitive is cleared (including the last
        # one and files with no doc_summary). After the window, consolidate_handler
        # re-checks: if still zero definitive → delete consolidate results.
        # force when remaining==0 so empty membership is not skipped by
        # snapshot net-change (source may be absent from doc_summary keys).
        remaining = _count_definitive_files(collection_id)
        schedule_debounced_consolidate(
            collection_id,
            snap,
            force_content_change=(remaining == 0),
        )
        logger.info(
            "definitive=False for %s — %d definitive remain, "
            "scheduled debounced consolidate (force=%s)",
            source,
            remaining,
            remaining == 0,
        )
        return

    # is_definitive=True
    if existing is not None:
        schedule_debounced_consolidate(collection_id, snap)
        logger.info(
            "definitive=True for %s — scheduled consolidate (summary exists)",
            source,
        )
        return

    # No summary yet → generate; doc_summary_handler consolidates when definitive
    try:
        task_manager.create_task(
            filename=f"doc_summary:{collection_id}:{source}",
            task_type="doc_summary",
            collection=collection_id,
            source=source,
        )
        logger.info(
            "definitive=True for %s — queued doc_summary (will consolidate)",
            source,
        )
    except Exception:
        logger.warning(
            "Failed to queue doc_summary for %s", source, exc_info=True
        )
    # Debounce as well: once summary appears, net-change vs pre-snapshot
    schedule_debounced_consolidate(collection_id, snap)


# ════════════════════════════════════════════════════════════════════
# Phase 5: Archive / End Chain / Toggle Archive / Enhanced Delete Node
# ════════════════════════════════════════════════════════════════════





def toggle_archive(
    collection_id: str, file_id: str, req: ArchiveToggle
) -> FileSummary:
    """Archive or unarchive — two layers only: path + file (no attachment greyed).

    Archive (archived=True):
      scope=file → exclude from search (files.archived=1)
      scope=path → path-archive via path_ids (precise) or folder_id;
                   auto file-level if no active paths remain

    Unarchive (archived=False):
      Always clear file-level when set + clear paths via path_ids or folder_id.
    """
    scope = (req.scope or "file").strip().lower()
    if scope not in ("file", "path"):
        raise HTTPException(400, "scope must be 'file' or 'path'")

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_path_archive_column(conn)
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")

            was_file_archived = bool(file_row["archived"])
            path_ids_touched: list[str] = []
            promoted = False
            file_level_cleared = False
            file_level_set = False

            folder_id = (req.folder_id or "").strip() or None
            if folder_id == "__archived__":
                folder_id = None
            req_path_ids = [
                p.strip() for p in (req.path_ids or []) if (p or "").strip()
            ]

            def _lock_and_bump(set_archived: int | None = None) -> None:
                """Bump version (and optionally set files.archived) under lock."""
                if set_archived is None:
                    cur = conn.execute(
                        "UPDATE files SET version=version+1 "
                        "WHERE file_id=? AND version=?",
                        (file_id, req.version),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE files SET archived=?, version=version+1 "
                        "WHERE file_id=? AND version=?",
                        (set_archived, file_id, req.version),
                    )
                if cur.rowcount == 0:
                    raise HTTPException(
                        409,
                        "File was modified by another user (version conflict)",
                    )

            if req.archived:
                if scope == "path":
                    if req_path_ids:
                        path_ids_touched = _archive_paths_by_ids(
                            conn, file_id, req_path_ids
                        )
                    elif folder_id:
                        fld = conn.execute(
                            "SELECT folder_id FROM folders WHERE folder_id=?",
                            (folder_id,),
                        ).fetchone()
                        if not fld:
                            raise HTTPException(
                                404, f"Folder '{folder_id}' not found"
                            )
                        path_ids_touched = _archive_paths_on_folder(
                            conn, file_id, folder_id
                        )
                    else:
                        raise HTTPException(
                            400,
                            "path_ids or folder_id is required for path-level archive",
                        )
                    need_promote = (
                        not was_file_archived
                        and not _path_has_active_mount(conn, file_id)
                    )
                    if need_promote:
                        _lock_and_bump(set_archived=1)
                        file_level_set = True
                        promoted = True
                        try:
                            _mark_qdrant_chunks_archived(collection_id, file_id)
                        except Exception:
                            pass
                    elif path_ids_touched:
                        _lock_and_bump(set_archived=None)
                    else:
                        raise HTTPException(
                            400,
                            "Already archived for the selected path(s)"
                            if req_path_ids
                            else "Already archived for this folder",
                        )
                else:
                    # scope=file: exclude from search
                    if was_file_archived:
                        raise HTTPException(
                            400, "File is already excluded from search"
                        )
                    _lock_and_bump(set_archived=1)
                    file_level_set = True
                    try:
                        _mark_qdrant_chunks_archived(collection_id, file_id)
                    except Exception:
                        pass
            else:
                # Unarchive:
                # - with path_ids: clear those path archives (+ file-level if set)
                # - with folder_id: clear this folder's path archives
                # - without either (e.g. /Archived): clear file-level ONLY
                if req_path_ids:
                    path_ids_touched = []
                    for pid in req_path_ids:
                        row = conn.execute(
                            """SELECT path_id FROM file_paths
                               WHERE path_id=? AND file_id=?""",
                            (pid, file_id),
                        ).fetchone()
                        if not row:
                            raise HTTPException(
                                404,
                                f"Path '{pid}' not found for file '{file_id}'",
                            )
                    n = _unarchive_paths_by_ids(conn, req_path_ids)
                    if n:
                        path_ids_touched = list(req_path_ids)
                elif folder_id:
                    fld = conn.execute(
                        "SELECT folder_id FROM folders WHERE folder_id=?",
                        (folder_id,),
                    ).fetchone()
                    if not fld:
                        raise HTTPException(404, f"Folder '{folder_id}' not found")
                    path_ids_touched = _unarchive_paths_on_folder(
                        conn, file_id, folder_id
                    )

                if was_file_archived:
                    _lock_and_bump(set_archived=0)
                    file_level_cleared = True
                    try:
                        _restore_qdrant_chunks_for_file(collection_id, file_id)
                    except Exception:
                        pass
                elif path_ids_touched:
                    _lock_and_bump(set_archived=None)
                else:
                    raise HTTPException(
                        400,
                        "Nothing to unarchive: file is not excluded from search"
                        + (
                            " and has no path archives in selection"
                            if (req_path_ids or folder_id)
                            else " (open a folder to clear path-level archives)"
                        ),
                    )

            row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            out = _row_to_file_out(row, conn, collection_id)
            # Unified display: not archived after successful unarchive
            if not req.archived and not bool(row["archived"]):
                out.is_greyed = False

        if req.archived:
            emit_event(
                "file.archived",
                collection_id,
                {
                    "file_id": file_id,
                    "scope": scope,
                    "folder_id": folder_id,
                    "path_ids": path_ids_touched,
                    "promoted_to_file": promoted,
                    "file_level": file_level_set,
                },
            )
        else:
            emit_event(
                "file.unarchived",
                collection_id,
                {
                    "file_id": file_id,
                    "folder_id": folder_id,
                    "path_ids_cleared": path_ids_touched,
                    "file_level_cleared": file_level_cleared,
                },
            )

        return out
    finally:
        conn.close()


# --- Enhanced delete_node (Phase 5) ---

# Keep original delete_node signature but replace implementation


# --- Derived path generation (node attachments) ---


def attach_file_to_node(
    collection_id: str,
    node_id: str,
    file_id: str | None = None,
    upload_file=None,
) -> FileSummary:
    """Attach a file to a node, generating derived paths.

    Modes:
    - file_id != None: attach existing file
    - upload_file != None: upload new file, then attach via file_id path
    """
    _actor_for("file.attach", collection_id, node_id=node_id, file_id=file_id)
    if file_id is None and upload_file is None:
        raise HTTPException(400, "Either file_id or upload_file must be provided")
    # Preserve async ingest task_id from upload path (row_to_file_out does not store it)
    upload_task_id: str | None = None
    if file_id is None and upload_file is not None:
        # Phase 4: upload first, then attach
        file_bytes, upload_filename = upload_file  # tuple: (bytes, str)
        conn2 = _open_db(collection_id)
        try:
            node = conn2.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{node_id}' not found")

            target_folder_id: str | None = None
            if node["group_id"]:
                grp = conn2.execute(
                    "SELECT folder_id FROM node_groups WHERE group_id=?",
                    (node["group_id"],)
                ).fetchone()
                if grp and grp["folder_id"]:
                    target_folder_id = grp["folder_id"]
            # No group (or group without folder) → root orphan upload. The node
            # keeps the attachment via file_nodes; derived paths sync later if
            # a group/branch folder appears (see _sync_node_derived_paths).
        finally:
            conn2.close()

        # Upload to the group folder with source_node_id
        result = upload_file_to_folder(
            collection_id, target_folder_id, file_bytes, upload_filename, source_node_id=node_id
        )
        file_id = result.file_id
        upload_task_id = result.task_id

    # Now file_id is guaranteed to be set — use Phase 3 attach logic
    conn = _open_db(collection_id)
    try:
        with conn:
            # validate node
            node = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{node_id}' not found")

            # validate file
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")

            # Check if already attached
            existing = conn.execute(
                "SELECT 1 FROM file_nodes WHERE file_id=? AND node_id=?",
                (file_id, node_id),
            ).fetchone()
            if not existing:
                # Insert file_nodes
                conn.execute(
                    """INSERT INTO file_nodes
                       (file_id, node_id, version_id, greyed, added_by)
                       VALUES (?, ?, ?, 0, ?)""",
                    (file_id, node_id, file_row["current_version_id"], _actor_id()),
                )

            # Always sync derived paths (group + branch folder) — covers re-attach
            # and cases where chain folder was missing on first attach.
            _sync_node_derived_paths(conn, node_id, file_id=file_id)

        emit_event(
            "file.uploaded",
            collection_id,
            {"file_id": file_id, "node_id": node_id},
        )
        out = _row_to_file_out(file_row, conn, collection_id)
        # Critical: without this, frontend never starts ingest polling after
        # node upload (Add Node / node attach), so file detail allows all tabs.
        if upload_task_id:
            out.task_id = upload_task_id
        try:
            from src.file_mgmt.todo_suggestions import schedule_for_node

            schedule_for_node(collection_id, node_id)
        except Exception:
            logger.debug("todo suggestion schedule after attach_file failed", exc_info=True)
        return out
    finally:
        conn.close()


def _upsert_derived_path(
    conn, file_id: str, folder_id: str, source_node_id: str
) -> None:
    """Insert a derived path, ignoring UNIQUE constraint violations.

    If a persistent path (source_node_id=NULL) already exists for (file_id, folder_id),
    the derived path (source_node_id=N) is still allowed due to the compound UNIQUE.
    If the exact same derived path already exists, skip it.
    """
    existing = conn.execute(
        """SELECT path_id FROM file_paths
           WHERE file_id=? AND folder_id=? AND source_node_id=?""",
        (file_id, folder_id, source_node_id),
    ).fetchone()
    if existing:
        return  # already exists, skip

    path_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO file_paths
           (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
           VALUES (?, ?, ?, 0, ?, ?)""",
        (path_id, file_id, folder_id, source_node_id, _actor_id()),
    )


def _branch_folder_ids_for_node(conn, node_id: str) -> set[str]:
    """Branch folder_ids this node should mount.

    Includes:
    - The folder of the branch chain the node currently lives on
    - Folders of branches that use this node as start (parent_node_id)
      or merge (merge_node_id) — anchors live on the main chain
    """
    folders: set[str] = set()
    node = conn.execute(
        "SELECT chain_id FROM nodes WHERE node_id=?", (node_id,)
    ).fetchone()
    if node and node["chain_id"]:
        chain = conn.execute(
            "SELECT folder_id, parent_chain_id FROM chains WHERE chain_id=?",
            (node["chain_id"],),
        ).fetchone()
        if chain and chain["folder_id"] and chain["parent_chain_id"] is not None:
            folders.add(chain["folder_id"])

    # Start / merge anchors on main (or any parent chain)
    for row in conn.execute(
        """SELECT folder_id FROM chains
           WHERE folder_id IS NOT NULL
             AND parent_chain_id IS NOT NULL
             AND (parent_node_id=? OR merge_node_id=?)""",
        (node_id, node_id),
    ).fetchall():
        if row["folder_id"]:
            folders.add(row["folder_id"])
    return folders


def _sync_node_derived_paths(
    conn,
    node_id: str,
    *,
    file_id: str | None = None,
) -> None:
    """Ensure every attachment on *node_id* has group + branch-folder derived paths.

    - Group folder path when node.group_id is set
    - Branch folder path when node is on a branch, OR is that branch's start/merge anchor
    - Drops stale derived paths for this node that point at neither target folder
      (e.g. after group/chain change). Persistent paths (source_node_id NULL) are
      never touched.
    """
    node = conn.execute(
        "SELECT * FROM nodes WHERE node_id=?", (node_id,)
    ).fetchone()
    if not node:
        return

    target_folders: set[str] = set()

    if node["group_id"]:
        grp = conn.execute(
            "SELECT folder_id FROM node_groups WHERE group_id=?",
            (node["group_id"],),
        ).fetchone()
        if grp and grp["folder_id"]:
            target_folders.add(grp["folder_id"])

    target_folders |= _branch_folder_ids_for_node(conn, node_id)

    if file_id:
        file_ids = [file_id]
    else:
        file_ids = [
            r["file_id"]
            for r in conn.execute(
                "SELECT file_id FROM file_nodes WHERE node_id=?", (node_id,)
            ).fetchall()
        ]

    for fid in file_ids:
        for folder_id in target_folders:
            _upsert_derived_path(conn, fid, folder_id, node_id)

        # Remove obsolete derived paths for this node that are not current targets
        if target_folders:
            placeholders = ",".join("?" * len(target_folders))
            conn.execute(
                f"""DELETE FROM file_paths
                    WHERE source_node_id=? AND file_id=?
                      AND folder_id NOT IN ({placeholders})""",
                (node_id, fid, *target_folders),
            )
        else:
            # No group and not on a branch folder — drop all derived for this node/file
            conn.execute(
                "DELETE FROM file_paths WHERE source_node_id=? AND file_id=?",
                (node_id, fid),
            )


def _sync_chain_branch_paths(conn, chain_id: str) -> None:
    """Sync derived paths for every node on a branch + its start/merge anchors."""
    ch = conn.execute(
        "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
    ).fetchone()
    if not ch:
        return
    # Nodes living on the branch
    for row in conn.execute(
        "SELECT node_id FROM nodes WHERE chain_id=?", (chain_id,)
    ).fetchall():
        _sync_node_derived_paths(conn, row["node_id"])
    # Start / merge anchors (may live on parent chain)
    if ch["parent_node_id"]:
        _sync_node_derived_paths(conn, ch["parent_node_id"])
    try:
        merge_id = ch["merge_node_id"]
    except (KeyError, IndexError):
        merge_id = None
    if merge_id:
        _sync_node_derived_paths(conn, merge_id)


def detach_file_from_node(
    collection_id: str, node_id: str, file_id: str
) -> None:
    conn = _open_db(collection_id)
    try:
        with conn:
            fn = conn.execute(
                "SELECT * FROM file_nodes WHERE file_id=? AND node_id=?",
                (file_id, node_id),
            ).fetchone()
            if not fn:
                raise HTTPException(
                    404,
                    f"File '{file_id}' is not attached to node '{node_id}'",
                )

            # Delete file_nodes record
            conn.execute(
                "DELETE FROM file_nodes WHERE file_id=? AND node_id=?",
                (file_id, node_id),
            )

            # Delete derived paths for this node
            conn.execute(
                "DELETE FROM file_paths WHERE file_id=? AND source_node_id=?",
                (file_id, node_id),
            )

        emit_event(
            "file_path.removed",
            collection_id,
            {"file_id": file_id, "node_id": node_id},
        )
        try:
            from src.file_mgmt.todo_suggestions import schedule_for_node

            schedule_for_node(collection_id, node_id)
        except Exception:
            logger.debug("todo suggestion schedule after detach_file failed", exc_info=True)
    finally:
        conn.close()


# --- Message CRUD ---


def _row_to_file_version(row, collection_id: str | None = None) -> FileVersionOut:
    from src.file_mgmt.models import FileVersionOut

    blob_ok = True
    if collection_id:
        try:
            from src.file_mgmt.storage_paths import version_blob_exists

            blob_ok = version_blob_exists(
                collection_id,
                row["file_id"],
                row["version_id"],
                row["storage_file_id"],
            )
        except Exception:
            blob_ok = False

    return FileVersionOut(
        version_id=row["version_id"],
        file_id=row["file_id"],
        version_no=row["version_no"],
        storage_file_id=row["storage_file_id"],
        archived=bool(row["archived"]),
        commit_message=row["commit_message"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        blob_available=blob_ok,
    )

