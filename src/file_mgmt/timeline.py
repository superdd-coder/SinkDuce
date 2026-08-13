"""Groups, chains, nodes, timeline, meeting anchors, end-chain."""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException

from src.file_mgmt.access import _actor_for, _actor_id, _main_chain_id, _now_iso, _open_db
from src.file_mgmt.events import emit_event
from src.file_mgmt.layout import (
    _assert_folder_name_free,
    _clear_node_inbound_fks,
    _delete_chain_subtree,
    _purge_node_owned_rows,
    _row_icon,
    _row_to_chain,
    _row_to_group,
    _row_to_node,
)
from src.file_mgmt.messages import _row_to_message
from src.file_mgmt.models import (
    ChainCreate,
    ChainOut,
    ChainUpdate,
    EndChainRequest,
    GroupCreate,
    GroupOut,
    GroupUpdate,
    NodeCreate,
    NodeOut,
    NodeReorder,
    NodeUpdate,
)

logger = logging.getLogger("file_mgmt.service")


def _ensure_path_archive_column(conn) -> None:
    from src.file_mgmt.files import _ensure_path_archive_column as impl
    return impl(conn)


def _unarchive_paths_by_ids(conn, path_ids):
    from src.file_mgmt.files import _unarchive_paths_by_ids as impl
    return impl(conn, path_ids)


def _unarchive_file_if_archived(conn, collection_id, file_id):
    from src.file_mgmt.files import _unarchive_file_if_archived as impl
    return impl(conn, collection_id, file_id)


def _sync_node_derived_paths(conn, node_id, file_id=None):
    from src.file_mgmt.files import _sync_node_derived_paths as impl
    return impl(conn, node_id, file_id=file_id)


def _sync_chain_branch_paths(conn, chain_id):
    from src.file_mgmt.files import _sync_chain_branch_paths as impl
    return impl(conn, chain_id)


def _archive_paths_on_folder(conn, file_id, folder_id):
    from src.file_mgmt.files import _archive_paths_on_folder as impl
    return impl(conn, file_id, folder_id)


def _archive_paths_by_ids(conn, path_ids):
    from src.file_mgmt.files import _archive_paths_by_ids as impl
    return impl(conn, path_ids)


def _load_file_index(collection_id):
    from src.file_mgmt.files import _load_file_index as impl
    return impl(collection_id)


def _attachment_display_fields(*args, **kwargs):
    from src.file_mgmt.files import _attachment_display_fields as impl
    return impl(*args, **kwargs)


def _promote_file_archive_if_needed(conn, collection_id, file_id):
    from src.file_mgmt.files import _promote_file_archive_if_needed as impl
    return impl(conn, collection_id, file_id)




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
    actor = _actor_for("group.create", collection_id)
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
                       VALUES (?, NULL, ?, 'user_group', 0, ?, ?, ?, 1)""",
                    (folder_id, group_folder_name, actor.id, now, now),
                )

            group_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO node_groups
                   (group_id, folder_id, name, description, created_by,
                    icon_type, icon_value, icon_color)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    group_id,
                    folder_id,
                    req.name,
                    req.description,
                    actor.id,
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

    _actor_for("group.update", collection_id, group_id=group_id)
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
    _actor_for("group.delete", collection_id, group_id=group_id)
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
    actor = _actor_for("chain.create", collection_id)
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
                       VALUES (?, NULL, ?, 'branch', 0, ?, ?, ?, 1)""",
                    (folder_id, req.title, actor.id, now, now),
                )

            chain_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO chains
                   (chain_id, parent_chain_id, parent_node_id, folder_id,
                    title, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (chain_id, req.parent_chain_id, req.parent_node_id,
                 folder_id, req.title, actor.id),
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
    _actor_for("chain.update", collection_id, chain_id=chain_id)
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
    _actor_for("chain.delete", collection_id, chain_id=chain_id)
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

    _actor_for("chain.reopen", collection_id, chain_id=chain_id)
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
            'SELECT * FROM nodes WHERE chain_id=? ORDER BY "order", created_at',
            (chain_id,),
        ).fetchall()
        return [_row_to_node(r) for r in rows]
    finally:
        conn.close()


