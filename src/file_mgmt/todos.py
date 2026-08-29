"""Collection To-do list CRUD (not timeline nodes)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import HTTPException

from src.file_mgmt.access import _actor_for, _main_chain_id, _now_iso, _open_db
from src.file_mgmt.events import emit_event
from src.file_mgmt.models import TodoCreate, TodoLinkNode, TodoOut, TodoUpdate

logger = logging.getLogger("file_mgmt.service")


def _sort_todo_rows(rows: list) -> list:
    open_dated: list = []
    open_undated: list = []
    completed: list = []
    for r in rows:
        if int(r["done"] or 0):
            completed.append(r)
        elif r["ddl"]:
            open_dated.append(r)
        else:
            open_undated.append(r)
    open_dated.sort(key=lambda r: (r["ddl"] or "", r["todo_id"] or ""))
    open_undated.sort(key=lambda r: (-_ts_key(r["created_at"]), r["todo_id"] or ""))
    completed.sort(key=lambda r: (-_ts_key(r["completed_at"]), r["todo_id"] or ""))
    return open_dated + open_undated + completed


def _ts_key(iso: str | None) -> float:
    """Rough sortable key from ISO string; missing → 0."""
    if not iso:
        return 0.0
    try:
        s = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _row_to_todo(row, conn) -> TodoOut:
    main_id = _main_chain_id(conn)
    stored_chain = row["target_chain_id"]
    resolved = stored_chain or main_id
    is_main = resolved == main_id
    chain_title = ""
    ch = conn.execute(
        "SELECT title, parent_chain_id FROM chains WHERE chain_id=?",
        (resolved,),
    ).fetchone()
    if ch:
        if ch["parent_chain_id"] is None:
            chain_title = ch["title"] or "Main"
            is_main = True
        else:
            chain_title = ch["title"] or ""
            is_main = False
    elif stored_chain:
        resolved = main_id
        is_main = True
        chain_title = "Main"
    else:
        chain_title = "Main"

    completed_node_id = row["completed_node_id"]
    if completed_node_id:
        exists = conn.execute(
            "SELECT 1 FROM nodes WHERE node_id=?", (completed_node_id,)
        ).fetchone()
        if not exists:
            completed_node_id = None

    keys = set(row.keys()) if hasattr(row, "keys") else set()
    body = row["body"] if "body" in keys else None

    def _opt(col: str) -> str | None:
        if col not in keys:
            return None
        v = row[col]
        return (str(v).strip() or None) if v is not None else None

    return TodoOut(
        todo_id=row["todo_id"],
        title=row["title"],
        body=body,
        done=bool(row["done"]),
        ddl=row["ddl"],
        target_chain_id=row["target_chain_id"],
        chain_id=resolved,
        chain_title=chain_title,
        is_main_chain=is_main,
        completed_node_id=completed_node_id,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        source_meeting_id=_opt("source_meeting_id"),
        source_section_tab_id=_opt("source_section_tab_id"),
        source_candidate_id=_opt("source_candidate_id"),
        assignee_person_id=_opt("assignee_person_id"),
    )


def _ensure_assignee(todo: TodoOut, conn) -> TodoOut:
    if todo.assignee_person_id or not todo.source_meeting_id:
        return todo
    try:
        from src.speakers.service import resolve_assignee_person_id

        pid = resolve_assignee_person_id(
            meeting_id=todo.source_meeting_id,
            candidate_id=todo.source_candidate_id,
        )
    except Exception:
        return todo
    if not pid:
        return todo
    try:
        conn.execute(
            "UPDATE todos SET assignee_person_id=? WHERE todo_id=?",
            (pid, todo.todo_id),
        )
        conn.commit()
    except Exception:
        logger.debug("todo assignee backfill skipped", exc_info=True)
    return todo.model_copy(update={"assignee_person_id": pid})


def list_todos(
    collection_id: str,
    *,
    done: bool | None = None,
    chain_id: str | None = None,
) -> list[TodoOut]:
    from src.file_mgmt.store import _ensure_todos_table

    conn = _open_db(collection_id)
    try:
        _ensure_todos_table(conn)
        conn.commit()
        sql = "SELECT * FROM todos WHERE 1=1"
        params: list = []
        if done is not None:
            sql += " AND done=?"
            params.append(1 if done else 0)
        if chain_id is not None:
            main_id = _main_chain_id(conn)
            if chain_id == main_id:
                sql += " AND (target_chain_id IS NULL OR target_chain_id=?)"
                params.append(main_id)
            else:
                sql += " AND target_chain_id=?"
                params.append(chain_id)
        rows = list(conn.execute(sql, params).fetchall())
        rows = _sort_todo_rows(rows)
        return [_ensure_assignee(_row_to_todo(r, conn), conn) for r in rows]
    finally:
        conn.close()


def create_todo(collection_id: str, req: TodoCreate) -> TodoOut:
    from src.file_mgmt.store import _ensure_todos_table

    _actor_for("todo.create", collection_id)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(400, "Todo title is required")

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_todos_table(conn)
            target = req.target_chain_id
            if target:
                ch = conn.execute(
                    "SELECT chain_id FROM chains WHERE chain_id=?", (target,)
                ).fetchone()
                if not ch:
                    raise HTTPException(404, f"Chain '{target}' not found")
            main_id = _main_chain_id(conn)
            if target == main_id:
                target = None

            todo_id = uuid.uuid4().hex
            now = _now_iso()
            body = (req.body or "").strip() or None
            src_m = (getattr(req, "source_meeting_id", None) or "").strip() or None
            src_t = (getattr(req, "source_section_tab_id", None) or "").strip() or None
            src_c = (getattr(req, "source_candidate_id", None) or "").strip() or None
            assignee = (getattr(req, "assignee_person_id", None) or "").strip() or None
            if not assignee and src_m:
                try:
                    from src.speakers.service import resolve_assignee_person_id

                    assignee = resolve_assignee_person_id(
                        meeting_id=src_m, candidate_id=src_c
                    )
                except Exception:
                    logger.debug("todo assignee resolve skipped", exc_info=True)
            conn.execute(
                """INSERT INTO todos
                   (todo_id, title, body, done, ddl, target_chain_id, completed_node_id,
                    sort_order, created_at, updated_at, completed_at,
                    source_meeting_id, source_section_tab_id, source_candidate_id,
                    assignee_person_id)
                   VALUES (?, ?, ?, 0, ?, ?, NULL, NULL, ?, ?, NULL, ?, ?, ?, ?)""",
                (
                    todo_id,
                    title,
                    body,
                    req.ddl,
                    target,
                    now,
                    now,
                    src_m,
                    src_t,
                    src_c,
                    assignee,
                ),
            )
            row = conn.execute(
                "SELECT * FROM todos WHERE todo_id=?", (todo_id,)
            ).fetchone()
        out = _row_to_todo(row, conn)
        emit_event("todo.created", collection_id, {"todo_id": todo_id})
        # Consume smart-suggestion after successful create
        sid = (getattr(req, "suggestion_id", None) or "").strip()
        if sid:
            try:
                from src.file_mgmt.todo_suggestions import consume_suggestion

                consume_suggestion(collection_id, out.chain_id, sid)
            except Exception:
                logger.debug(
                    "consume suggestion failed todo=%s sid=%s",
                    todo_id,
                    sid,
                    exc_info=True,
                )
        return out
    finally:
        conn.close()


def update_todo(collection_id: str, todo_id: str, req: TodoUpdate) -> TodoOut:
    from src.file_mgmt.store import _ensure_todos_table

    _actor_for("todo.update", collection_id, todo_id=todo_id)

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_todos_table(conn)
            row = conn.execute(
                "SELECT * FROM todos WHERE todo_id=?", (todo_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, f"Todo '{todo_id}' not found")

            updates: dict = {}
            data = req.model_dump(exclude_unset=True)

            # Content edits blocked when completed (done flip still allowed)
            content_keys = {
                "title", "body", "ddl", "target_chain_id",
                "assignee_person_id",
                "clear_ddl", "clear_chain", "clear_body", "clear_assignee",
            }
            if bool(row["done"]) and any(
                (k in data and data[k] is not None and data[k] is not False)
                or (k in data and k.startswith("clear_") and data[k])
                for k in content_keys
            ):
                # allow only pure done=false reopen without other fields, or done alone
                only_done = set(data.keys()) <= {"done"}
                if not only_done:
                    raise HTTPException(
                        400, "Completed todos cannot be edited"
                    )

            if "title" in data and data["title"] is not None:
                t = str(data["title"]).strip()
                if not t:
                    raise HTTPException(400, "Todo title is required")
                updates["title"] = t

            if data.get("clear_body"):
                updates["body"] = None
            elif "body" in data:
                b = data["body"]
                updates["body"] = (str(b).strip() or None) if b is not None else None

            if data.get("clear_ddl"):
                updates["ddl"] = None
            elif "ddl" in data:
                updates["ddl"] = data["ddl"]

            if data.get("clear_chain"):
                updates["target_chain_id"] = None
            elif "target_chain_id" in data:
                tc = data["target_chain_id"]
                if tc:
                    ch = conn.execute(
                        "SELECT chain_id FROM chains WHERE chain_id=?", (tc,)
                    ).fetchone()
                    if not ch:
                        raise HTTPException(404, f"Chain '{tc}' not found")
                    main_id = _main_chain_id(conn)
                    updates["target_chain_id"] = None if tc == main_id else tc
                else:
                    updates["target_chain_id"] = None

            if data.get("clear_assignee"):
                updates["assignee_person_id"] = None
            elif "assignee_person_id" in data and data["assignee_person_id"] is not None:
                aid = str(data["assignee_person_id"]).strip()
                updates["assignee_person_id"] = aid or None

            if "done" in data and data["done"] is not None:
                new_done = bool(data["done"])
                was_done = bool(row["done"])
                updates["done"] = 1 if new_done else 0
                if new_done and not was_done:
                    updates["completed_at"] = _now_iso()
                elif not new_done and was_done:
                    updates["completed_at"] = None

            if not updates:
                return _row_to_todo(row, conn)

            updates["updated_at"] = _now_iso()
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE todos SET {sets} WHERE todo_id=?",
                (*updates.values(), todo_id),
            )
            row = conn.execute(
                "SELECT * FROM todos WHERE todo_id=?", (todo_id,)
            ).fetchone()

        emit_event("todo.updated", collection_id, {"todo_id": todo_id})
        return _row_to_todo(row, conn)
    finally:
        conn.close()


def get_todo(collection_id: str, todo_id: str) -> TodoOut:
    from src.file_mgmt.store import _ensure_todos_table

    conn = _open_db(collection_id)
    try:
        _ensure_todos_table(conn)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM todos WHERE todo_id=?", (todo_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Todo '{todo_id}' not found")
        return _ensure_assignee(_row_to_todo(row, conn), conn)
    finally:
        conn.close()


def delete_todo(collection_id: str, todo_id: str) -> None:
    from src.file_mgmt.store import _ensure_todos_table

    _actor_for("todo.delete", collection_id, todo_id=todo_id)

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_todos_table(conn)
            row = conn.execute(
                "SELECT todo_id FROM todos WHERE todo_id=?", (todo_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, f"Todo '{todo_id}' not found")
            conn.execute("DELETE FROM todos WHERE todo_id=?", (todo_id,))
        emit_event("todo.deleted", collection_id, {"todo_id": todo_id})
    finally:
        conn.close()


def link_todo_node(
    collection_id: str, todo_id: str, req: TodoLinkNode
) -> TodoOut:
    from src.file_mgmt.store import _ensure_todos_table

    conn = _open_db(collection_id)
    try:
        with conn:
            _ensure_todos_table(conn)
            row = conn.execute(
                "SELECT * FROM todos WHERE todo_id=?", (todo_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, f"Todo '{todo_id}' not found")
            node = conn.execute(
                "SELECT node_id FROM nodes WHERE node_id=?", (req.node_id,)
            ).fetchone()
            if not node:
                raise HTTPException(404, f"Node '{req.node_id}' not found")
            now = _now_iso()
            if not row["done"]:
                conn.execute(
                    """UPDATE todos
                       SET completed_node_id=?, done=1, completed_at=COALESCE(completed_at, ?),
                           updated_at=?
                       WHERE todo_id=?""",
                    (req.node_id, now, now, todo_id),
                )
            else:
                conn.execute(
                    """UPDATE todos
                       SET completed_node_id=?, updated_at=?
                       WHERE todo_id=?""",
                    (req.node_id, now, todo_id),
                )
            row = conn.execute(
                "SELECT * FROM todos WHERE todo_id=?", (todo_id,)
            ).fetchone()
        emit_event(
            "todo.linked_node",
            collection_id,
            {"todo_id": todo_id, "node_id": req.node_id},
        )
        return _row_to_todo(row, conn)
    finally:
        conn.close()
