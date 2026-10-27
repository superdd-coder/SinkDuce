"""Message CRUD and folder/root message streams."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import HTTPException

from src.file_mgmt.access import _actor_for, _actor_id, _now_iso, _open_db
from src.file_mgmt.events import emit_event
from src.file_mgmt.models import MessageCreate, MessageOut, MessageUpdate

logger = logging.getLogger("file_mgmt.service")


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

def create_message(collection_id: str, req: MessageCreate) -> MessageOut:
    from src.file_mgmt.models import MessageCreate, MessageOut

    _actor_for("message.create", collection_id)
    conn = _open_db(collection_id)
    try:
        with conn:
            now = _now_iso()
            message_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO messages
                   (message_id, owner_type, owner_id, source_node_id, body,
                    author_type, author_id, created_at, edited_at, edited_by, version)
                   VALUES (?, ?, ?, ?, ?, 'user', ?, ?, NULL, NULL, 1)""",
                (
                    message_id,
                    req.owner_type,
                    req.owner_id,
                    req.source_node_id,
                    req.body,
                    _actor_id(),
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
        if (req.owner_type or "").strip().lower() == "node":
            try:
                from src.file_mgmt.todo_suggestions import schedule_for_node

                schedule_for_node(collection_id, req.owner_id)
            except Exception:
                logger.debug("todo suggestion schedule after create_message failed", exc_info=True)
        return _row_to_message(row, conn)
    finally:
        conn.close()


def update_message(collection_id: str, message_id: str, req: MessageUpdate) -> MessageOut:
    from src.file_mgmt.models import MessageUpdate, MessageOut

    _actor_for("message.update", collection_id, message_id=message_id)
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
                   SET body=?, edited_at=?, edited_by=?, version=version+1
                   WHERE message_id=? AND version=?""",
                (body, now, _actor_id(), message_id, req.version),
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
        if owner_type == "node":
            try:
                from src.file_mgmt.todo_suggestions import schedule_for_node

                schedule_for_node(collection_id, msg["owner_id"])
            except Exception:
                logger.debug("todo suggestion schedule after update_message failed", exc_info=True)
        return _row_to_message(row, conn)
    finally:
        conn.close()


def delete_message(collection_id: str, message_id: str) -> None:
    _actor_for("message.delete", collection_id, message_id=message_id)
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
        if (msg["owner_type"] or "").strip().lower() == "node":
            try:
                from src.file_mgmt.todo_suggestions import schedule_for_node

                schedule_for_node(collection_id, msg["owner_id"])
            except Exception:
                logger.debug("todo suggestion schedule after delete_message failed", exc_info=True)
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
