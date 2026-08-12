"""Event emission layer for file management.

MVP: no-op (debug log only). v2 will hook WebSocket broadcast.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("file_mgmt.events")


def emit_event(
    event_type: str,
    collection_id: str,
    payload: dict,
    user_id: str | None = None,
) -> None:
    """Emit a file-management event.

    MVP implementation: no-op (debug log only).
    v2: hook WebSocket broadcast listener.
    """
    if user_id is None:
        from src.identity import get_actor

        user_id = get_actor().id
    logger.debug(
        "event: %s collection=%s user=%s",
        event_type,
        collection_id,
        user_id,
    )


# Event type constants (for reference / type-checking in later phases)
EVENT_TYPES = frozenset({
    "folder.created", "folder.renamed", "folder.moved", "folder.deleted",
    "group.created", "group.renamed", "group.deleted",
    "chain.created", "chain.renamed", "chain.deleted", "chain.reopened",
    "node.created", "node.updated", "node.deleted", "node.reordered",
    "file.uploaded", "file.updated", "file.archived", "file.unarchived", "file.deleted",
    "file.version_deleted", "file.version_rolled_back",
    "file_path.added", "file_path.removed", "file_path.promoted",
    "message.created", "message.updated", "message.deleted",
    "archive.toggled",
})
