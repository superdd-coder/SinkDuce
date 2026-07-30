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
            now = _now_iso()
            parent_id = req.parent_folder_id
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

            folder_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO folders
                   (folder_id, parent_folder_id, name, kind, is_system,
                    created_by, created_at, updated_at, version)
                   VALUES (?, ?, ?, 'plain', 0, 'local', ?, ?, 1)""",
                (folder_id, parent_id, req.name, now, now),
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

            if "name" in updates and updates["name"] is not None:
                set_clauses.append("name = ?")
                params.append(updates["name"])

            if "parent_folder_id" in updates:
                set_clauses.append("parent_folder_id = ?")
                params.append(updates["parent_folder_id"])

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

            # Sync group name if this folder is bound to a node_group
            if "name" in updates and updates["name"] is not None:
                grp = conn.execute(
                    "SELECT group_id FROM node_groups WHERE folder_id=?",
                    (folder_id,),
                ).fetchone()
                if grp:
                    conn.execute(
                        "UPDATE node_groups SET name=? WHERE group_id=?",
                        (updates["name"], grp["group_id"]),
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
                folder_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO folders
                       (folder_id, parent_folder_id, name, kind, is_system,
                        created_by, created_at, updated_at, version)
                       VALUES (?, NULL, ?, 'user_group', 0, 'local', ?, ?, 1)""",
                    (folder_id, req.name, now, now),
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
        att_rows = conn.execute(
            """SELECT fn.file_id, fn.greyed, f.is_definitive, f.archived, f.version,
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
            attachments.append({
                "file_id": a["file_id"],
                "greyed": bool(a["greyed"]),
                "is_definitive": bool(a["is_definitive"]),
                "archived": bool(a["archived"]),
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
        node_msgs = [_row_to_message(r) for r in msg_rows]

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


def _row_to_message(row) -> MessageOut:
    from src.file_mgmt.models import MessageOut

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
    )


def _row_to_file_out(row, conn=None) -> FileOut:
    from src.file_mgmt.models import FileOut

    f = FileOut(
        file_id=row["file_id"],
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

    Rules:
    - files.archived=1 -> True
    - file_paths.archived=1 -> True (path-level archive)
    - 派生路径 (source_node_id != NULL): 查 file_nodes.greyed
    - 持久路径 (source_node_id=NULL) + files.archived=0 -> False
    """
    if file_row["archived"]:
        return True
    try:
        if path_row["archived"]:
            return True
    except (KeyError, IndexError):
        pass
    if path_row["source_node_id"] is not None:
        fn = conn.execute(
            "SELECT greyed FROM file_nodes WHERE file_id=? AND node_id=?",
            (file_row["file_id"], path_row["source_node_id"]),
        ).fetchone()
        if fn and fn["greyed"]:
            return True
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

        base = _row_to_file_out(file_row, conn)

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

        # node associations
        node_rows = conn.execute(
            """SELECT n.node_id, n.title, n.node_type, fn.greyed
               FROM file_nodes fn
               JOIN nodes n ON n.node_id = fn.node_id
               WHERE fn.file_id=?""",
            (file_id,),
        ).fetchall()
        nodes = [
            {
                "node_id": nr["node_id"],
                "title": nr["title"],
                "node_type": nr["node_type"],
                "greyed": bool(nr["greyed"]),
            }
            for nr in node_rows
        ]

        # messages
        msg_rows = conn.execute(
            """SELECT * FROM messages
               WHERE owner_type='file' AND owner_id=?
               ORDER BY created_at DESC""",
            (file_id,),
        ).fetchall()
        messages = [MessageOut(**_row_to_message(r).model_dump()) for r in msg_rows]

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
    collection_id: str, folder_id: str | None = None, archived: bool | None = None
) -> list[FileSummary]:
    conn = _open_db(collection_id)
    try:
        if folder_id:
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

        results: list[FileSummary] = []
        for r in rows:
            if archived is not None and bool(r["archived"]) != archived:
                continue
            fs = _row_to_file_out(r, conn)
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
        result: list = []
        for r in rows:
            fs = _row_to_file_out(r, conn)
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


