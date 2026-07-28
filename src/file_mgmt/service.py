"""Business logic for file-management metadata CRUD (Phase 2).

Pure functions: each takes collection_id as the first argument,
opens a per-collection SQLite connection via store.get_db(),
executes SQL, and returns Pydantic models. Write operations call
emit_event() after the transaction commits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from src.file_mgmt.events import emit_event
from src.file_mgmt.models import (
    ChainCreate,
    ChainOut,
    ChainUpdate,
    FolderCreate,
    FolderOut,
    FolderTree,
    FolderUpdate,
    GroupCreate,
    GroupOut,
    GroupUpdate,
    NodeCreate,
    NodeOut,
    NodeReorder,
    NodeUpdate,
)
from src.file_mgmt.store import get_db

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
    try:
        return get_db(collection_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Collection '{collection_id}' file-management DB not initialized",
        )


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


def _row_to_group(row, node_count: int = 0) -> GroupOut:
    return GroupOut(
        group_id=row["group_id"],
        folder_id=row["folder_id"],
        name=row["name"],
        description=row["description"],
        created_by=row["created_by"],
        node_count=node_count,
    )


def _row_to_chain(row, has_end_node: bool = False, node_count: int = 0) -> ChainOut:
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
            conn.execute(
                "DELETE FROM folders WHERE folder_id=?", (folder_id,)
            )
    else:
        conn.execute(
            "DELETE FROM folders WHERE folder_id=?", (folder_id,)
        )


def _delete_chain_subtree(conn, chain_id: str) -> None:
    """Delete a chain, its sub-chains, nodes, and associated folder."""
    sub_chains = conn.execute(
        "SELECT chain_id FROM chains WHERE parent_chain_id=?", (chain_id,)
    ).fetchall()
    for sc in sub_chains:
        _delete_chain_subtree(conn, sc["chain_id"])

    conn.execute("DELETE FROM nodes WHERE chain_id=?", (chain_id,))

    chain = conn.execute(
        "SELECT folder_id FROM chains WHERE chain_id=?", (chain_id,)
    ).fetchone()

    # Delete the chains record BEFORE the folder to avoid FK violation
    # (chains.folder_id REFERENCES folders).  Capture folder_id first.
    folder_id = chain["folder_id"] if chain else None
    conn.execute("DELETE FROM chains WHERE chain_id=?", (chain_id,))

    if folder_id:
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
            return FolderTree(
                **base.model_dump(),
                children=kids,
                file_count=0,
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
    conn = _open_db(collection_id)
    try:
        rows = conn.execute(
            """SELECT g.*, COUNT(n.node_id) AS node_count
               FROM node_groups g
               LEFT JOIN nodes n ON n.group_id = g.group_id
               GROUP BY g.group_id
               ORDER BY g.name"""
        ).fetchall()
        return [_row_to_group(r, r["node_count"]) for r in rows]
    finally:
        conn.close()


def create_group(collection_id: str, req: GroupCreate) -> GroupOut:
    conn = _open_db(collection_id)
    try:
        with conn:
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
                   (group_id, folder_id, name, description, created_by)
                   VALUES (?, ?, ?, ?, 'local')""",
                (group_id, folder_id, req.name, req.description),
            )
            row = conn.execute(
                "SELECT * FROM node_groups WHERE group_id=?", (group_id,)
            ).fetchone()

        emit_event("group.created", collection_id, {"group_id": group_id})
        return _row_to_group(row)
    finally:
        conn.close()


def update_group(
    collection_id: str, group_id: str, req: GroupUpdate
) -> GroupOut:
    updates = req.model_dump(exclude_unset=True)

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
            if fld and fld["is_system"] and "name" in updates:
                raise HTTPException(403, "System groups cannot be renamed")

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

        if "name" in updates:
            emit_event("group.renamed", collection_id, {"group_id": group_id})
        return _row_to_group(row)
    finally:
        conn.close()


def delete_group(collection_id: str, group_id: str) -> None:
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
            if fld and fld["is_system"]:
                raise HTTPException(403, "System groups cannot be deleted")

            conn.execute(
                "UPDATE nodes SET group_id=NULL WHERE group_id=?", (group_id,)
            )
            conn.execute(
                "DELETE FROM node_groups WHERE group_id=?", (group_id,)
            )
            _delete_folder_subtree(conn, grp["folder_id"])

        emit_event("group.deleted", collection_id, {"group_id": group_id})
    finally:
        conn.close()


# === Chain CRUD ===


def list_chains(collection_id: str) -> list[ChainOut]:
    conn = _open_db(collection_id)
    try:
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
            result.append(
                _row_to_chain(
                    r,
                    has_end_node=end is not None,
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

            _delete_chain_subtree(conn, chain_id)

        emit_event("chain.deleted", collection_id, {"chain_id": chain_id})
    finally:
        conn.close()


def reopen_chain(collection_id: str, chain_id: str) -> ChainOut:
    conn = _open_db(collection_id)
    try:
        with conn:
            ch = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            if not ch:
                raise HTTPException(404, f"Chain '{chain_id}' not found")

            conn.execute(
                'DELETE FROM nodes WHERE chain_id=? AND node_type="end"',
                (chain_id,),
            )
            row = conn.execute(
                "SELECT * FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()

        emit_event("chain.reopened", collection_id, {"chain_id": chain_id})
        return _row_to_chain(row)
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

            for field in ("title", "group_id", "order", "event_time"):
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

            row = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()

        emit_event("node.updated", collection_id, {"node_id": node_id})
        return _row_to_node(row)
    finally:
        conn.close()


def delete_node(collection_id: str, node_id: str) -> None:
    conn = _open_db(collection_id)
    try:
        with conn:
            node = conn.execute(
                "SELECT node_id FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{node_id}' not found")

            conn.execute(
                "DELETE FROM nodes WHERE node_id=?", (node_id,)
            )

        emit_event("node.deleted", collection_id, {"node_id": node_id})
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
        out = _row_to_node(node)
        return {
            **out.model_dump(),
            "attachments": [],
            "messages": [],
        }
    finally:
        conn.close()
