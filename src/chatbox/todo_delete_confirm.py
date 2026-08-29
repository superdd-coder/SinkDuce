"""Chat / Quick Chat HITL gate for delete_todo.

Flow:
1. Agent is about to run ``delete_todo``
2. Stream emits ``todo_delete_confirm`` and waits on :class:`TodoDeleteConfirmStore`
3. Frontend POSTs approve/deny to ``/api/chat/todo-delete-confirm``
4. Allow → delete; Decline / timeout → no mutation
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

TODO_DELETE_CONFIRM_TIMEOUT = 120.0


@dataclass
class _PendingConfirm:
    title: str
    todo_id: str
    collection: str
    collection_name: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None


class TodoDeleteConfirmStore:
    """In-process store bridging SSE streams and confirm HTTP calls."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingConfirm] = {}
        self._guard = threading.Lock()

    def create(
        self,
        *,
        title: str,
        todo_id: str,
        collection: str,
        collection_name: str,
    ) -> str:
        confirm_id = f"tdc_{uuid.uuid4().hex[:16]}"
        with self._guard:
            self._pending[confirm_id] = _PendingConfirm(
                title=title,
                todo_id=todo_id,
                collection=collection,
                collection_name=collection_name,
            )
        return confirm_id

    def resolve(self, confirm_id: str, approved: bool) -> bool:
        with self._guard:
            item = self._pending.get(confirm_id)
        if item is None:
            return False
        item.approved = bool(approved)
        item.event.set()
        return True

    async def wait(self, confirm_id: str, timeout: float = TODO_DELETE_CONFIRM_TIMEOUT) -> bool:
        with self._guard:
            item = self._pending.get(confirm_id)
        if item is None:
            return False
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(
            None, lambda: item.event.wait(timeout=timeout)
        )
        with self._guard:
            self._pending.pop(confirm_id, None)
        if not ok:
            logger.info("Todo delete confirm timed out: %s", confirm_id)
            return False
        return bool(item.approved)

    def cancel(self, confirm_id: str) -> None:
        with self._guard:
            item = self._pending.pop(confirm_id, None)
        if item is not None:
            item.approved = False
            item.event.set()


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