def _node_summary_row(
    conn,
    row,
    *,
    collection_id: str,
    group_names: dict[str, str],
    child_branches_by_parent: dict[str, list[dict]],
    depth: str = "summary",
    file_index: dict[str, dict] | None = None,
) -> dict:
    """Node dict with group_name, attachment/message counts, child branches.

    Sort key for display: (order, created_at). Same ``order`` uses created_at
    as secondary sort (stable, documented for agents).
    """
    base = _row_to_node(row).model_dump()
    nid = base["node_id"]
    gid = base.get("group_id")
    base["group_name"] = group_names.get(gid) if gid else None

    att = conn.execute(
        "SELECT COUNT(*) AS c FROM file_nodes WHERE node_id=?", (nid,)
    ).fetchone()
    base["attachment_count"] = int(att["c"] or 0)

    msg = conn.execute(
        """SELECT COUNT(*) AS c FROM messages
           WHERE owner_type='node' AND owner_id=?""",
        (nid,),
    ).fetchone()
    base["message_count"] = int(msg["c"] or 0)

    # Definitive attachment?
    def_att = conn.execute(
        """SELECT 1 FROM file_nodes fn
           JOIN files f ON f.file_id = fn.file_id
           WHERE fn.node_id=? AND f.is_definitive=1 LIMIT 1""",
        (nid,),
    ).fetchone()
    base["has_definitive_file"] = def_att is not None

    children = child_branches_by_parent.get(nid) or []
    base["child_branches"] = children
    if children:
        base["child_branch_count"] = len(children)

    if depth == "minimal":
        # Skip attachment payload for lighter responses
        return base

    # Include short attachment list so agents need fewer get_node calls
    att_rows = conn.execute(
        """SELECT fn.file_id, f.is_definitive, fv.storage_file_id
           FROM file_nodes fn
           JOIN files f ON f.file_id = fn.file_id
           LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
           WHERE fn.node_id=?""",
        (nid,),
    ).fetchall()
    idx = file_index if file_index is not None else _load_file_index(collection_id)
    base["attachments"] = []
    for a in att_rows:
        names = _attachment_display_fields(
            collection_id,
            a["file_id"],
            a["storage_file_id"],
            index=idx,
        )
        base["attachments"].append(
            {
                "file_id": a["file_id"],
                "filename": names["filename"],
                "display_name": names["display_name"],
                "is_definitive": bool(a["is_definitive"]),
            }
        )
    return base


