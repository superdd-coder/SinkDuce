"""ChatStreamHandler — wraps ChatboxAgent.chat_stream() as SSE event strings."""

from __future__ import annotations

import json
import logging
import math
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


def _sanitize_for_json(obj):
    """Recursively replace NaN/Infinity float values with None for JSON compat."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


class ChatStreamHandler:
    """Converts ChatboxAgent internal events into SSE-formatted strings."""

    def __init__(self, agent):
        self._agent = agent

    async def handle(
        self, session_id: str, user_message: str, *,
        thinking: bool = True, collections: list[str] | None = None,
        mode: str = "agentic",
        provider_id: str | None = None, model: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield SSE event strings for the frontend to consume."""
        try:
            async for event in self._agent.chat_stream(
                session_id, user_message, thinking=thinking, collections=collections,
                mode=mode, provider_id=provider_id, model=model,
            ):
                event_type = event.get("type", "unknown")
                try:
                    payload = json.dumps(event, ensure_ascii=False, default=str, allow_nan=False)
                except (TypeError, ValueError) as e:
                    logger.warning(
                        "Failed to serialize event type=%r: %s — retrying with nan/inf sanitization",
                        event_type, e,
                    )
                    try:
                        sanitized = _sanitize_for_json(event)
                        payload = json.dumps(sanitized, ensure_ascii=False, default=str)
                    except Exception:
                        logger.warning("Failed to serialize event type=%r even with sanitization, skipping", event_type)
                        continue
                except Exception:
                    logger.warning("Failed to serialize event type=%r, skipping", event_type)
                    continue
                yield f"event: {event_type}\ndata: {payload}\n\n"
        except Exception:
            logger.exception("Stream handler error for session %s", session_id)
            error_event = json.dumps(
                {"type": "error", "content": "Internal error, please retry"},
                ensure_ascii=False,
            )
            yield f"event: error\ndata: {error_event}\n\n"