def _write_upload_file(collection_id: str, file_id: str, file_bytes: bytes, filename: str) -> tuple[Path, str]:
    """Write upload file bytes to disk. Returns (file_path, safe_name)."""
    safe_name = Path(filename).name
    if not safe_name:
        raise HTTPException(400, "Invalid filename")
    file_dir = _files_dir(collection_id) / file_id
    file_dir.mkdir(parents=True, exist_ok=True)
    save_path = file_dir / safe_name
    save_path.write_bytes(file_bytes)
    return save_path, safe_name


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
    """Set archived=true on all Qdrant chunks for a given file_id. Returns updated count."""
    _log = logging.getLogger("file_mgmt.service")
    try:
        from src.services import services
        if services.db is None:
            _log.warning("Qdrant not available, skipping archive mark for %s", file_id)
            return 0
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        count = services.db.count_by_filter(
            collection_id,
            Filter(must=[FieldCondition(key="file_id", match=MatchValue(value=file_id))]),
        )
        # Update payload: set archived=true, is_current=false via scroll + re-upsert
        filter_cond = Filter(
            must=[FieldCondition(key="file_id", match=MatchValue(value=file_id))]
        )
        all_points = []
        offset = None
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
                payload["archived"] = True
                payload["is_current"] = False
                all_points.append((str(p.id), p.vector, payload))
            if offset is None:
                break

        if all_points:
            from qdrant_client.models import PointStruct
            points = [
                PointStruct(id=id_, vector=vec, payload=pl)
                for id_, vec, pl in all_points
            ]
            services.db.client.upsert(collection_name=collection_id, points=points)

        return count
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
    folder_id: str,
    file_bytes: bytes,
    filename: str,
    source_node_id: str | None = None,
) -> FileSummary:
    """Upload a file to a folder. Creates file record + version + path + optional ingest.

    Steps:
    1. Validate folder exists
    2. Generate file_id, store to disk
    3. Parse, check supported types
    4. Create file_versions (version_no=1)
    5. Create files record
    6. Write file_paths (persistent or derived)
    7. If supported: ingest to Qdrant
    8. Create system version message
    9. emit_event
    """
    conn = _open_db(collection_id)
    try:
        # PRAGMA must be set before BEGIN
        conn.execute("PRAGMA defer_foreign_keys=ON")
        with conn:
            # 1. Validate folder
            fld = conn.execute(
                "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()
            if not fld:
                raise HTTPException(404, f"Folder '{folder_id}' not found")

            # 2. Generate IDs and store file
            file_id = uuid.uuid4().hex
            file_id_for_cleanup = file_id
            version_id = uuid.uuid4().hex

            save_path, safe_name = _write_upload_file(collection_id, file_id, file_bytes, filename)
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

            # 6. Write file_paths
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
        result = _row_to_file_out(row, conn)
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
    parent_folder_id: str,
    files_data: list[tuple[bytes, str]],
) -> list[FileSummary]:
    """Upload an entire folder preserving relative paths.

    Args:
        files_data: list of (bytes_content, relative_filename) tuples
    """
    conn = _open_db(collection_id)
    now = _now_iso()

    # Validate parent folder
    try:
        parent = conn.execute(
            "SELECT * FROM folders WHERE folder_id=?", (parent_folder_id,)
        ).fetchone()
        if not parent:
            raise HTTPException(404, f"Folder '{parent_folder_id}' not found")
    finally:
        conn.close()

    # 1. Group files by relative path, create folders
    folder_cache: dict[str, str] = {}  # relative_dir -> folder_id

    def _ensure_folder(rel_dir: str) -> str:
        """Ensure a virtual folder exists for the given relative directory path."""
        if not rel_dir or rel_dir == ".":
            return parent_folder_id
        if rel_dir in folder_cache:
            return folder_cache[rel_dir]

        parts = Path(rel_dir).parts
        current_parent = parent_folder_id
        accumulated = ""
        for part in parts:
            accumulated = str(Path(accumulated) / part) if accumulated else part
            if accumulated in folder_cache:
                current_parent = folder_cache[accumulated]
                continue

            conn2 = _open_db(collection_id)
            try:
                with conn2:
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
            collection_id, target_folder_id, file_bytes, relative_path, source_node_id=None
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
) -> FileSummary:
    """Upload a new version of an existing file.

    Steps:
    1. Get current version_no → new version_no = MAX+1
    2. Generate new version_id, store new version
    3. Archive old version (DB + Qdrant)
    4. Ingest new version to Qdrant
    5. Update files.current_version_id
    6. Create system version message
    7. Check version limit (>20 → warning)
    8. emit_event
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

            # 2. Store new version
            save_path, safe_name = _write_upload_file(collection_id, file_id, file_bytes, filename)
            conn.execute(
                """INSERT INTO file_versions
                   (version_id, file_id, version_no, storage_file_id,
                    archived, commit_message, created_by, created_at)
                   VALUES (?, ?, ?, ?, 0, ?, 'local', ?)""",
                (new_version_id, file_id, new_version_no, safe_name, commit_message, now),
            )

            # 3. Archive old version in DB
            if old_version:
                conn.execute(
                    "UPDATE file_versions SET archived=1 WHERE version_id=?",
                    (old_version["version_id"],),
                )

            # Archive old Qdrant chunks
            _mark_qdrant_chunks_archived(collection_id, file_id)

            # 4. Ingest new version
            chunk_count = 0
            if not bool(file_row["unsupported"]):
                try:
                    chunk_count = _ingest_file_to_qdrant(
                        collection_id, file_id, save_path, new_version_id,
                        source_label=safe_name,
                    )
                except Exception as e:
                    logger.warning(
                        "Version ingest failed for file %s: %s", file_id, e
                    )

            # 5. Update current_version_id
            conn.execute(
                "UPDATE files SET current_version_id=? WHERE file_id=?",
                (new_version_id, file_id),
            )

            # 6. Create system version message
            message_id = uuid.uuid4().hex
            body = commit_message if commit_message else f"Updated to version {new_version_no}"
            conn.execute(
                """INSERT INTO messages
                   (message_id, owner_type, owner_id, source_node_id, body,
                    author_type, author_id, created_at, edited_at, edited_by, version)
                   VALUES (?, 'system_version', ?, NULL, ?,
                    'system', 'local', ?, NULL, NULL, 1)""",
                (message_id, file_id, body, now),
            )

            row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()

        # 9. Version limit warning
        warning = None
        if new_version_no > MAX_VERSIONS:
            warning = (
                f"File has {new_version_no} versions (limit is {MAX_VERSIONS}). "
                "Consider cleaning up old versions."
            )

        emit_event(
            "file.updated",
            collection_id,
            {"file_id": file_id, "version_no": new_version_no},
        )

        result = _row_to_file_out(row, conn)
        if warning:
            result.filename = f"[WARNING] {result.filename}"
        return result
    finally:
        conn.close()


# ── delete_file ──────────────────────────────────────────────────


def delete_file(collection_id: str, file_id: str) -> None:
    """Permanently delete a file: Qdrant chunks, disk, DB records, messages.

    Cascading cleanup:
    1. Delete Qdrant chunks
    2. Delete disk directory
    3. Delete file_nodes
    4. Delete file_paths
    5. Delete file_versions
    6. Delete file's messages
    7. Delete files record
    8. emit_event
    """
    conn = _open_db(collection_id)
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        with conn:
            file_row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise HTTPException(404, f"File '{file_id}' not found")

            # 1. Delete Qdrant chunks
            _delete_qdrant_chunks_by_file_id(collection_id, file_id)

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

        # Clean up file index
        try:
            from src.collections.file_index import remove_by_source as remove_file_index
            remove_file_index(collection_id, f"__file__:{file_id}")
        except Exception:
            logger.warning("Failed to clean up file index for %s", file_id, exc_info=True)

        emit_event("file.deleted", collection_id, {"file_id": file_id})
    finally:
        conn.close()


# ── update_file ──────────────────────────────────────────────────


def update_file(
    collection_id: str, file_id: str, req: dict
) -> FileSummary:
    """Update file metadata: is_definitive toggle, archived toggle (Phase 5).

    Uses optimistic locking: version field must match.
    """
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

            set_clauses: list[str] = []
            params: list = []

            if "is_definitive" in req:
                set_clauses.append("is_definitive = ?")
                params.append(1 if req["is_definitive"] else 0)

            if "archived" in req:
                new_archived = 1 if req["archived"] else 0
                set_clauses.append("archived = ?")
                params.append(new_archived)
                # If archiving, also mark Qdrant chunks
                if new_archived:
                    _mark_qdrant_chunks_archived(collection_id, file_id)
                else:
                    # Unarchiving — mark Qdrant chunks as not archived
                    # (restore current version chunks only)
                    pass

            set_clauses.append("version = version + 1")
            params.extend([file_id, version])

            cursor = conn.execute(
                f"UPDATE files SET {', '.join(set_clauses)} "
                "WHERE file_id = ? AND version = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise HTTPException(
                    409, "File was modified by another user (version conflict)"
                )

            row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()

        if "archived" in req and req["archived"]:
            emit_event("file.archived", collection_id, {"file_id": file_id})
        elif "archived" in req:
            emit_event("file.unarchived", collection_id, {"file_id": file_id})
        if "is_definitive" in req:
            emit_event("file.updated", collection_id, {"file_id": file_id})

        return _row_to_file_out(row, conn)
    finally:
        conn.close()


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

            existing_end = conn.execute(
                'SELECT node_id FROM nodes WHERE chain_id=? AND node_type="end" AND node_id != ?',
                (chain_id, node_id),
            ).fetchone()
            if existing_end:
                raise HTTPException(
                    409,
                    "This chain already has an end node. "
                    "Use reopen_chain first to re-open it.",
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

            result = {
                "greyed_files": list(path_archived),  # legacy field name
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
    """Archive or unarchive a file.

    - archived=True: file-level archive (``files.archived=1``) + Qdrant
    - archived=False:
        * if file-level archived → restore file + Qdrant
        * if ``folder_id`` set → clear path-level archives in that folder
          (branch merge greys that only set ``file_paths.archived``)
    - Optimistic locking via version (always bumps version on success)
    """
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
            path_cleared: list[str] = []
            did_file_change = False

            if req.archived:
                # File-level archive
                cursor = conn.execute(
                    "UPDATE files SET archived=1, version=version+1 "
                    "WHERE file_id=? AND version=?",
                    (file_id, req.version),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(
                        409, "File was modified by another user (version conflict)"
                    )
                did_file_change = True
                _mark_qdrant_chunks_archived(collection_id, file_id)
                conn.execute(
                    "UPDATE file_nodes SET greyed=1 WHERE file_id=?", (file_id,)
                )
            else:
                # Unarchive: file-level and/or path-level in folder
                folder_id = (req.folder_id or "").strip() or None
                if folder_id == "__archived__":
                    folder_id = None

                if folder_id:
                    fld = conn.execute(
                        "SELECT folder_id FROM folders WHERE folder_id=?",
                        (folder_id,),
                    ).fetchone()
                    if not fld:
                        raise HTTPException(404, f"Folder '{folder_id}' not found")
                    path_cleared = _unarchive_paths_on_folder(
                        conn, file_id, folder_id
                    )

                if was_file_archived:
                    cursor = conn.execute(
                        "UPDATE files SET archived=0, version=version+1 "
                        "WHERE file_id=? AND version=?",
                        (file_id, req.version),
                    )
                    if cursor.rowcount == 0:
                        raise HTTPException(
                            409,
                            "File was modified by another user (version conflict)",
                        )
                    did_file_change = True
                    _restore_qdrant_chunks_for_file(collection_id, file_id)
                elif path_cleared:
                    # Path-only unarchive still bumps version for optimistic lock
                    cursor = conn.execute(
                        "UPDATE files SET version=version+1 "
                        "WHERE file_id=? AND version=?",
                        (file_id, req.version),
                    )
                    if cursor.rowcount == 0:
                        raise HTTPException(
                            409,
                            "File was modified by another user (version conflict)",
                        )
                    did_file_change = True
                else:
                    # Nothing to unarchive
                    raise HTTPException(
                        400,
                        "Nothing to unarchive: file is not archived"
                        + (
                            " and has no path archives in this folder"
                            if folder_id
                            else " (pass folder_id to clear path-level archives)"
                        ),
                    )

            row = conn.execute(
                "SELECT * FROM files WHERE file_id=?", (file_id,)
            ).fetchone()
            out = _row_to_file_out(row, conn)
            # Reflect path state when unarchiving in a folder context
            if not req.archived and req.folder_id and path_cleared:
                out.is_greyed = False

        if req.archived:
            emit_event("file.archived", collection_id, {"file_id": file_id})
        else:
            emit_event(
                "file.unarchived",
                collection_id,
                {
                    "file_id": file_id,
                    "folder_id": req.folder_id,
                    "path_ids_cleared": path_cleared if not req.archived else [],
                    "file_level": did_file_change and was_file_archived,
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
        return _row_to_file_out(file_row, conn)
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
        return _row_to_message(row)
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

            if msg["author_type"] == "system":
                raise HTTPException(403, "System messages cannot be edited")

            now = _now_iso()
            cursor = conn.execute(
                """UPDATE messages
                   SET body=?, edited_at=?, edited_by='local', version=version+1
                   WHERE message_id=? AND version=?""",
                (req.body, now, message_id, req.version),
            )
            if cursor.rowcount == 0:
                raise HTTPException(
                    409, "Message was modified by another user (version conflict)"
                )

            row = conn.execute(
                "SELECT * FROM messages WHERE message_id=?", (message_id,)
            ).fetchone()

        emit_event(
            "message.updated",
            collection_id,
            {"message_id": message_id},
        )
        return _row_to_message(row)
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
    from src.file_mgmt.models import MessageOut

    conn = _open_db(collection_id)
    try:
        rows = conn.execute(
            """SELECT * FROM messages
               WHERE owner_type=? AND owner_id=?
               ORDER BY created_at DESC""",
            (owner_type, owner_id),
        ).fetchall()
        return [_row_to_message(r) for r in rows]
    finally:
        conn.close()


def list_folder_messages(
    collection_id: str,
    folder_id: str,
    include_node_msgs: bool = True,
    include_file_msgs: bool = True,
) -> list[MessageOut]:
    """Aggregated message stream for a folder.

    Includes:
    - folder's own messages
    - file messages (files in this folder via file_paths)
    - node messages (nodes in groups bound to this folder)
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

        queries: list[str] = []
        params: list = []

        # 1. Folder's own messages
        queries.append(
            "SELECT m.* FROM messages m WHERE m.owner_type='folder' AND m.owner_id=?"
        )
        params.append(folder_id)

        # 2. File messages for files in this folder
        if include_file_msgs:
            queries.append(
                """SELECT m.* FROM messages m
                   WHERE m.owner_type='file'
                     AND m.owner_id IN (
                       SELECT DISTINCT file_id FROM file_paths WHERE folder_id=?
                     )"""
            )
            params.append(folder_id)

        # 3. Node messages for nodes in groups bound to this folder
        if include_node_msgs:
            queries.append(
                """SELECT m.* FROM messages m
                   WHERE m.owner_type='node'
                     AND m.owner_id IN (
                       SELECT n.node_id FROM nodes n
                       JOIN node_groups g ON g.group_id = n.group_id
                       WHERE g.folder_id=?
                     )"""
            )
            params.append(folder_id)

        sql = " UNION ".join(queries) + " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_message(r) for r in rows]
    finally:
        conn.close()
