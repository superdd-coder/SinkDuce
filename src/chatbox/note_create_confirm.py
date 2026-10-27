"""Chat HITL gate for create_note.

Flow (mirrors ``todo_delete_confirm``):
1. Agent calls ``create_note`` with a target the user did not clearly name
   (or a fuzzy-repaired name).
2. Stream emits ``note_create_confirm`` and waits on the store.
3. Frontend POSTs approve/deny to ``/api/chat/note-create-confirm``.
4. Approve → note is created; Decline / timeout → ``user_declined``.

Quick Chat never confirms (the collection is locked by the surface itself).
"""

from __future__ import annotations

import logging
from typing import Any

from src.chatbox.collection_resolve import resolve_collection
from src.chatbox.confirm_store import ChatConfirmStore

logger = logging.getLogger(__name__)

NOTE_CREATE_CONFIRM_TIMEOUT = 120.0

# Safety cap for the note body carried on the confirm card / SSE event.
# Practically unbounded for real notes — the frontend renders it as scrollable
# markdown, so the user sees the whole thing.
_PREVIEW_CHARS = 8000


class NoteCreateConfirmStore(ChatConfirmStore):
    """HITL gate store for create_note (id prefix ``ncc_``)."""


note_create_confirm_store = NoteCreateConfirmStore(
    "ncc_", NOTE_CREATE_CONFIRM_TIMEOUT
)


def note_create_needs_confirm(
    *,
    is_quick: bool,
    match: str,
    collection_from_user: bool,
) -> bool:
    """Decide whether creating the note requires a user confirmation card.

    - Quick Chat: never (surface-locked collection).
    - Fuzzy (typo-repaired) match: always — the repair could point at the
      wrong collection even when the user named one.
    - Otherwise: only when the agent (not the user) chose the target.
    """
    if is_quick:
        return False
    if match == "fuzzy":
        return True
    return not collection_from_user


def user_named_collection(collection_name: str, user_message: str) -> bool:
    """Whether the resolved collection's display name appears in the user's
    current message.

    Host-side backstop for ``collection_from_user``: weak models set the
    flag=true and guess a target (a collection literally named "default" is
    a magnet). The note may only skip the card when the user's own words
    contain the target name.
    """
    name = (collection_name or "").strip().casefold()
    return bool(name) and name in (user_message or "").casefold()


def peek_note_for_create(collection_arg: str, title: str, content: str) -> dict[str, Any]:
    """Resolve the target collection for the confirm card, or ``{error}``.

    ``{error}`` results carry the full directory so the caller can fall
    through to plain execution and let the model self-correct.
    """
    resolved = resolve_collection(collection_arg)
    if "error" in resolved:
        return resolved
    return {
        "collection": resolved["collection"],
        "collection_name": resolved["collection_name"],
        "match": resolved["match"],
        "title": title,
        "preview": (content or "")[:_PREVIEW_CHARS],
    }


def declined_payload() -> dict[str, Any]:
    return {"error": "user_declined"}