def build_timeline(
    collection_id: str,
    *,
    depth: str = "summary",
) -> dict:
    """One-shot timeline: main chain + nested branches + enriched nodes.

    ``depth``:
    - ``minimal``: nodes with ids/titles/group_name/child_branches only
    - ``summary`` (default): + counts + short attachment filename list
    - ``full``: same as summary (attachments always included at summary+)

    Nodes are ordered by ``(order ASC, created_at ASC)``.

    Branches whose parent_node is missing or not on the parent chain are
    returned in ``detached_branches`` (full nodes) so agents never miss them
    when only walking ``timeline.branches``.

    ``warnings`` lists topology issues.
    """
    if depth not in ("minimal", "summary", "full"):
        raise HTTPException(400, "depth must be 'minimal', 'summary', or 'full'")

    from src.file_mgmt.store import _ensure_chains_merge_node_id

    conn = _open_db(collection_id)
    try:
        _ensure_chains_merge_node_id(conn)
        conn.commit()

        group_names = {
            r["group_id"]: r["name"]
            for r in conn.execute("SELECT group_id, name FROM node_groups").fetchall()
        }

        chain_rows = conn.execute("SELECT * FROM chains").fetchall()
        chains_meta: dict[str, dict] = {}
        for r in chain_rows:
            end = conn.execute(
                'SELECT 1 FROM nodes WHERE chain_id=? AND node_type="end" LIMIT 1',
                (r["chain_id"],),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM nodes WHERE chain_id=?",
                (r["chain_id"],),
            ).fetchone()
            try:
                merge_raw = r["merge_node_id"]
            except (KeyError, IndexError):
                merge_raw = None
            co = _row_to_chain(
                r,
                has_end_node=end is not None or bool(merge_raw),
                node_count=count["c"],
            )
            d = co.model_dump()
            # Friendly main title
            if d.get("is_main") and not d.get("title"):
                d["title"] = "Main"
            # parent node title (for branches)
            pnid = d.get("parent_node_id")
            if pnid:
                prow = conn.execute(
                    "SELECT title, chain_id FROM nodes WHERE node_id=?", (pnid,)
                ).fetchone()
                if prow:
                    d["parent_node_title"] = prow["title"]
                    d["parent_node_chain_id"] = prow["chain_id"]
                else:
                    d["parent_node_title"] = None
                    d["parent_node_chain_id"] = None
            else:
                d["parent_node_title"] = None
                d["parent_node_chain_id"] = None
            chains_meta[d["chain_id"]] = d

        # Branches keyed by parent_node_id
        child_branches_by_parent: dict[str, list[dict]] = {}
        for cid, meta in chains_meta.items():
            if meta.get("is_main"):
                continue
            pnid = meta.get("parent_node_id")
            if not pnid:
                continue
            child_branches_by_parent.setdefault(pnid, []).append(
                {
                    "chain_id": cid,
                    "title": meta.get("title"),
                    "node_count": meta.get("node_count", 0),
                    "has_end_node": meta.get("has_end_node", False),
                    "folder_id": meta.get("folder_id"),
                    "merge_node_id": meta.get("merge_node_id"),
                }
            )

        warnings: list[str] = []
        main_id = None
        for cid, meta in chains_meta.items():
            if meta.get("is_main"):
                main_id = cid
                break
        if not main_id:
            warnings.append("No main chain (parent_chain_id IS NULL) found")

        nested_ids: set[str] = set()
        # Load once for all nodes (attachments resolve display names via files.json)
        file_index = (
            _load_file_index(collection_id) if depth != "minimal" else None
        )

        def chain_with_nodes(chain_id: str, *, detached: bool = False) -> dict:
            nested_ids.add(chain_id)
            meta = dict(chains_meta[chain_id])
            if detached:
                meta["detached"] = True
            rows = conn.execute(
                'SELECT * FROM nodes WHERE chain_id=? ORDER BY "order", created_at',
                (chain_id,),
            ).fetchall()
            nodes = [
                _node_summary_row(
                    conn,
                    r,
                    collection_id=collection_id,
                    group_names=group_names,
                    child_branches_by_parent=child_branches_by_parent,
                    depth=depth,
                    file_index=file_index,
                )
                for r in rows
            ]
            meta["nodes"] = nodes
            # Nested branches that fork from nodes on this chain
            branches: list[dict] = []
            for n in nodes:
                for br in n.get("child_branches") or []:
                    bid = br["chain_id"]
                    if bid in chains_meta and bid not in nested_ids:
                        branches.append(chain_with_nodes(bid, detached=False))
            meta["branches"] = branches
            return meta

        # Topology warnings: branch parent_node must exist
        node_ids_all = {
            r["node_id"]
            for r in conn.execute("SELECT node_id FROM nodes").fetchall()
        }
        for cid, meta in chains_meta.items():
            if meta.get("is_main"):
                continue
            pnid = meta.get("parent_node_id")
            if not pnid:
                warnings.append(
                    f"Branch chain '{cid}' title={meta.get('title')!r} has no parent_node_id"
                )
            elif pnid not in node_ids_all:
                warnings.append(
                    f"Branch chain '{cid}' title={meta.get('title')!r} "
                    f"parent_node_id={pnid!r} does not exist on any chain"
                )
            elif meta.get("parent_chain_id"):
                # parent node should live on parent_chain
                prow = conn.execute(
                    "SELECT chain_id FROM nodes WHERE node_id=?", (pnid,)
                ).fetchone()
                if prow and prow["chain_id"] != meta["parent_chain_id"]:
                    warnings.append(
                        f"Branch '{cid}' parent_node_id is on chain "
                        f"{prow['chain_id']}, not parent_chain_id={meta['parent_chain_id']}"
                    )

        timeline = chain_with_nodes(main_id) if main_id else None

        # Branches not reachable from main via parent_node links
        detached_branches: list[dict] = []
        for cid, meta in chains_meta.items():
            if meta.get("is_main"):
                continue
            if cid in nested_ids:
                continue
            det = chain_with_nodes(cid, detached=True)
            reason = "unreachable_from_main"
            pnid = meta.get("parent_node_id")
            if not pnid:
                reason = "missing_parent_node_id"
            elif pnid not in node_ids_all:
                reason = "parent_node_missing"
            elif meta.get("parent_node_chain_id") and meta.get("parent_chain_id"):
                if meta["parent_node_chain_id"] != meta["parent_chain_id"]:
                    reason = "parent_node_not_on_parent_chain"
            det["detach_reason"] = reason
            detached_branches.append(det)
            warnings.append(
                f"Detached branch '{cid}' title={meta.get('title')!r} "
                f"({reason}) — see detached_branches[] for full nodes"
            )

        # Flat index for agents that prefer non-nested
        chains_list = [chains_meta[c] for c in chains_meta]

        return {
            "timeline": timeline,
            "detached_branches": detached_branches,
            "chains": chains_list,
            "groups": [
                {
                    "group_id": g["group_id"],
                    "name": g["name"],
                    "folder_id": g["folder_id"],
                }
                for g in conn.execute(
                    "SELECT group_id, name, folder_id FROM node_groups ORDER BY name"
                ).fetchall()
            ],
            "depth": depth,
            "node_order_rule": "ORDER BY order ASC, created_at ASC (same order → older first)",
            "warnings": warnings,
            "summary": {
                "chain_count": len(chains_meta),
                "branch_count": sum(1 for c in chains_meta.values() if not c.get("is_main")),
                "detached_branch_count": len(detached_branches),
                "node_count": len(node_ids_all),
                "group_count": len(group_names),
            },
            "read_hint": (
                "Walk timeline.branches for attached forks; always also read "
                "detached_branches (broken parent links) so no nodes are missed."
            ),
        }
    finally:
        conn.close()


