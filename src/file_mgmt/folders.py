"""Folder CRUD."""

from __future__ import annotations

import json
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
from src.file_mgmt.models import (
    FolderArchiveToggle,
    FolderCreate,
    FolderOut,
    FolderTree,
    FolderUpdate,
)




def _coerce_locked_icons(
    kind: str, icon_color: str | None
) -> tuple[str, str, str | None]:
    """Plain → folder glyph; branch → git-branch. Color is kept."""
    if kind == "branch":
        return "lucide", "git-branch", icon_color
    return "lucide", "folder", icon_color


def _assert_group_icon_allowed(icon_value: str | None) -> None:
    key = (icon_value or "").strip().lower()
    if key in ("folder", "git-branch"):
        raise HTTPException(
            400, "Node group folders cannot use the folder or branch icon"
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
        from src.file_mgmt.store import _ensure_folders_archive_columns

        _ensure_folders_archive_columns(conn)
        conn.commit()
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

            icon_type, icon_value, icon_color = _coerce_locked_icons(
                "plain", req.icon_color
            )
            # No icon payload → leave columns null (kind default is folder)
            if not (req.icon_type or req.icon_value or req.icon_color):
                icon_type = icon_value = icon_color = None

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
                    icon_type,
                    icon_value,
                    icon_color,
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

            if "parent_folder_id" in updates and folder["kind"] != "plain":
                raise HTTPException(403, "Only plain folders can be moved")

            icon_keys = {"icon_type", "icon_value", "icon_color"}
            if icon_keys & set(updates.keys()):
                if folder["kind"] in ("plain", "branch"):
                    color = (
                        updates["icon_color"] if "icon_color" in updates else None
                    )
                    _t, _v, _c = _coerce_locked_icons(folder["kind"], color)
                    updates["icon_type"] = _t
                    updates["icon_value"] = _v
                    if "icon_color" in updates:
                        updates["icon_color"] = _c
                elif folder["kind"] == "user_group":
                    _assert_group_icon_allowed(updates.get("icon_value"))

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


def toggle_folder_archive(
    collection_id: str, folder_id: str, req: FolderArchiveToggle
) -> FolderOut:
    """Archive or restore a plain folder and its plain subtree."""
    _actor_for("folder.archive", collection_id, folder_id=folder_id)

    conn = _open_db(collection_id)
    try:
        with conn:
            from src.file_mgmt.store import _ensure_folders_archive_columns

            _ensure_folders_archive_columns(conn)
            folder = conn.execute(
                "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()
            if not folder:
                raise HTTPException(404, f"Folder '{folder_id}' not found")
            if folder["kind"] != "plain" or folder["is_system"]:
                raise HTTPException(400, "Only plain folders can be archived")
            if folder["version"] != req.version:
                raise HTTPException(
                    409, "Folder was modified by another user (version conflict)"
                )

            already = bool(folder["archived"])
            if req.archived == already:
                return _row_to_folder(folder)

            if req.archived:
                _archive_plain_folder(
                    conn, collection_id, folder_id, cascade=False
                )
                emit_event(
                    "folder.archived", collection_id, {"folder_id": folder_id}
                )
            else:
                _unarchive_plain_folder(
                    conn, collection_id, folder_id, as_root=True
                )
                emit_event(
                    "folder.unarchived", collection_id, {"folder_id": folder_id}
                )

            row = conn.execute(
                "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()
            return _row_to_folder(row)
    finally:
        conn.close()


def _archive_plain_folder(
    conn, collection_id: str, folder_id: str, *, cascade: bool
) -> None:
    from src.file_mgmt.files import (
        _archive_paths_on_folder,
        _promote_file_archive_if_needed,
    )

    row = conn.execute(
        "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
    ).fetchone()
    if not row:
        return
    if row["kind"] != "plain":
        return
    if bool(row["archived"]):
        return

    path_ids: list[str] = []
    promoted: list[str] = []
    already_file_archived: list[str] = []
    file_rows = conn.execute(
        "SELECT DISTINCT file_id FROM file_paths WHERE folder_id=?",
        (folder_id,),
    ).fetchall()
    for fr in file_rows:
        fid = fr["file_id"]
        was_file_archived = bool(
            conn.execute(
                "SELECT archived FROM files WHERE file_id=?", (fid,)
            ).fetchone()["archived"]
        )
        flipped = _archive_paths_on_folder(conn, fid, folder_id)
        path_ids.extend(flipped)
        if flipped and was_file_archived:
            already_file_archived.append(fid)
        if flipped and _promote_file_archive_if_needed(conn, collection_id, fid):
            promoted.append(fid)

    kids = conn.execute(
        "SELECT folder_id FROM folders WHERE parent_folder_id=?",
        (folder_id,),
    ).fetchall()
    for k in kids:
        _archive_plain_folder(conn, collection_id, k["folder_id"], cascade=True)

    snap = json.dumps(
        {
            "path_ids": path_ids,
            "promoted_file_ids": promoted,
            "already_file_archived": already_file_archived,
            "cascade": cascade,
        },
        separators=(",", ":"),
    )
    conn.execute(
        """UPDATE folders
           SET archived=1, archive_snapshot=?, updated_at=?, version=version+1
           WHERE folder_id=?""",
        (snap, _now_iso(), folder_id),
    )


def _unarchive_plain_folder(
    conn, collection_id: str, folder_id: str, *, as_root: bool
) -> None:
    from src.file_mgmt.files import (
        _path_has_active_mount,
        _unarchive_file_if_archived,
        _unarchive_paths_by_ids,
    )

    row = conn.execute(
        "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
    ).fetchone()
    if not row or row["kind"] != "plain":
        return

    snap_raw = None
    try:
        snap_raw = row["archive_snapshot"]
    except (KeyError, IndexError):
        snap_raw = None
    snap: dict = {}
    if snap_raw:
        try:
            snap = json.loads(snap_raw) or {}
        except (TypeError, json.JSONDecodeError):
            snap = {}

    is_cascade = bool(snap.get("cascade"))
    if bool(row["archived"]) and (as_root or is_cascade):
        path_ids = [p for p in (snap.get("path_ids") or []) if p]
        if path_ids:
            _unarchive_paths_by_ids(conn, path_ids)
        already = {
            fid
            for fid in (snap.get("already_file_archived") or [])
            if fid
        }
        file_ids: set[str] = {
            fid for fid in (snap.get("promoted_file_ids") or []) if fid
        }
        if path_ids:
            ph = ",".join("?" * len(path_ids))
            for r in conn.execute(
                f"SELECT DISTINCT file_id FROM file_paths WHERE path_id IN ({ph})",
                path_ids,
            ).fetchall():
                if r["file_id"]:
                    file_ids.add(r["file_id"])
        for fid in file_ids:
            if fid in already:
                continue
            if _path_has_active_mount(conn, fid):
                _unarchive_file_if_archived(conn, collection_id, fid)
        conn.execute(
            """UPDATE folders
               SET archived=0, archive_snapshot=NULL, updated_at=?,
                   version=version+1
               WHERE folder_id=?""",
            (_now_iso(), folder_id),
        )

    kids = conn.execute(
        "SELECT folder_id FROM folders WHERE parent_folder_id=?",
        (folder_id,),
    ).fetchall()
    for k in kids:
        _unarchive_plain_folder(
            conn, collection_id, k["folder_id"], as_root=False
        )


# === NodeGroup CRUD ===


