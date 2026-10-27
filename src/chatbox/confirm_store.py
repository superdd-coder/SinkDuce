"""Generic in-process HITL confirm store bridging SSE streams and confirm HTTP calls.

Shared by ``todo_delete_confirm`` (delete_todo) and ``note_create_confirm``
(create_note). Each surface owns one instance with its own id prefix so
confirm ids are self-describing in logs and 404 messages.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _PendingConfirm:
    payload: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None
    # Optional decision detail carried from the HTTP resolve back to the
    # waiting stream (e.g. note-create card: user-picked collection id).
    override: str | None = None


class ChatConfirmStore:
    """create() → confirm_id; resolve() from HTTP; wait() inside the stream."""

    def __init__(self, prefix: str, default_timeout: float = 120.0) -> None:
        self._prefix = prefix
        self._default_timeout = default_timeout
        self._pending: dict[str, _PendingConfirm] = {}
        self._guard = threading.Lock()

    def create(self, payload: dict[str, Any]) -> str:
        confirm_id = f"{self._prefix}{uuid.uuid4().hex[:16]}"
        with self._guard:
            self._pending[confirm_id] = _PendingConfirm(payload=dict(payload))
        return confirm_id

    def resolve(
        self, confirm_id: str, approved: bool, override: str | None = None
    ) -> bool:
        with self._guard:
            item = self._pending.get(confirm_id)
        if item is None:
            return False
        item.approved = bool(approved)
        if override:
            item.override = override
        item.event.set()
        return True

    async def wait_result(
        self, confirm_id: str, timeout: float | None = None
    ) -> tuple[bool, str | None]:
        """Wait for the decision; returns ``(approved, override)``."""
        if timeout is None:
            timeout = self._default_timeout
        with self._guard:
            item = self._pending.get(confirm_id)
        if item is None:
            return False, None
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(
            None, lambda: item.event.wait(timeout=timeout)
        )
        with self._guard:
            self._pending.pop(confirm_id, None)
        if not ok:
            logger.info("Confirm %s timed out", confirm_id)
            return False, None
        return bool(item.approved), item.override

    async def wait(self, confirm_id: str, timeout: float | None = None) -> bool:
        approved, _ = await self.wait_result(confirm_id, timeout)
        return approved

    def cancel(self, confirm_id: str) -> None:
        with self._guard:
            item = self._pending.pop(confirm_id, None)
        if item is not None:
            item.approved = False
            item.event.set()
