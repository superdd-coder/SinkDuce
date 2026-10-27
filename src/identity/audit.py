"""Thin audit hook. Collection events go through file_mgmt.emit_event."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def audit(
    event_type: str,
    *,
    collection_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    if collection_id:
        from src.file_mgmt.events import emit_event

        emit_event(event_type, collection_id, payload or {})
        return
    logger.debug("event: %s", event_type)
