"""Business logic for file-management metadata CRUD (Phase 2–5).

Pure functions: each takes collection_id as the first argument,
opens a per-collection SQLite connection via store.get_db(),
executes SQL, and returns Pydantic models. Write operations call
emit_event() after the transaction commits.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from fastapi import HTTPException

from src.file_mgmt.events import emit_event
from src.file_mgmt.models import (
    ArchiveToggle,
    ChainCreate,
    ChainOut,
    ChainUpdate,
    EndChainRequest,
    FileDetail,
    FileOut,
    FilePathOut,
    FileSummary,
    FileVersionOut,
    FolderCreate,
    FolderOut,
    FolderTree,
    FolderUpdate,
    GroupCreate,
    GroupOut,
    GroupUpdate,
    MessageCreate,
    MessageOut,
    MessageUpdate,
    NodeCreate,
    NodeOut,
    NodeReorder,
    NodeUpdate,
    OldVersionOut,
)
from src.file_mgmt.store import get_db

logger = logging.getLogger("file_mgmt.service")

COLLECTIONS_DIR = Path("data").resolve() / "collections"
MAX_VERSIONS = 20

# === Helpers ===


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_collection(collection_id: str) -> None:
    from src.collections.store import get_collection_meta

    if not get_collection_meta(collection_id):
        raise HTTPException(
            status_code=404,
            detail=f"Collection '{collection_id}' not found",
        )


# ── Name uniqueness (same parent folder / same directory) ──────


def _split_display_name(name: str) -> tuple[str, str]:
    """Split 'report.pdf' → ('report', '.pdf'); 'notes' → ('notes', '')."""
    p = Path(name)
    suffix = p.suffix  # includes leading dot, or ""
    if not suffix:
        return name, ""
    stem = name[: -len(suffix)]
    return stem, suffix


def suggest_unique_name(desired: str, existing: set[str]) -> str:
    """Return desired if free, else 'stem (1).ext', 'stem (2).ext', … (case-insensitive)."""
    desired = (desired or "").strip() or "untitled"
    taken = {n.casefold() for n in existing if n}
    if desired.casefold() not in taken:
        return desired
    stem, ext = _split_display_name(desired)
    # If already ends with " (N)", strip before numbering
    base = stem
    n = 1
    while True:
        candidate = f"{base} ({n}){ext}"
        if candidate.casefold() not in taken:
            return candidate
        n += 1
        if n > 9999:
            return f"{base} ({uuid.uuid4().hex[:6]}){ext}"


def _raise_name_conflict(resource: str, name: str, suggested: str) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": "name_conflict",
            "resource": resource,
            "name": name,
            "suggested_name": suggested,
            "message": f"A {resource} named '{name}' already exists here.",
        },
    )


def _sibling_folder_names(
    conn,
    parent_folder_id: str | None,
    *,
    exclude_folder_id: str | None = None,
) -> set[str]:
    if parent_folder_id is None:
        rows = conn.execute(
            "SELECT folder_id, name FROM folders WHERE parent_folder_id IS NULL"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT folder_id, name FROM folders WHERE parent_folder_id=?",
            (parent_folder_id,),
        ).fetchall()
    names: set[str] = set()
    for r in rows:
        if exclude_folder_id and r["folder_id"] == exclude_folder_id:
            continue
        if r["name"]:
            names.add(r["name"])
    return names


def _assert_folder_name_free(
    conn,
    parent_folder_id: str | None,
    name: str,
    *,
    exclude_folder_id: str | None = None,
) -> None:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Folder name is required")
    existing = _sibling_folder_names(
        conn, parent_folder_id, exclude_folder_id=exclude_folder_id
    )
    if name.casefold() in {n.casefold() for n in existing}:
        _raise_name_conflict(
            "folder", name, suggest_unique_name(name, existing)
        )


def _file_display_names_in_folder(
    conn,
    folder_id: str | None,
    *,
    exclude_file_id: str | None = None,
) -> set[str]:
    """Current-version filenames of files mounted in this folder.

    ``folder_id=None`` → root orphans (files with no file_paths rows).
    """
    if folder_id is None:
        rows = conn.execute(
            """SELECT f.file_id, fv.storage_file_id AS filename
               FROM files f
               LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
               WHERE f.file_id NOT IN (SELECT file_id FROM file_paths)"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT f.file_id, fv.storage_file_id AS filename
               FROM file_paths fp
               JOIN files f ON f.file_id = fp.file_id
               LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
               WHERE fp.folder_id=?""",
            (folder_id,),
        ).fetchall()
    names: set[str] = set()
    for r in rows:
        if exclude_file_id and r["file_id"] == exclude_file_id:
            continue
        fn = r["filename"]
        if fn:
            # basename only (upload-folder may store relative paths)
            names.add(Path(fn).name)
    return names


def _assert_file_name_free(
    conn,
    folder_id: str | None,
    filename: str,
    *,
    exclude_file_id: str | None = None,
) -> str:
    """Return basename; raise name_conflict if taken in folder (or root)."""
    base = Path((filename or "").strip() or "unnamed").name
    existing = _file_display_names_in_folder(
        conn, folder_id, exclude_file_id=exclude_file_id
    )
    if base.casefold() in {n.casefold() for n in existing}:
        _raise_name_conflict(
            "file", base, suggest_unique_name(base, existing)
        )
    return base


def _open_db(collection_id: str):
    _validate_collection(collection_id)
    from src.file_mgmt.store import init_collection_db
    init_collection_db(collection_id)  # idempotent: creates, backfills, migrates
    return get_db(collection_id)


def _row_to_folder(row) -> FolderOut:
    return FolderOut(
        folder_id=row["folder_id"],
        parent_folder_id=row["parent_folder_id"],
        name=row["name"],
        kind=row["kind"],
        is_system=bool(row["is_system"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
        icon_type=_row_icon(row, "icon_type"),
        icon_value=_row_icon(row, "icon_value"),
        icon_color=_row_icon(row, "icon_color"),
    )


def _row_icon(row, key: str) -> str | None:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _row_to_group(row, node_count: int = 0, is_system: bool = False) -> GroupOut:
    return GroupOut(
        group_id=row["group_id"],
        folder_id=row["folder_id"],
        name=row["name"],
        description=row["description"],
        created_by=row["created_by"],
        node_count=node_count,
        icon_type=_row_icon(row, "icon_type"),
        icon_value=_row_icon(row, "icon_value"),
        icon_color=_row_icon(row, "icon_color"),
        is_system=is_system,
    )


def _row_to_chain(row, has_end_node: bool = False, node_count: int = 0) -> ChainOut:
    # merge_node_id may be missing on very old Row objects before backfill
    try:
        merge_node_id = row["merge_node_id"]
    except (KeyError, IndexError):
        merge_node_id = None
    return ChainOut(
        chain_id=row["chain_id"],
        parent_chain_id=row["parent_chain_id"],
        parent_node_id=row["parent_node_id"],
        folder_id=row["folder_id"],
        title=row["title"],
        created_by=row["created_by"],
        is_main=row["parent_chain_id"] is None,
        has_end_node=has_end_node,
        node_count=node_count,
        merge_node_id=merge_node_id,
    )


def _row_to_node(row) -> NodeOut:
    return NodeOut(
        node_id=row["node_id"],
        chain_id=row["chain_id"],
        group_id=row["group_id"],
        node_type=row["node_type"],
        title=row["title"],
        order=row["order"],
        event_time=row["event_time"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        version=row["version"],
        has_definitive_file=False,
    )


def _main_chain_id(conn) -> str:
    row = conn.execute(
        "SELECT chain_id FROM chains WHERE parent_chain_id IS NULL"
    ).fetchone()
    if not row:
        raise HTTPException(500, "Main chain not found")
    return row["chain_id"]


def _is_descendant(conn, folder_id: str, candidate_parent: str) -> bool:
    """True if candidate_parent lives inside the subtree of folder_id."""
    current = candidate_parent
    visited: set[str] = set()
    while current and current not in visited:
        if current == folder_id:
            return True
        visited.add(current)
        row = conn.execute(
            "SELECT parent_folder_id FROM folders WHERE folder_id=?",
            (current,),
        ).fetchone()
        if not row:
            return False
        current = row["parent_folder_id"]
    return False


def _delete_folder_subtree(conn, folder_id: str) -> None:
    """Delete a folder and all descendants, with kind-specific cleanup."""
    folder = conn.execute(
        "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
    ).fetchone()
    if not folder:
        return

    children = conn.execute(
        "SELECT folder_id FROM folders WHERE parent_folder_id=?",
        (folder_id,),
    ).fetchall()
    for child in children:
        _delete_folder_subtree(conn, child["folder_id"])

    kind = folder["kind"]
    if kind == "user_group":
        conn.execute(
            "UPDATE nodes SET group_id=NULL WHERE group_id IN "
            "(SELECT group_id FROM node_groups WHERE folder_id=?)",
            (folder_id,),
        )
        conn.execute(
            "DELETE FROM node_groups WHERE folder_id=?", (folder_id,)
        )
        # Same residual cleanup as plain folders — previously missing, left
        # orphan folder messages that only showed under Nested at root with
        # blank source tags (no folder row to resolve the name).
        conn.execute("DELETE FROM file_paths WHERE folder_id=?", (folder_id,))
        conn.execute(
            "DELETE FROM messages WHERE owner_type='folder' AND owner_id=?",
            (folder_id,),
        )
        conn.execute(
            "DELETE FROM folders WHERE folder_id=?", (folder_id,)
        )
    elif kind == "branch":
        chain = conn.execute(
            "SELECT chain_id FROM chains WHERE folder_id=?", (folder_id,)
        ).fetchone()
        if chain:
            _delete_chain_subtree(conn, chain["chain_id"])
        else:
            conn.execute("DELETE FROM file_paths WHERE folder_id=?", (folder_id,))
            conn.execute(
                "DELETE FROM messages WHERE owner_type='folder' AND owner_id=?",
                (folder_id,),
            )
            conn.execute(
                "DELETE FROM folders WHERE folder_id=?", (folder_id,)
            )
    else:
        conn.execute("DELETE FROM file_paths WHERE folder_id=?", (folder_id,))
        conn.execute(
            "DELETE FROM messages WHERE owner_type='folder' AND owner_id=?",
            (folder_id,),
        )
        conn.execute(
            "DELETE FROM folders WHERE folder_id=?", (folder_id,)
        )


def _clear_node_inbound_fks(conn, node_id: str) -> None:
    """Clear or remove rows that reference *node_id* so DELETE FROM nodes succeeds."""
    # Messages may cite this node as the source of a cross-owner note
    conn.execute(
        "UPDATE messages SET source_node_id=NULL WHERE source_node_id=?",
        (node_id,),
    )
    # Closed branches that merge into this main-chain node → reopen loop
    conn.execute(
        "UPDATE chains SET merge_node_id=NULL WHERE merge_node_id=?",
        (node_id,),
    )


def _purge_node_owned_rows(conn, node_id: str) -> None:
    """Remove rows owned by *node_id* (attachments, derived paths, node messages)."""
    conn.execute("DELETE FROM file_nodes WHERE node_id=?", (node_id,))
    conn.execute("DELETE FROM file_paths WHERE source_node_id=?", (node_id,))
    conn.execute(
        "DELETE FROM messages WHERE owner_type='node' AND owner_id=?",
        (node_id,),
    )
    _clear_node_inbound_fks(conn, node_id)


def _delete_chain_subtree(conn, chain_id: str) -> None:
    """Delete a chain, its sub-chains, nodes, and associated folder."""
    sub_chains = conn.execute(
        "SELECT chain_id FROM chains WHERE parent_chain_id=?", (chain_id,)
    ).fetchall()
    for sc in sub_chains:
        _delete_chain_subtree(conn, sc["chain_id"])

    chain = conn.execute(
        "SELECT folder_id, merge_node_id FROM chains WHERE chain_id=?",
        (chain_id,),
    ).fetchone()

    # If this branch closed onto a merge node on the parent chain, remove that
    # merge node too (same as reopen_chain) after clearing the FK pointer.
    merge_id = None
    if chain is not None:
        try:
            merge_id = chain["merge_node_id"]
        except (KeyError, IndexError):
            merge_id = None
    if merge_id:
        conn.execute(
            "UPDATE chains SET merge_node_id=NULL WHERE chain_id=?",
            (chain_id,),
        )
        # Merge node lives on parent — purge its deps then delete
        _purge_node_owned_rows(conn, merge_id)
        conn.execute("DELETE FROM nodes WHERE node_id=?", (merge_id,))

    # Purge deps for every node on this chain before deleting nodes
    node_rows = conn.execute(
        "SELECT node_id FROM nodes WHERE chain_id=?", (chain_id,)
    ).fetchall()
    for nr in node_rows:
        _purge_node_owned_rows(conn, nr["node_id"])

    conn.execute("DELETE FROM nodes WHERE chain_id=?", (chain_id,))

    # Delete the chains record BEFORE the folder to avoid FK violation
    # (chains.folder_id REFERENCES folders).  Capture folder_id first.
    folder_id = chain["folder_id"] if chain else None
    conn.execute("DELETE FROM chains WHERE chain_id=?", (chain_id,))

    if folder_id:
        # Branch folder may still hold derived paths from start/merge anchors
        # (parent lives on main; paths.source_node_id points at parent, folder_id
        # at this branch). Clear them or DELETE folder hits FOREIGN KEY.
        conn.execute("DELETE FROM file_paths WHERE folder_id=?", (folder_id,))
        conn.execute(
            "DELETE FROM messages WHERE owner_type='folder' AND owner_id=?",
            (folder_id,),
        )
        children = conn.execute(
            "SELECT folder_id FROM folders WHERE parent_folder_id=?",
            (folder_id,),
        ).fetchall()
        for child in children:
            _delete_folder_subtree(conn, child["folder_id"])
        conn.execute(
            "DELETE FROM folders WHERE folder_id=?",
            (folder_id,),
        )


# === Folder CRUD ===


def list_folders(collection_id: str) -> list[FolderOut]:
    conn = _open_db(collection_id)
    try:
        rows = conn.execute("SELECT * FROM folders ORDER BY name").fetchall()
        return [_row_to_folder(r) for r in rows]
    finally:
        conn.close()


def get_folder(collection_id: str, folder_id: str) -> FolderOut:
    conn = _open_db(collection_id)
    try:
        row = conn.execute(
            "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Folder '{folder_id}' not found")
        return _row_to_folder(row)
    finally:
        conn.close()


def get_folder_tree(collection_id: str) -> list[FolderTree]:
    conn = _open_db(collection_id)
    try:
        rows = conn.execute("SELECT * FROM folders ORDER BY name").fetchall()
        children_map: dict[str | None, list] = {}
        for r in rows:
            children_map.setdefault(r["parent_folder_id"], []).append(r)

        def build(row) -> FolderTree:
            kids = [build(c) for c in children_map.get(row["folder_id"], [])]
            base = _row_to_folder(row)
            # Compute accurate file_count
            fid = row["folder_id"]
            cnt = conn.execute(
                "SELECT COUNT(DISTINCT file_id) FROM file_paths WHERE folder_id=?",
                (fid,),
            ).fetchone()[0]
            return FolderTree(
                **base.model_dump(),
                children=kids,
                file_count=cnt,
            )

        roots = children_map.get(None, [])
        return [build(r) for r in roots]
    finally:
        conn.close()


def create_folder(collection_id: str, req: FolderCreate) -> FolderOut:
    kind = (req.kind or "plain")
    if kind != "plain":
        raise HTTPException(
            400,
            "This endpoint only creates plain folders. "
            "Use the group or chain API for user_group / branch kinds.",
        )

    conn = _open_db(collection_id)
    try:
        with conn:
            from src.file_mgmt.store import _ensure_folders_icon_columns

            _ensure_folders_icon_columns(conn)
            now = _now_iso()
            parent_id = req.parent_folder_id
            name = (req.name or "").strip()
            if parent_id:
                parent = conn.execute(
                    "SELECT kind, is_system FROM folders WHERE folder_id=?",
                    (parent_id,),
                ).fetchone()
                if not parent:
                    raise HTTPException(404, f"Parent folder '{parent_id}' not found")
                if parent["kind"] in ("system_group", "user_group"):
                    raise HTTPException(
                        400,
                        "Group folders are flat - cannot contain sub-folders",
                    )

            _assert_folder_name_free(conn, parent_id, name)

            folder_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO folders
                   (folder_id, parent_folder_id, name, kind, is_system,
                    created_by, created_at, updated_at, version,
                    icon_type, icon_value, icon_color)
                   VALUES (?, ?, ?, 'plain', 0, 'local', ?, ?, 1, ?, ?, ?)""",
                (
                    folder_id,
                    parent_id,
                    name,
                    now,
                    now,
                    req.icon_type,
                    req.icon_value,
                    req.icon_color,
                ),
            )
            row = conn.execute(
                "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()

        emit_event("folder.created", collection_id, {"folder_id": folder_id})
        return _row_to_folder(row)
    finally:
        conn.close()


def update_folder(
    collection_id: str, folder_id: str, req: FolderUpdate
) -> FolderOut:
    updates = req.model_dump(exclude_unset=True)

    conn = _open_db(collection_id)
    try:
        with conn:
            from src.file_mgmt.store import _ensure_folders_icon_columns

            _ensure_folders_icon_columns(conn)
            folder = conn.execute(
                "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()
            if not folder:
                raise HTTPException(404, f"Folder '{folder_id}' not found")

            if folder["is_system"]:
                if "name" in updates:
                    raise HTTPException(403, "System folders cannot be renamed")
                if "parent_folder_id" in updates:
                    raise HTTPException(403, "System folders cannot be moved")
                for ik in ("icon_type", "icon_value", "icon_color"):
                    if ik in updates:
                        raise HTTPException(
                            403, "System folders cannot change icons"
                        )

            if "parent_folder_id" in updates:
                new_parent = updates["parent_folder_id"]
                if new_parent is not None:
                    parent = conn.execute(
                        "SELECT kind FROM folders WHERE folder_id=?",
                        (new_parent,),
                    ).fetchone()
                    if not parent:
                        raise HTTPException(
                            404, f"Parent folder '{new_parent}' not found"
                        )
                    if parent["kind"] in ("system_group", "user_group"):
                        raise HTTPException(
                            400,
                            "Group folders are flat - cannot contain sub-folders",
                        )
                    if _is_descendant(conn, folder_id, new_parent):
                        raise HTTPException(
                            400,
                            "Cannot move a folder into its own subtree",
                        )

            set_clauses: list[str] = []
            params: list = []

            # Effective parent after this update (for name uniqueness)
            effective_parent = folder["parent_folder_id"]
            if "parent_folder_id" in updates:
                effective_parent = updates["parent_folder_id"]

            if "name" in updates and updates["name"] is not None:
                new_name = (updates["name"] or "").strip()
                _assert_folder_name_free(
                    conn,
                    effective_parent,
                    new_name,
                    exclude_folder_id=folder_id,
                )
                set_clauses.append("name = ?")
                params.append(new_name)
                updates["name"] = new_name

            if "parent_folder_id" in updates:
                # Moving without rename: still check name free under new parent
                if "name" not in updates or updates.get("name") is None:
                    _assert_folder_name_free(
                        conn,
                        updates["parent_folder_id"],
                        folder["name"],
                        exclude_folder_id=folder_id,
                    )
                set_clauses.append("parent_folder_id = ?")
                params.append(updates["parent_folder_id"])

            for field in ("icon_type", "icon_value", "icon_color"):
                if field in updates:
                    set_clauses.append(f"{field} = ?")
                    params.append(updates[field])

            set_clauses.append("updated_at = ?")
            params.append(_now_iso())
            set_clauses.append("version = version + 1")
            params.extend([folder_id, req.version])

            cursor = conn.execute(
                f"UPDATE folders SET {', '.join(set_clauses)} "
                "WHERE folder_id = ? AND version = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise HTTPException(
                    409, "Folder was modified by another user (version conflict)"
                )

            row = conn.execute(
                "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()

            # Sync bound node_group name + icons (folder grid prefers group icon)
            grp = conn.execute(
                "SELECT group_id FROM node_groups WHERE folder_id=?",
                (folder_id,),
            ).fetchone()
            if grp:
                g_sets: list[str] = []
                g_params: list = []
                if "name" in updates and updates["name"] is not None:
                    g_sets.append("name = ?")
                    g_params.append(updates["name"])
                for field in ("icon_type", "icon_value", "icon_color"):
                    if field in updates:
                        g_sets.append(f"{field} = ?")
                        g_params.append(updates[field])
                if g_sets:
                    g_params.append(grp["group_id"])
                    conn.execute(
                        f"UPDATE node_groups SET {', '.join(g_sets)} WHERE group_id=?",
                        g_params,
                    )

        if "name" in updates and updates["name"] is not None:
            emit_event("folder.renamed", collection_id, {"folder_id": folder_id})
        if "parent_folder_id" in updates:
            emit_event("folder.moved", collection_id, {"folder_id": folder_id})
        return _row_to_folder(row)
    finally:
        conn.close()


def delete_folder(collection_id: str, folder_id: str) -> None:
    conn = _open_db(collection_id)
    try:
        with conn:
            folder = conn.execute(
                "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()
            if not folder:
                raise HTTPException(404, f"Folder '{folder_id}' not found")

            if folder["is_system"]:
                raise HTTPException(403, "System folders cannot be deleted")

            _delete_folder_subtree(conn, folder_id)

        emit_event("folder.deleted", collection_id, {"folder_id": folder_id})
    finally:
        conn.close()


# === NodeGroup CRUD ===


def list_groups(collection_id: str) -> list[GroupOut]:
    from src.file_mgmt.store import _ensure_node_groups_icon_columns

    conn = _open_db(collection_id)
    try:
        _ensure_node_groups_icon_columns(conn)
        conn.commit()
        rows = conn.execute(
            """SELECT g.*, COUNT(n.node_id) AS node_count,
                      COALESCE(f.is_system, 0) AS folder_is_system
               FROM node_groups g
               LEFT JOIN nodes n ON n.group_id = g.group_id
               LEFT JOIN folders f ON f.folder_id = g.folder_id
               GROUP BY g.group_id
               ORDER BY g.name"""
        ).fetchall()
        result: list[GroupOut] = []
        for r in rows:
            gname = (r["name"] or "").strip().lower()
            is_sys = bool(r["folder_is_system"]) or gname in (
                "meeting",
                "notes",
                "note",
            )
            result.append(_row_to_group(r, r["node_count"], is_sys))
        return result
    finally:
        conn.close()


def create_group(collection_id: str, req: GroupCreate) -> GroupOut:
    from src.file_mgmt.store import _ensure_node_groups_icon_columns

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_node_groups_icon_columns(conn)
            now = _now_iso()

            if req.bind_existing_folder_id:
                fld = conn.execute(
                    "SELECT * FROM folders WHERE folder_id=?",
                    (req.bind_existing_folder_id,),
                ).fetchone()
                if not fld:
                    raise HTTPException(
                        404, f"Folder '{req.bind_existing_folder_id}' not found"
                    )
                if fld["kind"] != "plain":
                    raise HTTPException(400, "Can only bind plain folders")
                child = conn.execute(
                    "SELECT 1 FROM folders WHERE parent_folder_id=? LIMIT 1",
                    (req.bind_existing_folder_id,),
                ).fetchone()
                if child:
                    raise HTTPException(
                        400,
                        "Cannot bind a folder that contains sub-folders",
                    )
                bound = conn.execute(
                    "SELECT 1 FROM node_groups WHERE folder_id=? LIMIT 1",
                    (req.bind_existing_folder_id,),
                ).fetchone()
                if bound:
                    raise HTTPException(
                        400, "Folder is already bound to another group"
                    )
                conn.execute(
                    "UPDATE folders SET kind='user_group', updated_at=? "
                    "WHERE folder_id=?",
                    (now, req.bind_existing_folder_id),
                )
                folder_id = req.bind_existing_folder_id
            else:
                group_folder_name = (req.name or "").strip()
                _assert_folder_name_free(conn, None, group_folder_name)
                folder_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO folders
                       (folder_id, parent_folder_id, name, kind, is_system,
                        created_by, created_at, updated_at, version)
                       VALUES (?, NULL, ?, 'user_group', 0, 'local', ?, ?, 1)""",
                    (folder_id, group_folder_name, now, now),
                )

            group_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO node_groups
                   (group_id, folder_id, name, description, created_by,
                    icon_type, icon_value, icon_color)
                   VALUES (?, ?, ?, ?, 'local', ?, ?, ?)""",
                (
                    group_id,
                    folder_id,
                    req.name,
                    req.description,
                    req.icon_type,
                    req.icon_value,
                    req.icon_color,
                ),
            )
            row = conn.execute(
                "SELECT * FROM node_groups WHERE group_id=?", (group_id,)
            ).fetchone()

        emit_event("group.created", collection_id, {"group_id": group_id})
        return _row_to_group(row, is_system=False)
    finally:
        conn.close()


def update_group(
    collection_id: str, group_id: str, req: GroupUpdate
) -> GroupOut:
    from src.file_mgmt.store import _ensure_node_groups_icon_columns

    updates = req.model_dump(exclude_unset=True)

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_node_groups_icon_columns(conn)
            grp = conn.execute(
                "SELECT * FROM node_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if not grp:
                raise HTTPException(404, f"Group '{group_id}' not found")

            fld = conn.execute(
                "SELECT is_system, name FROM folders WHERE folder_id=?",
                (grp["folder_id"],),
            ).fetchone()
            gname = (grp["name"] or "").strip().lower()
            is_system = bool(fld and fld["is_system"]) or gname in (
                "meeting",
                "notes",
                "note",
            )
            if is_system:
                forbidden = {"name", "icon_type", "icon_value", "icon_color"}
                if forbidden & set(updates.keys()):
                    raise HTTPException(403, "System groups cannot be modified")

            set_clauses: list[str] = []
            params: list = []

            if "name" in updates and updates["name"] is not None:
                set_clauses.append("name = ?")
                params.append(updates["name"])
                conn.execute(
                    "UPDATE folders SET name=?, updated_at=? WHERE folder_id=?",
                    (updates["name"], _now_iso(), grp["folder_id"]),
                )

            if "description" in updates:
                set_clauses.append("description = ?")
                params.append(updates["description"])

            for field in ("icon_type", "icon_value", "icon_color"):
                if field in updates:
                    set_clauses.append(f"{field} = ?")
                    params.append(updates[field])

            # Rebind folder (F-b): move only paths of files attached to this group's nodes
            if "rebind_folder_id" in updates and updates["rebind_folder_id"] is not None:
                if is_system:
                    raise HTTPException(403, "System groups cannot rebind folder")
                new_fid = updates["rebind_folder_id"]
                old_fid = grp["folder_id"]
                if new_fid != old_fid:
                    new_fld = conn.execute(
                        "SELECT * FROM folders WHERE folder_id=?", (new_fid,)
                    ).fetchone()
                    if not new_fld:
                        raise HTTPException(404, f"Folder '{new_fid}' not found")
                    if new_fld["kind"] != "plain":
                        raise HTTPException(400, "Can only rebind to plain folders")
                    child = conn.execute(
                        "SELECT 1 FROM folders WHERE parent_folder_id=? LIMIT 1",
                        (new_fid,),
                    ).fetchone()
                    if child:
                        raise HTTPException(
                            400,
                            "Cannot bind a folder that contains sub-folders",
                        )
                    bound = conn.execute(
                        "SELECT 1 FROM node_groups WHERE folder_id=? AND group_id!=? LIMIT 1",
                        (new_fid, group_id),
                    ).fetchone()
                    if bound:
                        raise HTTPException(
                            400, "Folder is already bound to another group"
                        )

                    # F-b: files attached to nodes of this group that have a path in old folder
                    if old_fid:
                        attach_files = conn.execute(
                            """SELECT DISTINCT fn.file_id FROM file_nodes fn
                               JOIN nodes n ON n.node_id = fn.node_id
                               WHERE n.group_id=?""",
                            (group_id,),
                        ).fetchall()
                        for af in attach_files:
                            fid = af["file_id"]
                            paths = conn.execute(
                                """SELECT path_id, source_node_id FROM file_paths
                                   WHERE file_id=? AND folder_id=?""",
                                (fid, old_fid),
                            ).fetchall()
                            for pr in paths:
                                source_node_id = pr["source_node_id"]
                                if source_node_id is None:
                                    conflict = conn.execute(
                                        """SELECT path_id FROM file_paths
                                           WHERE file_id=? AND folder_id=?
                                             AND source_node_id IS NULL""",
                                        (fid, new_fid),
                                    ).fetchone()
                                else:
                                    conflict = conn.execute(
                                        """SELECT path_id FROM file_paths
                                           WHERE file_id=? AND folder_id=?
                                             AND source_node_id=?""",
                                        (fid, new_fid, source_node_id),
                                    ).fetchone()
                                if conflict:
                                    conn.execute(
                                        "DELETE FROM file_paths WHERE path_id=?",
                                        (pr["path_id"],),
                                    )
                                else:
                                    conn.execute(
                                        "UPDATE file_paths SET folder_id=? WHERE path_id=?",
                                        (new_fid, pr["path_id"]),
                                    )

                    now = _now_iso()
                    if old_fid:
                        conn.execute(
                            "UPDATE folders SET kind='plain', updated_at=? "
                            "WHERE folder_id=? AND is_system=0",
                            (now, old_fid),
                        )
                    conn.execute(
                        "UPDATE folders SET kind='user_group', updated_at=? WHERE folder_id=?",
                        (now, new_fid),
                    )
                    set_clauses.append("folder_id = ?")
                    params.append(new_fid)

            if set_clauses:
                params.append(group_id)
                conn.execute(
                    f"UPDATE node_groups SET {', '.join(set_clauses)} "
                    "WHERE group_id=?",
                    params,
                )

            row = conn.execute(
                "SELECT * FROM node_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE group_id=?", (group_id,)
            ).fetchone()[0]

        if "name" in updates:
            emit_event("group.renamed", collection_id, {"group_id": group_id})
        return _row_to_group(row, count, is_system)
    finally:
        conn.close()


def delete_group(collection_id: str, group_id: str) -> None:
    """Delete a user group: unassign nodes, keep folder (kind → plain)."""
    conn = _open_db(collection_id)
    try:
        with conn:
            grp = conn.execute(
                "SELECT * FROM node_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if not grp:
                raise HTTPException(404, f"Group '{group_id}' not found")

            fld = conn.execute(
                "SELECT is_system FROM folders WHERE folder_id=?",
                (grp["folder_id"],),
            ).fetchone()
            gname = (grp["name"] or "").strip().lower()
            if (fld and fld["is_system"]) or gname in ("meeting", "notes", "note"):
                raise HTTPException(403, "System groups cannot be deleted")

            conn.execute(
                "UPDATE nodes SET group_id=NULL WHERE group_id=?", (group_id,)
            )
            conn.execute(
                "DELETE FROM node_groups WHERE group_id=?", (group_id,)
            )
            # Keep folder and files; demote to plain so it can be rebound later
            if grp["folder_id"]:
                conn.execute(
                    "UPDATE folders SET kind='plain', updated_at=? WHERE folder_id=? AND is_system=0",
                    (_now_iso(), grp["folder_id"]),
                )

        emit_event("group.deleted", collection_id, {"group_id": group_id})
    finally:
        conn.close()


# === Chain CRUD ===


def list_chains(collection_id: str) -> list[ChainOut]:
    from src.file_mgmt.store import _ensure_chains_merge_node_id

    conn = _open_db(collection_id)
    try:
        _ensure_chains_merge_node_id(conn)
        conn.commit()
        rows = conn.execute("SELECT * FROM chains ORDER BY title").fetchall()
        result: list[ChainOut] = []
        for r in rows:
            end = conn.execute(
                'SELECT 1 FROM nodes WHERE chain_id=? AND node_type="end" LIMIT 1',
                (r["chain_id"],),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM nodes WHERE chain_id=?",
                (r["chain_id"],),
            ).fetchone()
            try:
                merge_id = r["merge_node_id"]
            except (KeyError, IndexError):
                merge_id = None
            result.append(
                _row_to_chain(
                    r,
                    has_end_node=end is not None or bool(merge_id),
                    node_count=count["c"],
                )
            )
        return result
    finally:
        conn.close()


def create_chain(collection_id: str, req: ChainCreate) -> ChainOut:
    conn = _open_db(collection_id)
    try:
        with conn:
            main_id = _main_chain_id(conn)
            if req.parent_chain_id != main_id:
                raise HTTPException(
                    400,
                    "MVP only supports branching from the main chain",
                )

            pnode = conn.execute(
                "SELECT node_id FROM nodes WHERE node_id=? AND chain_id=?",
                (req.parent_node_id, main_id),
            ).fetchone()
            if not pnode:
                raise HTTPException(
                    400,
                    f"Parent node '{req.parent_node_id}' not found on main chain",
                )

            now = _now_iso()

            if req.bind_existing_folder_id:
                fld = conn.execute(
                    "SELECT * FROM folders WHERE folder_id=?",
                    (req.bind_existing_folder_id,),
                ).fetchone()
                if not fld:
                    raise HTTPException(
                        404, f"Folder '{req.bind_existing_folder_id}' not found"
                    )
                if fld["kind"] != "plain":
                    raise HTTPException(400, "Can only bind plain folders")
                child = conn.execute(
                    "SELECT 1 FROM folders WHERE parent_folder_id=? LIMIT 1",
                    (req.bind_existing_folder_id,),
                ).fetchone()
                if child:
                    raise HTTPException(
                        400, "Cannot bind a folder that contains sub-folders"
                    )
                # Phase 4: also check for files
                has_files = conn.execute(
                    "SELECT 1 FROM file_paths WHERE folder_id=? LIMIT 1",
                    (req.bind_existing_folder_id,),
                ).fetchone()
                if has_files:
                    raise HTTPException(
                        400,
                        "Cannot bind a folder that contains files",
                    )
                conn.execute(
                    "UPDATE folders SET kind='branch', updated_at=? WHERE folder_id=?",
                    (now, req.bind_existing_folder_id),
                )
                folder_id = req.bind_existing_folder_id
            else:
                folder_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO folders
                       (folder_id, parent_folder_id, name, kind, is_system,
                        created_by, created_at, updated_at, version)
                       VALUES (?, NULL, ?, 'branch', 0, 'local', ?, ?, 1)""",
                    (folder_id, req.title, now, now),
                )

            chain_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO chains
                   (chain_id, parent_chain_id, parent_node_id, folder_id,
                    title, created_by)
                   VALUES (?, ?, ?, ?, ?, 'local')""",
                (chain_id, req.parent_chain_id, req.parent_node_id,
                 folder_id, req.title),
            )
            # Start anchor lives on main — mount branch folder for its attachments
            _sync_node_derived_paths(conn, req.parent_node_id)
            row = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()

        emit_event("chain.created", collection_id, {"chain_id": chain_id})
        return _row_to_chain(row)
    finally:
        conn.close()


def update_chain(
    collection_id: str, chain_id: str, req: ChainUpdate
) -> ChainOut:
    updates = req.model_dump(exclude_unset=True)

    conn = _open_db(collection_id)
    try:
        with conn:
            ch = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            if not ch:
                raise HTTPException(404, f"Chain '{chain_id}' not found")

            if ch["parent_chain_id"] is None:
                raise HTTPException(403, "Main chain title cannot be changed")

            if "title" in updates and updates["title"] is not None:
                conn.execute(
                    "UPDATE chains SET title=? WHERE chain_id=?",
                    (updates["title"], chain_id),
                )
                if ch["folder_id"]:
                    conn.execute(
                        "UPDATE folders SET name=?, updated_at=? WHERE folder_id=?",
                        (updates["title"], _now_iso(), ch["folder_id"]),
                    )

            row = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()

        if "title" in updates:
            emit_event("chain.renamed", collection_id, {"chain_id": chain_id})
        return _row_to_chain(row)
    finally:
        conn.close()


def delete_chain(collection_id: str, chain_id: str) -> None:
    conn = _open_db(collection_id)
    try:
        with conn:
            ch = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            if not ch:
                raise HTTPException(404, f"Chain '{chain_id}' not found")

            if ch["parent_chain_id"] is None:
                raise HTTPException(403, "Main chain cannot be deleted")

            parent_node_id = ch["parent_node_id"]
            _delete_chain_subtree(conn, chain_id)

            # Parent start-anchor → normal event when no branch still references it
            if parent_node_id:
                other = conn.execute(
                    "SELECT 1 FROM chains WHERE parent_node_id=? LIMIT 1",
                    (parent_node_id,),
                ).fetchone()
                if not other:
                    conn.execute(
                        """UPDATE nodes
                           SET node_type='event', version=version+1
                           WHERE node_id=?""",
                        (parent_node_id,),
                    )

        emit_event("chain.deleted", collection_id, {"chain_id": chain_id})
    finally:
        conn.close()


def reopen_chain(collection_id: str, chain_id: str) -> ChainOut:
    """Re-open a closed branch.

    - Clear chains.merge_node_id (loop is open again)
    - Remove branch-local ``end`` marker nodes (dialog end placeholders)
    - **Keep** the main-chain merge node: move it onto this branch as the last
      ``event`` node (preserves title, files, messages)
    - Undo **only** path/file archives recorded at end_chain (merge time);
      user manual archives are left untouched
    """
    import json

    from src.file_mgmt.store import (
        _ensure_chains_merge_archive_json,
        _ensure_chains_merge_node_id,
    )

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_chains_merge_node_id(conn)
            _ensure_chains_merge_archive_json(conn)
            _ensure_path_archive_column(conn)
            ch = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            if not ch:
                raise HTTPException(404, f"Chain '{chain_id}' not found")

            if ch["parent_chain_id"] is None:
                raise HTTPException(400, "Main chain cannot be reopened")

            # Reverse merge-time archives only (from end_chain snapshot)
            merge_archive = {"path_ids": [], "file_ids": []}
            try:
                raw = ch["merge_archive_json"]
            except (KeyError, IndexError):
                raw = None
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        merge_archive["path_ids"] = list(parsed.get("path_ids") or [])
                        merge_archive["file_ids"] = list(parsed.get("file_ids") or [])
                except (TypeError, json.JSONDecodeError):
                    logger.warning(
                        "Invalid merge_archive_json on chain %s; skip restore",
                        chain_id,
                    )

            restored_paths = _unarchive_paths_by_ids(
                conn, merge_archive["path_ids"]
            )
            restored_files = 0
            for fid in merge_archive["file_ids"]:
                if _unarchive_file_if_archived(conn, collection_id, fid):
                    restored_files += 1

            # Clear snapshot so a later re-merge starts clean
            conn.execute(
                "UPDATE chains SET merge_archive_json=NULL WHERE chain_id=?",
                (chain_id,),
            )

            # Remove branch-local end markers only (not the merge node on main)
            end_rows = conn.execute(
                'SELECT node_id FROM nodes WHERE chain_id=? AND node_type="end"',
                (chain_id,),
            ).fetchall()
            for er in end_rows:
                _purge_node_owned_rows(conn, er["node_id"])
            conn.execute(
                'DELETE FROM nodes WHERE chain_id=? AND node_type="end"',
                (chain_id,),
            )

            try:
                merge_id = ch["merge_node_id"]
            except (KeyError, IndexError):
                merge_id = None

            # Detach merge pointer first (FK)
            conn.execute(
                "UPDATE chains SET merge_node_id=NULL WHERE chain_id=?",
                (chain_id,),
            )

            if merge_id:
                merge = conn.execute(
                    "SELECT * FROM nodes WHERE node_id=?", (merge_id,)
                ).fetchone()
                if merge:
                    max_row = conn.execute(
                        'SELECT COALESCE(MAX("order"), 0) AS m '
                        "FROM nodes WHERE chain_id=?",
                        (chain_id,),
                    ).fetchone()
                    new_order = int(max_row["m"]) + 1
                    # Move merge onto branch as a normal event (last position)
                    conn.execute(
                        """UPDATE nodes
                           SET chain_id=?, node_type='event', "order"=?,
                               version=version+1
                           WHERE node_id=?""",
                        (chain_id, new_order, merge_id),
                    )
                    # Ensure attachments get branch-folder derived paths
                    _sync_node_derived_paths(conn, merge_id)

            row = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            end = conn.execute(
                'SELECT 1 FROM nodes WHERE chain_id=? AND node_type="end" LIMIT 1',
                (chain_id,),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM nodes WHERE chain_id=?",
                (chain_id,),
            ).fetchone()

        emit_event(
            "chain.reopened",
            collection_id,
            {
                "chain_id": chain_id,
                "restored_path_archives": restored_paths,
                "restored_file_archives": restored_files,
            },
        )
        return _row_to_chain(row, has_end_node=end is not None, node_count=count["c"])
    finally:
        conn.close()


# === Node CRUD ===


def list_nodes(collection_id: str, chain_id: str) -> list[NodeOut]:
    conn = _open_db(collection_id)
    try:
        ch = conn.execute(
            "SELECT chain_id FROM chains WHERE chain_id=?", (chain_id,)
        ).fetchone()
        if not ch:
            raise HTTPException(404, f"Chain '{chain_id}' not found")

        rows = conn.execute(
            'SELECT * FROM nodes WHERE chain_id=? ORDER BY "order"',
            (chain_id,),
        ).fetchall()
        return [_row_to_node(r) for r in rows]
    finally:
        conn.close()


def create_node(
    collection_id: str, chain_id: str, req: NodeCreate
) -> NodeOut:
    # System end markers may omit title; user event nodes must have a name
    if req.node_type == "event":
        if not req.title or not str(req.title).strip():
            raise HTTPException(400, "Node title is required")
    conn = _open_db(collection_id)
    try:
        with conn:
            ch = conn.execute(
                "SELECT chain_id FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            if not ch:
                raise HTTPException(404, f"Chain '{chain_id}' not found")

            if req.group_id:
                grp = conn.execute(
                    "SELECT group_id FROM node_groups WHERE group_id=?",
                    (req.group_id,),
                ).fetchone()
                if not grp:
                    raise HTTPException(
                        404, f"Group '{req.group_id}' not found"
                    )

            max_row = conn.execute(
                'SELECT COALESCE(MAX("order"), 0) AS m FROM nodes WHERE chain_id=?',
                (chain_id,),
            ).fetchone()
            max_order = max_row["m"]

            order = max(1, min(req.order, max_order + 1))
            if order <= max_order:
                conn.execute(
                    'UPDATE nodes SET "order" = "order" + 1 '
                    'WHERE chain_id=? AND "order" >= ?',
                    (chain_id, order),
                )

            node_id = uuid.uuid4().hex
            now = _now_iso()
            conn.execute(
                """INSERT INTO nodes
                   (node_id, chain_id, group_id, node_type, title,
                    "order", event_time, created_by, created_at, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'local', ?, 1)""",
                (node_id, chain_id, req.group_id, req.node_type,
                 req.title, order, req.event_time, now),
            )
            row = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()

        emit_event("node.created", collection_id, {"node_id": node_id})
        return _row_to_node(row)
    finally:
        conn.close()


def update_node(
    collection_id: str, node_id: str, req: NodeUpdate
) -> NodeOut:
    updates = req.model_dump(exclude_unset=True)

    conn = _open_db(collection_id)
    try:
        with conn:
            node = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{node_id}' not found")

            if "group_id" in updates and updates["group_id"] is not None:
                grp = conn.execute(
                    "SELECT group_id FROM node_groups WHERE group_id=?",
                    (updates["group_id"],),
                ).fetchone()
                if not grp:
                    raise HTTPException(
                        404, f"Group '{updates['group_id']}' not found"
                    )

            set_clauses: list[str] = []
            params: list = []

            for field in ("title", "group_id", "order", "event_time", "node_type", "chain_id"):
                if field in updates:
                    set_clauses.append(f'"{field}" = ?')
                    params.append(updates[field])

            set_clauses.append("version = version + 1")
            params.extend([node_id, req.version])

            cursor = conn.execute(
                f"UPDATE nodes SET {', '.join(set_clauses)} "
                "WHERE node_id = ? AND version = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise HTTPException(
                    409, "Node was modified by another user (version conflict)"
                )

            # group_id / chain_id change → remount derived folder paths
            if "group_id" in updates or "chain_id" in updates:
                _sync_node_derived_paths(conn, node_id)

            row = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()

        emit_event("node.updated", collection_id, {"node_id": node_id})
        return _row_to_node(row)
    finally:
        conn.close()


def delete_node(collection_id: str, node_id: str) -> dict | None:
    """Delete a node and handle associated files.

    - If type 'end': just delete the node.
    - For other nodes:
      a. Remove node's derived file_paths (source_node_id = node_id)
      b. For each file attachment:
         - If file has other node attachments OR persistent paths → just delete this file_nodes row
         - If this is the file's only attachment and no persistent paths → affected_files candidate
    - Delete affected node messages.

    Returns None for simple deletes, or dict with affected_files for the UI to handle.
    """
    conn = _open_db(collection_id)
    try:
        affected_files: list[dict] = []
        result: dict | None = None

        with conn:
            node = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{node_id}' not found")

            # Get all file attachments BEFORE deleting anything
            fn_rows = conn.execute(
                "SELECT file_id FROM file_nodes WHERE node_id=?", (node_id,)
            ).fetchall()
            attached_file_ids = [fn["file_id"] for fn in fn_rows]

            # Check each attached file's state
            for fid in attached_file_ids:
                # Does this file have other node attachments?
                other_attachments = conn.execute(
                    "SELECT 1 FROM file_nodes WHERE file_id=? AND node_id!=? LIMIT 1",
                    (fid, node_id),
                ).fetchone()

                # Does this file have persistent paths (source_node_id=NULL)?
                persistent = conn.execute(
                    "SELECT 1 FROM file_paths WHERE file_id=? AND source_node_id IS NULL LIMIT 1",
                    (fid,),
                ).fetchone()

                if other_attachments or persistent:
                    # File survives — just remove this node's derived paths
                    conn.execute(
                        "DELETE FROM file_paths WHERE file_id=? AND source_node_id=?",
                        (fid, node_id),
                    )
                else:
                    # This file has NO other references → affected
                    file_info = conn.execute(
                        "SELECT * FROM files WHERE file_id=?", (fid,)
                    ).fetchone()
                    if file_info:
                        ver = conn.execute(
                            "SELECT storage_file_id FROM file_versions WHERE version_id=?",
                            (file_info["current_version_id"],),
                        ).fetchone()
                        affected_files.append({
                            "file_id": fid,
                            "filename": ver["storage_file_id"] if ver else "",
                            "has_only_this_node": True,
                        })

            # Delete file_nodes for this node
            conn.execute(
                "DELETE FROM file_nodes WHERE node_id=?", (node_id,)
            )

            # Delete derived paths for this node (cleanup any remaining)
            conn.execute(
                "DELETE FROM file_paths WHERE source_node_id=?", (node_id,)
            )

            # Delete node's messages (from message flow)
            conn.execute(
                "DELETE FROM messages WHERE owner_type='node' AND owner_id=?",
                (node_id,),
            )

            # Break inbound FKs that still point at this node:
            # - messages.source_node_id
            # - chains.merge_node_id (reopens closed branches that merged here)
            _clear_node_inbound_fks(conn, node_id)

            # Branches that start at this node cannot keep a dangling parent_node_id —
            # remove those branch chains (and their merge node on main, if any).
            start_branches = conn.execute(
                "SELECT chain_id FROM chains WHERE parent_node_id=?",
                (node_id,),
            ).fetchall()
            for br in start_branches:
                # Revert is handled by deleting the branch; parent type reset below
                # is skipped because parent is the node we're deleting.
                _delete_chain_subtree(conn, br["chain_id"])

            # Delete the node itself
            conn.execute(
                "DELETE FROM nodes WHERE node_id=?", (node_id,)
            )

            # If node was on a branch chain and it is now empty, clean up the branch
            chain_id = node["chain_id"]
            chain_row = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            if chain_row and chain_row["parent_chain_id"] is not None:
                # This is a branch chain (not main)
                remaining = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM nodes WHERE chain_id=?", (chain_id,)
                ).fetchone()["cnt"]
                if remaining == 0:
                    # No nodes left — revert parent node type and delete chain
                    parent_node_id = chain_row["parent_node_id"]
                    if parent_node_id:
                        conn.execute(
                            "UPDATE nodes SET node_type=?, version=version+1 WHERE node_id=? AND node_type=?",
                            ("event", parent_node_id, "start"),
                        )
                    _delete_chain_subtree(conn, chain_id)

            if affected_files:
                result = {"affected_files": affected_files}

        emit_event("node.deleted", collection_id, {"node_id": node_id})
        return result
    finally:
        conn.close()


def reorder_node(
    collection_id: str, node_id: str, req: NodeReorder
) -> list[NodeOut]:
    conn = _open_db(collection_id)
    try:
        with conn:
            node = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{node_id}' not found")

            chain_id = node["chain_id"]
            old_order = node["order"]

            max_row = conn.execute(
                'SELECT COALESCE(MAX("order"), 0) AS m FROM nodes WHERE chain_id=?',
                (chain_id,),
            ).fetchone()
            new_order = max(1, min(req.new_order, max_row["m"]))

            if new_order != old_order:
                if new_order < old_order:
                    conn.execute(
                        'UPDATE nodes SET "order" = "order" + 1 '
                        'WHERE chain_id=? AND "order" >= ? AND "order" < ?',
                        (chain_id, new_order, old_order),
                    )
                else:
                    conn.execute(
                        'UPDATE nodes SET "order" = "order" - 1 '
                        'WHERE chain_id=? AND "order" > ? AND "order" <= ?',
                        (chain_id, old_order, new_order),
                    )
                conn.execute(
                    'UPDATE nodes SET "order"=? WHERE node_id=?',
                    (new_order, node_id),
                )

            rows = conn.execute(
                'SELECT * FROM nodes WHERE chain_id=? ORDER BY "order"',
                (chain_id,),
            ).fetchall()

        emit_event("node.reordered", collection_id, {"node_id": node_id})
        return [_row_to_node(r) for r in rows]
    finally:
        conn.close()


def get_node_detail(collection_id: str, node_id: str) -> dict:
    conn = _open_db(collection_id)
    try:
        node = conn.execute(
            "SELECT * FROM nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        if not node:
            raise HTTPException(404, f"Node '{node_id}' not found")

        # Self-heal: ensure attachments mount group + branch (incl. start/merge anchors)
        try:
            with conn:
                _sync_node_derived_paths(conn, node_id)
        except Exception:
            logger.exception(
                "Failed to sync derived paths for node %s", node_id
            )

        out = _row_to_node(node)

        # Attachments: file_nodes JOIN files JOIN file_versions
        _ensure_path_archive_column(conn)
        att_rows = conn.execute(
            """SELECT fn.file_id, f.is_definitive, f.archived, f.version,
                      fv.storage_file_id
               FROM file_nodes fn
               JOIN files f ON f.file_id = fn.file_id
               LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
               WHERE fn.node_id=?""",
            (node_id,),
        ).fetchall()
        attachments = []
        has_definitive = False
        for a in att_rows:
            # archived for UI = file-level OR any path-level archive on this file
            file_archived = bool(a["archived"])
            path_archived = False
            if not file_archived:
                pr = conn.execute(
                    """SELECT 1 FROM file_paths
                       WHERE file_id=? AND COALESCE(archived, 0)=1 LIMIT 1""",
                    (a["file_id"],),
                ).fetchone()
                path_archived = pr is not None
            attachments.append({
                "file_id": a["file_id"],
                "is_definitive": bool(a["is_definitive"]),
                "archived": file_archived or path_archived,
                "filename": a["storage_file_id"] or "",
                "version": int(a["version"] or 1),
            })
            if a["is_definitive"]:
                has_definitive = True

        # Node messages
        msg_rows = conn.execute(
            """SELECT * FROM messages
               WHERE owner_type='node' AND owner_id=?
               ORDER BY created_at DESC""",
            (node_id,),
        ).fetchall()
        node_msgs = [_row_to_message(r, conn) for r in msg_rows]

        return {
            **out.model_dump(),
            "attachments": attachments,
            "messages": [m.model_dump() for m in node_msgs],
            "has_definitive_file": has_definitive,
        }
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# Phase 3: File Paths + Attachments + Messages
# ════════════════════════════════════════════════════════════════════

# --- Helpers ---


def _message_source_name(conn, owner_type: str, owner_id: str) -> str | None:
    """Resolve a short display name for a message owner (folder/file/node/…)."""
    ot = (owner_type or "").strip().lower()
    if not owner_id:
        return None
    try:
        if ot == "folder":
            r = conn.execute(
                "SELECT name FROM folders WHERE folder_id=?", (owner_id,)
            ).fetchone()
            return (r["name"] if r else None) or None
        if ot == "file":
            r = conn.execute(
                """SELECT fv.storage_file_id AS filename
                   FROM files f
                   LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
                   WHERE f.file_id=?""",
                (owner_id,),
            ).fetchone()
            if not r or not r["filename"]:
                return None
            return Path(r["filename"]).name
        if ot == "node":
            r = conn.execute(
                "SELECT title FROM nodes WHERE node_id=?", (owner_id,)
            ).fetchone()
            if not r:
                return None
            t = (r["title"] or "").strip()
            return t or "Untitled"
        if ot == "collection":
            return "Root"
        if ot == "system_version":
            # Prefer current display name of the file this version log belongs to
            r = conn.execute(
                """SELECT fv.storage_file_id AS filename
                   FROM files f
                   LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
                   WHERE f.file_id=?""",
                (owner_id,),
            ).fetchone()
            if r and r["filename"]:
                return Path(r["filename"]).name
            return "Version update"
    except Exception:
        return None
    return None


def _row_to_message(row, conn=None) -> MessageOut:
    from src.file_mgmt.models import MessageOut

    source_name = None
    if conn is not None:
        try:
            source_name = _message_source_name(
                conn, row["owner_type"], row["owner_id"]
            )
        except Exception:
            source_name = None

    return MessageOut(
        message_id=row["message_id"],
        owner_type=row["owner_type"],
        owner_id=row["owner_id"],
        source_node_id=row["source_node_id"],
        body=row["body"],
        author_type=row["author_type"],
        author_id=row["author_id"],
        created_at=row["created_at"],
        edited_at=row["edited_at"],
        edited_by=row["edited_by"],
        version=row["version"],
        source_name=source_name,
    )


def _load_file_index(collection_id: str | None) -> dict[str, dict]:
    """Load files.json index for *collection_id* (empty dict on miss/error)."""
    if not collection_id:
        return {}
    try:
        from src.collections.file_index import load as load_file_index
        return load_file_index(collection_id) or {}
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
        is_greyed=bool(row["archived"]),
    )
    # compute filename from current version
    if conn and f.current_version_id:
        ver = conn.execute(
            "SELECT storage_file_id, created_at FROM file_versions WHERE version_id=?",
            (f.current_version_id,),
        ).fetchone()
        if ver:
            f.filename = ver["storage_file_id"]
            from pathlib import Path
            f.original_ext = Path(ver["storage_file_id"]).suffix.lstrip(".") if Path(ver["storage_file_id"]).suffix else ""
            f.created_at = ver["created_at"]

    # Prefer preloaded index (list endpoints); else load single entry
    if index is not None:
        entry = index.get(file_id)
    else:
        entry = _load_file_index(collection_id).get(file_id) if collection_id else None
    label, src = _index_entry_display(entry)
    f.source = src or f"__file__:{file_id}"
    f.display_name = label or f.filename or file_id
    f.doc_kind = _doc_kind_from_source(f.source)
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
        versions = [_row_to_file_version(r) for r in ver_rows]

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
                   VALUES (?, ?, ?, ?, NULL, 'local')""",
                (path_id, file_id, folder_id, 1 if is_primary else 0),
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
) -> None:
    """Idempotently place an ingested note/meeting snapshot into file-mgmt.

    - Ensures a row under the system folder (Notes / Meeting).
    - Drops older SQLite rows for the same ``source`` (re-ingest).
    Safe to call after files.json has been updated.
    """
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

            # Purge older file_ids for the same source (re-ingest)
            try:
                from src.collections.file_index import load as load_file_index

                idx = load_file_index(collection_id) or {}
                for fid, entry in list(idx.items()):
                    if (
                        entry.get("source") == source
                        and fid != file_id
                        and conn.execute(
                            "SELECT 1 FROM files WHERE file_id=?", (fid,)
                        ).fetchone()
                    ):
                        _purge_file_sqlite_rows(conn, fid)
                        logger.info(
                            "Purged stale file-mgmt row %s for source %s",
                            fid,
                            source,
                        )
            except Exception:
                logger.debug(
                    "stale source purge skipped for %s", source, exc_info=True
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
                           VALUES (?, ?, 1, ?, 0, NULL, 'local', ?)""",
                        (version_id, file_id, name, now),
                    )
                    conn.execute(
                        "UPDATE files SET current_version_id=?, unsupported=0 "
                        "WHERE file_id=?",
                        (version_id, file_id),
                    )
                conn.execute(
                    "UPDATE files SET unsupported=0 WHERE file_id=?", (file_id,)
                )
                has_path = conn.execute(
                    "SELECT 1 FROM file_paths WHERE file_id=? AND folder_id=? LIMIT 1",
                    (file_id, folder_id),
                ).fetchone()
                if not has_path:
                    conn.execute(
                        """INSERT INTO file_paths
                           (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                           VALUES (?, ?, ?, 1, NULL, 'local')""",
                        (uuid.uuid4().hex, file_id, folder_id),
                    )
            else:
                version_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO files
                       (file_id, current_version_id, is_definitive, archived,
                        unsupported, created_by, version)
                       VALUES (?, NULL, 0, 0, 0, 'local', 1)""",
                    (file_id,),
                )
                conn.execute(
                    """INSERT INTO file_versions
                       (version_id, file_id, version_no, storage_file_id,
                        archived, commit_message, created_by, created_at)
                       VALUES (?, ?, 1, ?, 0, NULL, 'local', ?)""",
                    (version_id, file_id, name, now),
                )
                conn.execute(
                    "UPDATE files SET current_version_id=? WHERE file_id=?",
                    (version_id, file_id),
                )
                conn.execute(
                    """INSERT INTO file_paths
                       (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                       VALUES (?, ?, ?, 1, NULL, 'local')""",
                    (uuid.uuid4().hex, file_id, folder_id),
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

    # Fallback: if index missing/stale, still try note_id suffix as file lookup
    # is not possible from SQLite alone — scan files dir for leftover nothing.
    # (source is only stored in files.json / Qdrant payloads.)

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
            "unregister_files_for_source: no files.json entries for %s in %s",
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


def _ingest_file_to_qdrant(
    collection_id: str,
    file_id: str,
    file_path: Path,
    version_id: str,
    *,
    source_label: str = "",
) -> int:
    """Ingest a parsed file into Qdrant. Returns chunk count.

    Reuses existing parsing + chunking + embedding logic from handlers.py.
    Qdrant payload includes archived/is_current/version_id/created_by.
    """
    from src.services import services

    if services.db is None or services.embedding is None:
        logger.warning(
            "Qdrant/Embedding not available, skipping ingest for %s/%s",
            collection_id, file_id,
        )
        return 0

    from src.parsers import parse_file
    from src.rag.chunker import ParagraphChunker
    from src.rag.markdown_chunker import MarkdownChunker
    from src.rag.collection_utils import get_collection_embedding

    doc = parse_file(file_path)
    if not doc.content or not doc.content.strip():
        raise ValueError(f"No extractable text found for '{file_path.name}'")

    config = services.db.get_collection_config(collection_id)
    filename = file_path.name

    # Choose chunker
    use_markdown = doc.file_type == "markdown" or bool(doc.images)
    if use_markdown:
        chunker = MarkdownChunker(
            max_tokens=config.get("chunk_size", 512),
            buffer_ratio=config.get("buffer_ratio", 0.5),
            chunk_overlap=config.get("chunk_overlap", 64),
        )
    else:
        chunker = ParagraphChunker(
            max_tokens=config.get("chunk_size", 512),
            buffer_ratio=config.get("buffer_ratio", 0.5),
            chunk_overlap=config.get("chunk_overlap", 64),
        )

    source = f"__file__:{file_id}"
    extra_meta = {
        "file_type": doc.file_type,
        "ingested_at": __import__("time").time(),
        "file_id": file_id,
        "archived": False,
        "version_id": version_id,
        "is_current": True,
        "created_by": "local",
        "source_label": source_label or filename,
    }
    chunks = chunker.chunk_with_metadata(
        doc.content, source=source, extra_metadata=extra_meta
    )

    if not chunks:
        raise ValueError(f"Chunking produced no results for '{filename}'")

    # Embed
    embedding = get_collection_embedding(config, collection_id)
    texts = []
    for c in chunks:
        parts = []
        s = c.metadata.get("source", "")
        if s:
            parts.append(f"Source: {Path(s).name}")
        summary = c.metadata.get("summary", "")
        if summary:
            parts.append(f"Document: {summary}")
        context = c.metadata.get("context", "")
        if context:
            parts.append(f"Context: {context}")
        parts.append(c.text)
        texts.append("\n".join(parts))
    embeddings = embedding.embed_texts(texts)

    # Upsert
    ids = []
    payloads = []
    for c in chunks:
        cid = c.metadata.get("chunk_id") or str(uuid.uuid4())
        c.metadata["chunk_id"] = cid
        ids.append(cid)
        payload = {
            "text": c.text,
            "parent_id": c.parent_id,
            "chunk_type": c.chunk_type,
            "collection": collection_id,
        }
        if c.metadata.get("context"):
            payload["context"] = c.metadata["context"]
        if c.metadata.get("summary"):
            payload["summary"] = c.metadata["summary"]
        payload.update({k: v for k, v in c.metadata.items() if k not in ("context", "summary")})
        payloads.append(payload)

    services.db.upsert_points(
        collection=collection_id, ids=ids, vectors=embeddings, payloads=payloads,
    )

    # Update file index
    try:
        from src.collections.file_index import add as add_file_index
        add_file_index(
            collection_id, file_id, source,
            source_label or filename,
            doc.file_type, len(chunks),
            file_path.suffix.lower().lstrip("."),
        )
    except Exception:
        logger.warning("Failed to update files.json for %s", file_id, exc_info=True)

    return len(chunks)


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
                   VALUES (?, NULL, 0, 0, ?, 'local', 1)""",
                (file_id, unsupported),
            )

            # 5. Create file_versions (now files row exists)
            conn.execute(
                """INSERT INTO file_versions
                   (version_id, file_id, version_no, storage_file_id,
                    archived, commit_message, created_by, created_at)
                   VALUES (?, ?, 1, ?, 0, NULL, 'local', ?)""",
                (version_id, file_id, safe_name, now),
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
                       VALUES (?, ?, ?, ?, ?, 'local')""",
                    (path_id, file_id, folder_id, is_primary, source_node_id),
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
                    logger.warning(
                        "Failed to queue ingest task for file %s (%s): %s",
                        file_id, safe_name, e,
                    )
                    err_msg_id = uuid.uuid4().hex
                    conn.execute(
                        """INSERT INTO messages
                           (message_id, owner_type, owner_id, source_node_id, body,
                            author_type, author_id, created_at, edited_at, edited_by, version)
                           VALUES (?, 'system_version', ?, NULL, ?,
                            'system', 'system', ?, NULL, NULL, 1)""",
                        (err_msg_id, file_id, f"Failed to queue ingest: {e}", now),
                    )

            # 8. Create system version message
            message_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO messages
                   (message_id, owner_type, owner_id, source_node_id, body,
                    author_type, author_id, created_at, edited_at, edited_by, version)
                   VALUES (?, 'system_version', ?, NULL, 'Initial upload',
                    'system', 'local', ?, NULL, NULL, 1)""",
                (message_id, file_id, now),
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
    files_data: list[tuple[bytes, str]],
) -> list[FileSummary]:
    """Upload an entire folder preserving relative paths.

    Args:
        parent_folder_id: destination folder, or empty/None for collection root
        files_data: list of (bytes_content, relative_filename) tuples
    """
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
                               VALUES (?, ?, ?, 'plain', 0, 'local', ?, ?, 1)""",
                            (fid, current_parent, part, now, now),
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
                   VALUES (?, ?, ?, ?, 0, ?, 'local', ?)""",
                (new_version_id, file_id, new_version_no, safe_name, commit_body, now),
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

            # 5. Create system version message (editable note; default body)
            message_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO messages
                   (message_id, owner_type, owner_id, source_node_id, body,
                    author_type, author_id, created_at, edited_at, edited_by, version)
                   VALUES (?, 'system_version', ?, NULL, ?,
                    'system', 'local', ?, NULL, NULL, 1)""",
                (message_id, file_id, commit_body, now),
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
                    logger.warning(
                        "Failed to queue version ingest for file %s (%s): %s",
                        file_id,
                        safe_name,
                        e,
                    )
                    err_msg_id = uuid.uuid4().hex
                    conn.execute(
                        """INSERT INTO messages
                           (message_id, owner_type, owner_id, source_node_id, body,
                            author_type, author_id, created_at, edited_at, edited_by, version)
                           VALUES (?, 'system_version', ?, NULL, ?,
                            'system', 'system', ?, NULL, NULL, 1)""",
                        (
                            err_msg_id,
                            file_id,
                            f"Failed to queue ingest: {e}",
                            now,
                        ),
                    )

            # 6b. Always refresh files.json so display_name / original_ext match
            # the new version immediately — including unsupported types that skip
            # the upload task (which would otherwise leave the old label forever).
            try:
                from src.collections.file_index import add as add_file_index

                orig_ext = Path(safe_name).suffix.lower().lstrip(".") or None
                idx_type = (
                    "note"
                    if file_source.startswith("__note__:")
                    else "meeting"
                    if file_source.startswith("__meeting__:")
                    else ("unsupported" if unsupported else file_type or "file")
                )
                add_file_index(
                    collection_id,
                    file_id,
                    file_source,
                    label,
                    idx_type,
                    0,  # chunk count; ingest overwrites when task completes
                    orig_ext,
                )
            except Exception:
                logger.warning(
                    "Failed to update files.json after version upload for %s",
                    file_id,
                    exc_info=True,
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

            storage_name = ver["storage_file_id"] or ""
            ver_created = ver["created_at"] or ""
            commit_body = (ver["commit_message"] or "").strip()

            # 1. Disk: remove this version's directory (or legacy flat blob)
            from src.file_mgmt.storage_paths import delete_version_storage

            delete_version_storage(
                collection_id, file_id, version_id, storage_name
            )

            # 2. Qdrant: points tagged with this version_id
            _delete_qdrant_chunks_by_version_id(collection_id, version_id)

            # 3. Paired system_version message (same file, same created_at when possible)
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

            # 4. Version row
            conn.execute(
                "DELETE FROM file_versions WHERE version_id=?", (version_id,)
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
                # Must be free in every folder this file is mounted in
                mounts = conn.execute(
                    "SELECT DISTINCT folder_id FROM file_paths WHERE file_id=?",
                    (file_id,),
                ).fetchall()
                for m in mounts:
                    _assert_file_name_free(
                        conn,
                        m["folder_id"],
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


def end_chain(collection_id: str, node_id: str, req: EndChainRequest) -> dict:
    """End a branch chain and close the loop on the parent chain.

    Steps:
    1. Validate end-type node on a branch chain
    2. Path-archive non-inherited files on the **branch folder** only
    3. Promote to file-level archive when no active paths remain
    4. Create merge node on parent with form fields; attach files + message
    5. Link chains.merge_node_id → merge node
    6. Persist merge archive snapshot on the chain (for reopen undo)
    """
    import json

    from src.file_mgmt.store import (
        _ensure_chains_merge_archive_json,
        _ensure_chains_merge_node_id,
    )

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_chains_merge_node_id(conn)
            _ensure_chains_merge_archive_json(conn)
            _ensure_path_archive_column(conn)

            # 1. Validate node
            node = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{node_id}' not found")
            if node["node_type"] != "end":
                raise HTTPException(400, "Node is not an 'end' type node")

            chain_id = node["chain_id"]
            chain = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            if not chain:
                raise HTTPException(500, "Node's chain not found")
            parent_chain_id = chain["parent_chain_id"]
            if parent_chain_id is None:
                raise HTTPException(400, "Cannot end the main chain")

            # Already closed (merge pointer set) → must reopen first
            try:
                existing_merge = chain["merge_node_id"]
            except (KeyError, IndexError):
                existing_merge = None
            if existing_merge:
                raise HTTPException(
                    409,
                    "This chain is already closed. "
                    "Use reopen_chain first to re-open it.",
                )

            # Branch may have leftover end markers (cancel-after-prepare, or
            # stale double-create). Keep the end node used for this call;
            # purge any other end-type placeholders on this branch.
            other_ends = conn.execute(
                'SELECT node_id FROM nodes WHERE chain_id=? AND node_type="end" '
                "AND node_id != ?",
                (chain_id, node_id),
            ).fetchall()
            for er in other_ends:
                _purge_node_owned_rows(conn, er["node_id"])
            if other_ends:
                conn.execute(
                    'DELETE FROM nodes WHERE chain_id=? AND node_type="end" '
                    "AND node_id != ?",
                    (chain_id, node_id),
                )

            all_nodes = conn.execute(
                'SELECT * FROM nodes WHERE chain_id=? ORDER BY "order"',
                (chain_id,),
            ).fetchall()
            all_node_ids = {n["node_id"] for n in all_nodes}

            # Collect all files on branch nodes
            branch_files: set[str] = set()
            files_by_node: dict[str, set[str]] = {}
            for n_id in all_node_ids:
                fn_rows = conn.execute(
                    "SELECT file_id FROM file_nodes WHERE node_id=?",
                    (n_id,),
                ).fetchall()
                files_by_node[n_id] = {fn["file_id"] for fn in fn_rows}
                branch_files |= files_by_node[n_id]

            # Resolve inherit set (prefer file ids; fall back to node ids)
            if req.inherit_file_ids:
                inherit_files = set(req.inherit_file_ids)
                for fid in inherit_files:
                    if fid not in branch_files:
                        raise HTTPException(
                            400,
                            f"inherit_file_id '{fid}' is not attached on this chain",
                        )
            elif req.inherit_node_ids:
                inherit_files = set()
                for inh_id in req.inherit_node_ids:
                    if inh_id not in all_node_ids:
                        raise HTTPException(
                            400, f"inherit_node_id '{inh_id}' is not on this chain"
                        )
                    inherit_files |= files_by_node.get(inh_id, set())
            else:
                # Default: inherit nothing if client sends empty (explicit)
                inherit_files = set()

            non_inherited = branch_files - inherit_files
            branch_folder_id = chain["folder_id"]

            path_archived: list[str] = []
            path_ids_archived: list[str] = []
            file_archived: list[str] = []

            for fid in non_inherited:
                if branch_folder_id:
                    pids = _archive_paths_on_folder(conn, fid, branch_folder_id)
                    path_ids_archived.extend(pids)
                    if pids:
                        path_archived.append(fid)
                if _promote_file_archive_if_needed(conn, collection_id, fid):
                    file_archived.append(fid)

            # Snapshot for reopen — only what this merge archived
            merge_archive_payload = json.dumps(
                {
                    "path_ids": list(path_ids_archived),
                    "file_ids": list(file_archived),
                },
                separators=(",", ":"),
            )

            # 3. Create merge node on parent chain — title is required
            if not req.title or not str(req.title).strip():
                raise HTTPException(400, "Merge node title is required")
            merge_title = str(req.title).strip()
            merge_group_id = req.group_id
            if merge_group_id:
                g = conn.execute(
                    "SELECT group_id FROM node_groups WHERE group_id=?",
                    (merge_group_id,),
                ).fetchone()
                if not g:
                    raise HTTPException(400, f"Group '{merge_group_id}' not found")

            max_row = conn.execute(
                'SELECT COALESCE(MAX("order"), 0) AS m FROM nodes WHERE chain_id=?',
                (parent_chain_id,),
            ).fetchone()
            merge_order = int(max_row["m"]) + 1
            merge_node_id = uuid.uuid4().hex
            now = _now_iso()
            conn.execute(
                """INSERT INTO nodes
                   (node_id, chain_id, group_id, node_type, title,
                    "order", event_time, created_by, created_at, version)
                   VALUES (?, ?, ?, 'end', ?, ?, ?, 'local', ?, 1)""",
                (
                    merge_node_id,
                    parent_chain_id,
                    merge_group_id,
                    merge_title,
                    merge_order,
                    req.event_time,
                    now,
                ),
            )
            conn.execute(
                """UPDATE chains
                   SET merge_node_id=?, merge_archive_json=?
                   WHERE chain_id=?""",
                (merge_node_id, merge_archive_payload, chain_id),
            )

            # Attach files to merge node (reuse attach helper pieces)
            for fid in req.attachment_file_ids:
                fr = conn.execute(
                    "SELECT * FROM files WHERE file_id=?", (fid,)
                ).fetchone()
                if not fr:
                    raise HTTPException(404, f"Attachment file '{fid}' not found")
                existing_fn = conn.execute(
                    "SELECT 1 FROM file_nodes WHERE file_id=? AND node_id=?",
                    (fid, merge_node_id),
                ).fetchone()
                if existing_fn:
                    continue
                conn.execute(
                    """INSERT INTO file_nodes
                       (file_id, node_id, version_id, greyed, added_by)
                       VALUES (?, ?, ?, 0, 'local')""",
                    (fid, merge_node_id, fr["current_version_id"]),
                )

            # Mount group + branch folder on merge (and heal all branch nodes)
            _sync_node_derived_paths(conn, merge_node_id)
            _sync_chain_branch_paths(conn, chain_id)

            if req.message_body and req.message_body.strip():
                msg_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO messages
                       (message_id, owner_type, owner_id, source_node_id, body,
                        author_type, author_id, created_at, edited_at, edited_by, version)
                       VALUES (?, 'node', ?, NULL, ?, 'user', 'local', ?, NULL, NULL, 1)""",
                    (msg_id, merge_node_id, req.message_body.strip(), now),
                )

            # Branch end marker is only a dialog placeholder — the real close
            # lives on the parent as merge_node. Remove all branch end-type
            # nodes so reopen/re-merge never sees stale duplicates.
            branch_ends = conn.execute(
                'SELECT node_id FROM nodes WHERE chain_id=? AND node_type="end"',
                (chain_id,),
            ).fetchall()
            for er in branch_ends:
                _purge_node_owned_rows(conn, er["node_id"])
            if branch_ends:
                conn.execute(
                    'DELETE FROM nodes WHERE chain_id=? AND node_type="end"',
                    (chain_id,),
                )

            result = {
                # greyed_files kept as alias of path_archived for older clients
                "greyed_files": list(path_archived),
                "archive_candidates": [],
                "path_archived_files": path_archived,
                "path_archived_path_ids": path_ids_archived,
                "file_archived": file_archived,
                "inherited_files": list(inherit_files),
                "merged_node_id": merge_node_id,
            }

        emit_event(
            "archive.toggled",
            collection_id,
            {"node_id": node_id, "chain_id": chain_id, **result},
        )
        return result
    finally:
        conn.close()


def toggle_archive(
    collection_id: str, file_id: str, req: ArchiveToggle
) -> FileSummary:
    """Archive or unarchive — two layers only: path + file (no attachment greyed).

    Archive (archived=True):
      scope=file → exclude from search (files.archived=1)
      scope=path → path-archive in folder_id; auto file-level if no active paths

    Unarchive (archived=False):
      Always clear file-level when set + clear paths in folder_id when given.
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
                    if not folder_id:
                        raise HTTPException(
                            400, "folder_id is required for path-level archive"
                        )
                    fld = conn.execute(
                        "SELECT folder_id FROM folders WHERE folder_id=?",
                        (folder_id,),
                    ).fetchone()
                    if not fld:
                        raise HTTPException(404, f"Folder '{folder_id}' not found")
                    path_ids_touched = _archive_paths_on_folder(
                        conn, file_id, folder_id
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
                            400, "Already archived for this folder"
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
                # - with folder_id: clear file-level + this folder's path archives
                # - without folder_id (e.g. /Archived): clear file-level ONLY
                #   (path archives stay; restore those in each folder)
                if folder_id:
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
                            " and has no path archives in this folder"
                            if folder_id
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
    if file_id is None and upload_file is None:
        raise HTTPException(400, "Either file_id or upload_file must be provided")
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
                    (node["group_id"],),
                ).fetchone()
                if grp and grp["folder_id"]:
                    target_folder_id = grp["folder_id"]
            if not target_folder_id:
                raise HTTPException(
                    400,
                    "Node has no group — cannot auto-determine upload folder. "
                    "Upload to a folder first, then attach with file_id.",
                )
        finally:
            conn2.close()

        # Upload to the group folder with source_node_id
        result = upload_file_to_folder(
            collection_id, target_folder_id, file_bytes, upload_filename, source_node_id=node_id
        )
        file_id = result.file_id

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
                       VALUES (?, ?, ?, 0, 'local')""",
                    (file_id, node_id, file_row["current_version_id"]),
                )

            # Always sync derived paths (group + branch folder) — covers re-attach
            # and cases where chain folder was missing on first attach.
            _sync_node_derived_paths(conn, node_id, file_id=file_id)

        emit_event(
            "file.uploaded",
            collection_id,
            {"file_id": file_id, "node_id": node_id},
        )
        return _row_to_file_out(file_row, conn, collection_id)
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
           VALUES (?, ?, ?, 0, ?, 'local')""",
        (path_id, file_id, folder_id, source_node_id),
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
    finally:
        conn.close()


# --- Message CRUD ---


def _row_to_file_version(row) -> FileVersionOut:
    from src.file_mgmt.models import FileVersionOut

    return FileVersionOut(
        version_id=row["version_id"],
        file_id=row["file_id"],
        version_no=row["version_no"],
        storage_file_id=row["storage_file_id"],
        archived=bool(row["archived"]),
        commit_message=row["commit_message"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def create_message(collection_id: str, req: MessageCreate) -> MessageOut:
    from src.file_mgmt.models import MessageCreate, MessageOut

    conn = _open_db(collection_id)
    try:
        with conn:
            now = _now_iso()
            message_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO messages
                   (message_id, owner_type, owner_id, source_node_id, body,
                    author_type, author_id, created_at, edited_at, edited_by, version)
                   VALUES (?, ?, ?, ?, ?, 'user', 'local', ?, NULL, NULL, 1)""",
                (
                    message_id,
                    req.owner_type,
                    req.owner_id,
                    req.source_node_id,
                    req.body,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM messages WHERE message_id=?", (message_id,)
            ).fetchone()

        emit_event(
            "message.created",
            collection_id,
            {"message_id": message_id, "owner_type": req.owner_type, "owner_id": req.owner_id},
        )
        return _row_to_message(row, conn)
    finally:
        conn.close()


def update_message(collection_id: str, message_id: str, req: MessageUpdate) -> MessageOut:
    from src.file_mgmt.models import MessageUpdate, MessageOut

    conn = _open_db(collection_id)
    try:
        with conn:
            msg = conn.execute(
                "SELECT * FROM messages WHERE message_id=?", (message_id,)
            ).fetchone()
            if not msg:
                raise HTTPException(404, f"Message '{message_id}' not found")

            # system_version notes (file version updates) are editable;
            # other system messages stay locked.
            owner_type = (msg["owner_type"] or "").lower()
            if msg["author_type"] == "system" and owner_type != "system_version":
                raise HTTPException(403, "System messages cannot be edited")

            now = _now_iso()
            body = (req.body or "").strip() or (
                "version update" if owner_type == "system_version" else req.body
            )
            cursor = conn.execute(
                """UPDATE messages
                   SET body=?, edited_at=?, edited_by='local', version=version+1
                   WHERE message_id=? AND version=?""",
                (body, now, message_id, req.version),
            )
            if cursor.rowcount == 0:
                raise HTTPException(
                    409, "Message was modified by another user (version conflict)"
                )

            # Keep matching file_versions.commit_message in sync when possible
            if owner_type == "system_version":
                try:
                    # Pair by chronological order: nth system_version ↔ version_no n
                    sv_rows = conn.execute(
                        """SELECT message_id FROM messages
                           WHERE owner_type='system_version' AND owner_id=?
                           ORDER BY created_at ASC, message_id ASC""",
                        (msg["owner_id"],),
                    ).fetchall()
                    idx = next(
                        (
                            i
                            for i, r in enumerate(sv_rows)
                            if r["message_id"] == message_id
                        ),
                        None,
                    )
                    if idx is not None:
                        ver = conn.execute(
                            """SELECT version_id FROM file_versions
                               WHERE file_id=?
                               ORDER BY version_no ASC
                               LIMIT 1 OFFSET ?""",
                            (msg["owner_id"], idx),
                        ).fetchone()
                        if ver:
                            conn.execute(
                                "UPDATE file_versions SET commit_message=? WHERE version_id=?",
                                (body, ver["version_id"]),
                            )
                except Exception:
                    pass

            row = conn.execute(
                "SELECT * FROM messages WHERE message_id=?", (message_id,)
            ).fetchone()

        emit_event(
            "message.updated",
            collection_id,
            {"message_id": message_id},
        )
        return _row_to_message(row, conn)
    finally:
        conn.close()


def delete_message(collection_id: str, message_id: str) -> None:
    conn = _open_db(collection_id)
    try:
        with conn:
            msg = conn.execute(
                "SELECT * FROM messages WHERE message_id=?", (message_id,)
            ).fetchone()
            if not msg:
                raise HTTPException(404, f"Message '{message_id}' not found")

            if msg["author_type"] == "system":
                raise HTTPException(403, "System messages cannot be deleted")

            conn.execute("DELETE FROM messages WHERE message_id=?", (message_id,))

        emit_event(
            "message.deleted",
            collection_id,
            {"message_id": message_id},
        )
    finally:
        conn.close()


def list_messages(
    collection_id: str, owner_type: str, owner_id: str
) -> list[MessageOut]:
    """List messages for an owner.

    For *files*, includes both user file notes (``owner_type=file``) and
    version-update logs (``owner_type=system_version``), matching
    ``get_file_detail`` / the file-detail Log panel.
    """
    from src.file_mgmt.models import MessageOut

    conn = _open_db(collection_id)
    try:
        ot = (owner_type or "").strip().lower()
        if ot == "file":
            rows = conn.execute(
                """SELECT * FROM messages
                   WHERE owner_id=?
                     AND owner_type IN ('file', 'system_version')
                   ORDER BY created_at DESC""",
                (owner_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM messages
                   WHERE owner_type=? AND owner_id=?
                   ORDER BY created_at DESC""",
                (owner_type, owner_id),
            ).fetchall()
        return [_row_to_message(r, conn) for r in rows]
    finally:
        conn.close()


def _purge_orphan_messages(conn) -> None:
    """Drop messages whose owner row no longer exists (self-heal old bugs)."""
    conn.execute(
        """DELETE FROM messages
           WHERE owner_type='folder'
             AND owner_id NOT IN (SELECT folder_id FROM folders)"""
    )
    conn.execute(
        """DELETE FROM messages
           WHERE owner_type='file'
             AND owner_id NOT IN (SELECT file_id FROM files)"""
    )
    conn.execute(
        """DELETE FROM messages
           WHERE owner_type='node'
             AND owner_id NOT IN (SELECT node_id FROM nodes)"""
    )


def list_root_messages(
    collection_id: str,
    include_node_msgs: bool = False,
    include_file_msgs: bool = False,
    recursive: bool = False,
) -> list[MessageOut]:
    """Message stream for collection root (folder view).

    Always includes collection-level messages.

    - ``include_file_msgs`` only: + orphan file messages (no file_paths)
    - ``recursive`` only: + every folder's own messages
    - both: + all folder messages + all file messages in the collection
    """
    from src.file_mgmt.models import MessageOut

    conn = _open_db(collection_id)
    try:
        # Heal pre-fix leftovers (e.g. user_group folder delete left messages).
        _purge_orphan_messages(conn)
        conn.commit()

        queries: list[str] = [
            """SELECT m.* FROM messages m
               WHERE m.owner_type='collection' AND m.owner_id=?"""
        ]
        params: list = [collection_id]

        if recursive:
            # Only folders that still exist — never show dangling owner_ids.
            queries.append(
                """SELECT m.* FROM messages m
                   WHERE m.owner_type='folder'
                     AND m.owner_id IN (SELECT folder_id FROM folders)"""
            )
            if include_file_msgs:
                # User file notes + version-update logs (system_version)
                queries.append(
                    """SELECT m.* FROM messages m
                       WHERE m.owner_type IN ('file', 'system_version')
                         AND m.owner_id IN (SELECT file_id FROM files)"""
                )
            if include_node_msgs:
                queries.append(
                    """SELECT m.* FROM messages m
                       WHERE m.owner_type='node'
                         AND m.owner_id IN (SELECT node_id FROM nodes)"""
                )
        else:
            if include_file_msgs:
                # Root layer only: orphan files (not mounted in any folder)
                queries.append(
                    """SELECT m.* FROM messages m
                       WHERE m.owner_type IN ('file', 'system_version')
                         AND m.owner_id IN (SELECT file_id FROM files)
                         AND m.owner_id NOT IN (SELECT file_id FROM file_paths)"""
                )
            # Nodes without Nested at root: no-op.
            # Root has no bound group/chain; branch/group nodes live on child
            # folders and require Nested (recursive) to appear.

        sql = " UNION ".join(queries) + " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_message(r, conn) for r in rows]
    finally:
        conn.close()


def _descendant_folder_ids(conn, folder_id: str) -> list[str]:
    """Return ``folder_id`` plus every nested folder under it (recursive)."""
    rows = conn.execute(
        """
        WITH RECURSIVE descendants AS (
          SELECT folder_id FROM folders WHERE folder_id=?
          UNION ALL
          SELECT f.folder_id
          FROM folders f
          JOIN descendants d ON f.parent_folder_id = d.folder_id
        )
        SELECT folder_id FROM descendants
        """,
        (folder_id,),
    ).fetchall()
    return [r["folder_id"] for r in rows]


def list_folder_messages(
    collection_id: str,
    folder_id: str,
    include_node_msgs: bool = True,
    include_file_msgs: bool = True,
    recursive: bool = False,
) -> list[MessageOut]:
    """Aggregated message stream for a folder.

    Always includes the folder's own messages.

    - ``include_file_msgs``: file messages for files mounted in the scope
    - ``include_node_msgs``: node messages linked to folders in scope via:
        * groups bound to those folders (group.folder_id), or
        * chains bound to those folders (chain.folder_id, e.g. branch folders)
    - ``recursive=False``: scope = this folder only
    - ``recursive=True``: scope = this folder + all descendant folders

    When only ``include_file_msgs`` is on (no recursive): files in the current
    folder layer only. When both recursive and file msgs: every file under the
    whole subtree.
    """
    from src.file_mgmt.models import MessageOut

    conn = _open_db(collection_id)
    try:
        # Validate folder
        fld = conn.execute(
            "SELECT folder_id FROM folders WHERE folder_id=?", (folder_id,)
        ).fetchone()
        if not fld:
            raise HTTPException(404, f"Folder '{folder_id}' not found")

        if recursive:
            scope_ids = _descendant_folder_ids(conn, folder_id)
        else:
            scope_ids = [folder_id]

        if not scope_ids:
            return []

        placeholders = ",".join("?" * len(scope_ids))
        queries: list[str] = []
        params: list = []

        # 1. Folder messages in scope
        queries.append(
            f"""SELECT m.* FROM messages m
                WHERE m.owner_type='folder' AND m.owner_id IN ({placeholders})"""
        )
        params.extend(scope_ids)

        # 2. File messages + version-update logs for files mounted in scope
        if include_file_msgs:
            queries.append(
                f"""SELECT m.* FROM messages m
                    WHERE m.owner_type IN ('file', 'system_version')
                      AND m.owner_id IN (
                        SELECT DISTINCT file_id FROM file_paths
                        WHERE folder_id IN ({placeholders})
                      )"""
            )
            params.extend(scope_ids)

        # 3. Node messages linked via group binding or branch/chain folder
        if include_node_msgs:
            queries.append(
                f"""SELECT m.* FROM messages m
                    WHERE m.owner_type='node'
                      AND m.owner_id IN (
                        SELECT n.node_id FROM nodes n
                        JOIN node_groups g ON g.group_id = n.group_id
                        WHERE g.folder_id IN ({placeholders})
                        UNION
                        SELECT n.node_id FROM nodes n
                        JOIN chains c ON c.chain_id = n.chain_id
                        WHERE c.folder_id IN ({placeholders})
                      )"""
            )
            params.extend(scope_ids)
            params.extend(scope_ids)

        sql = " UNION ".join(queries) + " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_message(r, conn) for r in rows]
    finally:
        conn.close()
