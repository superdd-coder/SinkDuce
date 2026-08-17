"""Folder CRUD."""

from __future__ import annotations

import uuid

from fastapi import HTTPException

from src.file_mgmt.access import _actor_for, _now_iso, _open_db
from src.file_mgmt.events import emit_event
from src.file_mgmt.layout import (
    _assert_folder_name_free,
    _delete_folder_subtree,
    _is_descendant,
    _row_to_folder,
)
from src.file_mgmt.models import FolderCreate, FolderOut, FolderTree, FolderUpdate




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

        # Direct-file latest version time per folder (for content_updated_at)
        direct_file_updated: dict[str, str] = {}
        for r in conn.execute(
            """
            SELECT fp.folder_id AS folder_id,
                   MAX(fv.created_at) AS max_ts
            FROM file_paths fp
            JOIN files f ON f.file_id = fp.file_id
            JOIN file_versions fv ON fv.version_id = f.current_version_id
            WHERE fp.folder_id IS NOT NULL
            GROUP BY fp.folder_id
            """
        ).fetchall():
            if r["folder_id"] and r["max_ts"]:
                direct_file_updated[r["folder_id"]] = r["max_ts"]

        def _max_ts(*vals: str | None) -> str:
            best = ""
            for v in vals:
                s = (v or "").strip()
                if s and s > best:
                    best = s
            return best

        def build(row) -> FolderTree:
            kids = [build(c) for c in children_map.get(row["folder_id"], [])]
            base = _row_to_folder(row)
            fid = row["folder_id"]
            cnt = conn.execute(
                "SELECT COUNT(DISTINCT file_id) FROM file_paths WHERE folder_id=?",
                (fid,),
            ).fetchone()[0]
            # content_updated_at = max(direct files, nested folders, folder.updated_at)
            content_ts = direct_file_updated.get(fid, "")
            for k in kids:
                content_ts = _max_ts(content_ts, k.content_updated_at)
            content_ts = _max_ts(content_ts, base.updated_at, base.created_at)
            return FolderTree(
                **base.model_dump(),
                children=kids,
                file_count=cnt,
                content_updated_at=content_ts,
            )

        roots = children_map.get(None, [])
        return [build(r) for r in roots]
    finally:
        conn.close()


def create_folder(collection_id: str, req: FolderCreate) -> FolderOut:
    actor = _actor_for("folder.create", collection_id)
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
                   VALUES (?, ?, ?, 'plain', 0, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    folder_id,
                    parent_id,
                    name,
                    actor.id,
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
    _actor_for("folder.update", collection_id, folder_id=folder_id)
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
    _actor_for("folder.delete", collection_id, folder_id=folder_id)
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


