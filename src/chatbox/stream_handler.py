"""ChatStreamHandler — wraps ChatboxAgent.chat_stream() as SSE event strings."""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


class ChatStreamHandler:
    """Converts ChatboxAgent internal events into SSE-formatted strings."""

    def __init__(self, agent):
        self._agent = agent

    async def handle(
        self, session_id: str, user_message: str, *,
        thinking: bool = True, collections: list[str] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield SSE event strings for the frontend to consume."""
        try:
            async for event in self._agent.chat_stream(
                session_id, user_message, thinking=thinking, collections=collections,
            ):
                event_type = event.get("type", "unknown")
                payload = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {payload}\n\n"
        except Exception:
            logger.exception("Stream handler error for session %s", session_id)
            error_event = json.dumps(
                {"type": "error", "content": "Internal error, please retry"},
                ensure_ascii=False,
            )
            yield f"event: error\ndata: {error_event}\n\n"