def create_node(
    collection_id: str, chain_id: str, req: NodeCreate
) -> NodeOut:
    actor = _actor_for("node.create", collection_id, chain_id=chain_id)
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
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (node_id, chain_id, req.group_id, req.node_type,
                 req.title, order, req.event_time, actor.id, now),
            )
            row = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()

        emit_event("node.created", collection_id, {"node_id": node_id})
        try:
            from src.file_mgmt.todo_suggestions import schedule_todo_suggestion_refresh

            schedule_todo_suggestion_refresh(collection_id, chain_id)
        except Exception:
            logger.debug("todo suggestion schedule after create_node failed", exc_info=True)
        return _row_to_node(row)
    finally:
        conn.close()


def update_node(
    collection_id: str, node_id: str, req: NodeUpdate
) -> NodeOut:
    _actor_for("node.update", collection_id, node_id=node_id)
    updates = req.model_dump(exclude_unset=True)

    conn = _open_db(collection_id)
    try:
        old_chain_id = None
        with conn:
            node = conn.execute(
                "SELECT * FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{node_id}' not found")
            old_chain_id = node["chain_id"]

            if "group_id" in updates and updates["group_id"] is not None:
                grp = conn.execute(
                    "SELECT group_id FROM node_groups WHERE group_id=?",
                    (updates["group_id"],),
                ).fetchone()
                if not grp:
                    raise HTTPException(
                        404, f"Group '{updates['group_id']}' not found"
                    )

            # Branch fork/merge anchors must stay on the main chain. Moving them
            # onto a branch detaches the chain from the timeline (parent_node no
            # longer resolves on main) while list_chains still exposes the folder.
            if "chain_id" in updates and updates["chain_id"] != old_chain_id:
                main_id = _main_chain_id(conn)
                new_chain_id = updates["chain_id"]
                dest = conn.execute(
                    "SELECT chain_id FROM chains WHERE chain_id=?",
                    (new_chain_id,),
                ).fetchone()
                if not dest:
                    raise HTTPException(
                        404, f"Chain '{new_chain_id}' not found"
                    )
                is_parent_anchor = conn.execute(
                    "SELECT 1 FROM chains WHERE parent_node_id=? LIMIT 1",
                    (node_id,),
                ).fetchone()
                is_merge_anchor = conn.execute(
                    "SELECT 1 FROM chains WHERE merge_node_id=? LIMIT 1",
                    (node_id,),
                ).fetchone()
                if (is_parent_anchor or is_merge_anchor) and new_chain_id != main_id:
                    raise HTTPException(
                        400,
                        "Branch start/merge anchors must stay on the main chain",
                    )
                # start/end typed nodes also belong on main topology
                ntype = updates.get("node_type") or node["node_type"]
                if ntype in ("start", "end") and new_chain_id != main_id:
                    raise HTTPException(
                        400,
                        "Start/end nodes must stay on the main chain",
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
        try:
            from src.file_mgmt.todo_suggestions import schedule_todo_suggestion_refresh

            new_chain_id = row["chain_id"] if row else old_chain_id
            schedule_todo_suggestion_refresh(collection_id, new_chain_id)
            if old_chain_id and new_chain_id and old_chain_id != new_chain_id:
                schedule_todo_suggestion_refresh(collection_id, old_chain_id)
        except Exception:
            logger.debug("todo suggestion schedule after update_node failed", exc_info=True)
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
    _actor_for("node.delete", collection_id, node_id=node_id)
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
        try:
            from src.file_mgmt.todo_suggestions import schedule_todo_suggestion_refresh

            # chain_id still available from deleted node snapshot above
            schedule_todo_suggestion_refresh(collection_id, node["chain_id"])
        except Exception:
            logger.debug("todo suggestion schedule after delete_node failed", exc_info=True)
        return result
    finally:
        conn.close()


def reorder_node(
    collection_id: str, node_id: str, req: NodeReorder
) -> list[NodeOut]:
    _actor_for("node.reorder", collection_id, node_id=node_id)
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
        try:
            from src.file_mgmt.todo_suggestions import schedule_todo_suggestion_refresh

            schedule_todo_suggestion_refresh(collection_id, chain_id)
        except Exception:
            logger.debug("todo suggestion schedule after reorder_node failed", exc_info=True)
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
        file_index = _load_file_index(collection_id)
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
            names = _attachment_display_fields(
                collection_id,
                a["file_id"],
                a["storage_file_id"],
                index=file_index,
            )
            attachments.append({
                "file_id": a["file_id"],
                "is_definitive": bool(a["is_definitive"]),
                "archived": file_archived or path_archived,
                "filename": names["filename"],
                "display_name": names["display_name"],
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
# Note / Meeting ingest → file-mgmt (immediate, not only lazy migration)
# ════════════════════════════════════════════════════════════════════


def meeting_external_ref(meeting_id: str) -> str:
    """Canonical nodes.external_ref for a meeting anchor."""
    return f"meeting:{meeting_id}"


def ensure_meeting_anchor_node(
    collection_id: str,
    meeting_id: str,
    *,
    title: str,
    event_time: str | None = None,
    chain_id: str | None = None,
) -> str:
    """Get or create the timeline event node for a meeting on a given chain.

    Identity: ``external_ref = meeting:{meeting_id}`` + ``chain_id``
    (unique pair; same meeting may have one node per chain).
    Defaults to main chain when *chain_id* is None.
    """
    if not meeting_id:
        raise ValueError("meeting_id is required")
    ref = meeting_external_ref(meeting_id)
    node_title = (title or "").strip() or "Untitled meeting"
    conn = _open_db(collection_id)
    try:
        with conn:
            main_id = _main_chain_id(conn)
            target_chain = (chain_id or "").strip() or main_id
            ch = conn.execute(
                "SELECT chain_id FROM chains WHERE chain_id=?", (target_chain,)
            ).fetchone()
            if not ch:
                raise HTTPException(404, f"Chain '{target_chain}' not found")

            existing = conn.execute(
                "SELECT * FROM nodes WHERE external_ref=? AND chain_id=?",
                (ref, target_chain),
            ).fetchone()
            if existing:
                if (existing["title"] or "") != node_title:
                    conn.execute(
                        "UPDATE nodes SET title=?, version=version+1 WHERE node_id=?",
                        (node_title, existing["node_id"]),
                    )
                if event_time and not existing["event_time"]:
                    conn.execute(
                        "UPDATE nodes SET event_time=?, version=version+1 WHERE node_id=?",
                        (event_time, existing["node_id"]),
                    )
                return existing["node_id"]

            # Legacy: single global meeting ref (pre multi-chain) on this chain or any
            if target_chain == main_id:
                legacy = conn.execute(
                    "SELECT * FROM nodes WHERE external_ref=?", (ref,)
                ).fetchone()
                if legacy and legacy["chain_id"] == main_id:
                    if (legacy["title"] or "") != node_title:
                        conn.execute(
                            "UPDATE nodes SET title=?, version=version+1 WHERE node_id=?",
                            (node_title, legacy["node_id"]),
                        )
                    if event_time and not legacy["event_time"]:
                        conn.execute(
                            "UPDATE nodes SET event_time=?, version=version+1 WHERE node_id=?",
                            (event_time, legacy["node_id"]),
                        )
                    return legacy["node_id"]

            grp = conn.execute(
                "SELECT group_id FROM node_groups WHERE name=? LIMIT 1",
                ("Meeting",),
            ).fetchone()
            group_id = grp["group_id"] if grp else None

            max_row = conn.execute(
                'SELECT COALESCE(MAX("order"), 0) AS m FROM nodes WHERE chain_id=?',
                (target_chain,),
            ).fetchone()
            order = (max_row["m"] or 0) + 1
            node_id = uuid.uuid4().hex
            now = _now_iso()
            try:
                conn.execute(
                    """INSERT INTO nodes
                       (node_id, chain_id, group_id, node_type, title,
                        "order", event_time, created_by, created_at, version,
                        external_ref)
                       VALUES (?, ?, ?, 'event', ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        node_id,
                        target_chain,
                        group_id,
                        node_title,
                        order,
                        event_time,
                        _actor_id(),
                        now,
                        ref,
                    ),
                )
            except Exception:
                again = conn.execute(
                    "SELECT node_id FROM nodes WHERE external_ref=? AND chain_id=?",
                    (ref, target_chain),
                ).fetchone()
                if again:
                    return again["node_id"]
                raise

        emit_event(
            "node.created",
            collection_id,
            {"node_id": node_id, "external_ref": ref, "chain_id": target_chain},
        )
        return node_id
    finally:
        conn.close()


def get_node_by_external_ref(
    collection_id: str, external_ref: str, *, chain_id: str | None = None
) -> NodeOut | None:
    """Lookup a node by ``external_ref`` (e.g. ``meeting:{id}``).

    When *chain_id* is set, match that chain. Otherwise return the first match
    (prefer main chain if multiple).
    """
    ref = (external_ref or "").strip()
    if not ref:
        return None
    conn = _open_db(collection_id)
    try:
        if chain_id:
            row = conn.execute(
                "SELECT * FROM nodes WHERE external_ref=? AND chain_id=?",
                (ref, chain_id),
            ).fetchone()
            if not row:
                return None
            return _row_to_node(row)
        main_id = _main_chain_id(conn)
        rows = list(
            conn.execute(
                "SELECT * FROM nodes WHERE external_ref=?", (ref,)
            ).fetchall()
        )
        if not rows:
            return None
        for r in rows:
            if r["chain_id"] == main_id:
                return _row_to_node(r)
        return _row_to_node(rows[0])
    finally:
        conn.close()


def list_nodes_by_external_ref(
    collection_id: str, external_ref: str
) -> list[NodeOut]:
    """All nodes sharing an external_ref (e.g. meeting on multiple chains)."""
    ref = (external_ref or "").strip()
    if not ref:
        return []
    conn = _open_db(collection_id)
    try:
        rows = conn.execute(
            "SELECT * FROM nodes WHERE external_ref=?", (ref,)
        ).fetchall()
        return [_row_to_node(r) for r in rows]
    finally:
        conn.close()


def delete_meeting_anchor_if_empty(
    collection_id: str, meeting_id: str
) -> bool:
    """Delete meeting anchor node(s) with no remaining attachments.

    A meeting may have one node per chain; each empty node is removed.
    Returns True if at least one node was deleted.
    """
    if not meeting_id:
        return False
    ref = meeting_external_ref(meeting_id)
    conn = _open_db(collection_id)
    try:
        rows = conn.execute(
            "SELECT node_id FROM nodes WHERE external_ref=?", (ref,)
        ).fetchall()
        empty_ids: list[str] = []
        for row in rows:
            node_id = row["node_id"]
            has_files = conn.execute(
                "SELECT 1 FROM file_nodes WHERE node_id=? LIMIT 1", (node_id,)
            ).fetchone()
            if not has_files:
                empty_ids.append(node_id)
    finally:
        conn.close()

    if not empty_ids:
        return False

    deleted_any = False
    for node_id in empty_ids:
        try:
            delete_node(collection_id, node_id)
            logger.info(
                "Deleted empty meeting anchor node=%s ref=%s col=%s",
                node_id[:12],
                ref,
                collection_id,
            )
            deleted_any = True
        except Exception:
            logger.warning(
                "Failed deleting empty meeting anchor %s node=%s",
                ref,
                node_id[:12],
                exc_info=True,
            )
    return deleted_any





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
    _actor_for("chain.end", collection_id, node_id=node_id)
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
                   VALUES (?, ?, ?, 'end', ?, ?, ?, ?, ?, 1)""",
                (
                    merge_node_id,
                    parent_chain_id,
                    merge_group_id,
                    merge_title,
                    merge_order,
                    req.event_time,
                    _actor_id(),
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
                       VALUES (?, ?, ?, 0, ?)""",
                    (fid, merge_node_id, fr["current_version_id"], _actor_id()),
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
                       VALUES (?, 'node', ?, NULL, ?, 'user', ?, ?, NULL, NULL, 1)""",
                    (msg_id, merge_node_id, req.message_body.strip(), _actor_id(), now),
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


