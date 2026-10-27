"""ChatStreamHandler — wraps ChatboxAgent.chat_stream() as SSE event strings."""

from __future__ import annotations

import json
import logging
import math
from typing import AsyncGenerator

from src.chatbox.note_changes import merge_note_changes

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
        web_search_enabled: bool = False,
        attachment_ids: list[str] | None = None,
        report_reviews: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield SSE event strings for the frontend to consume."""
        answer_parts: list[str] = []
        note_changes: list[dict] = []
        report_changes: list[dict] = []
        citations: list[dict] = []
        saw_done = False
        try:
            async for event in self._agent.chat_stream(
                session_id, user_message, thinking=thinking, collections=collections,
                mode=mode, provider_id=provider_id, model=model,
                web_search_enabled=web_search_enabled,
                **({"attachment_ids": attachment_ids} if attachment_ids else {}),
                **({"report_reviews": report_reviews} if report_reviews else {}),
            ):
                event_type = event.get("type", "unknown")
                if event_type == "token":
                    answer_parts.append(str(event.get("content") or ""))
                elif event_type == "done":
                    saw_done = True
                if event.get("note_changes"):
                    merge_note_changes(note_changes, event["note_changes"])
                if event.get("report_changes"):
                    from src.reports.tools import merge_report_changes
                    merge_report_changes(report_changes, event["report_changes"])
                if isinstance(event.get("citations"), list):
                    citations = event["citations"]
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
        finally:
            # Client gone mid-turn (refresh / stop / crash): the agent's own
            # persist only runs right before ``done``, so without this the
            # already-generated text would be lost. GeneratorExit is a
            # BaseException — the except above never swallows it, and the
            # persist here is sync-only, which is safe during generator close.
            if not saw_done:
                try:
                    self._agent.persist_interrupted_answer(
                        session_id, "".join(answer_parts),
                        **({"note_changes": note_changes} if note_changes else {}),
                        **({"citations": citations} if citations else {}),
                        **({"report_changes": report_changes} if report_changes else {}),
                    )
                except Exception:
                    logger.exception(
                        "Interrupted-answer persist failed for session %s", session_id
                    )
