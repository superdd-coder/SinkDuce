"""Chat / Quick Chat HITL gate for delete_todo.

Flow:
1. Agent is about to run ``delete_todo``
2. Stream emits ``todo_delete_confirm`` and waits on :class:`TodoDeleteConfirmStore`
3. Frontend POSTs approve/deny to ``/api/chat/todo-delete-confirm``
4. Allow → delete; Decline / timeout → no mutation
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from src.chatbox.confirm_store import ChatConfirmStore

logger = logging.getLogger(__name__)

TODO_DELETE_CONFIRM_TIMEOUT = 120.0


class TodoDeleteConfirmStore(ChatConfirmStore):
    """HITL gate store for delete_todo (id prefix ``tdc_``)."""

    def __init__(self) -> None:
        super().__init__("tdc_", TODO_DELETE_CONFIRM_TIMEOUT)

    def create(
        self,
        *,
        title: str,
        todo_id: str,
        collection: str,
        collection_name: str,
    ) -> str:
        return super().create(
            {
                "title": title,
                "todo_id": todo_id,
                "collection": collection,
                "collection_name": collection_name,
            }
        )


todo_delete_confirm_store = TodoDeleteConfirmStore()


def peek_todo_for_delete(collection: str, todo_id: str) -> dict[str, Any]:
    """Return title payload for the confirm card, or ``{error}``."""
    from src.collections.store import get_collection_meta
    from src.file_mgmt import service as fm

    col = (collection or "").strip()
    tid = (todo_id or "").strip()
    if not col:
        return {"error": "collection is required to create/update/delete a todo. Use list_collections to get an ID."}
    if not tid:
        return {"error": "todo_id is required"}
    try:
        todo = fm.get_todo(col, tid)
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            return {"error": str(detail.get("message") or detail)}
        return {"error": str(detail)}
    except Exception as exc:
        return {"error": str(exc)}
    meta = get_collection_meta(col) or {}
    return {
        "todo_id": todo.todo_id,
        "title": todo.title,
        "collection": col,
        "collection_name": str(meta.get("name") or col),
    }


def declined_payload(todo_id: str) -> dict[str, Any]:
    return {"error": "user_declined", "todo_id": todo_id}
