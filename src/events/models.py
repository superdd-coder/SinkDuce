"""Event envelope and the domain resolution table.

The `domain` field is the frontend's dispatch key. It is resolved through an
explicit table rather than by naive first-segment derivation, because several
event families share a first segment but belong to different domains
(`meeting_group.*` → meetings, `person.*` → people, `session.message_*` →
sessions while `message.*` is a file-management collection message).
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """One change notification. `seq` is assigned by the bus on publish."""

    seq: int = 0
    ts: float
    type: str
    domain: str
    scope: dict[str, Any] = Field(default_factory=dict)
    origin: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Domain table — the single authority for type → domain ───────
#
# Adding an event type means adding it here. tests/test_event_bus.py pins the
# mapping for every declared type, so a typo fails the suite rather than
# silently routing to the wrong frontend surface.

_FILES = "files"
_TIMELINE = "timeline"
_MEETINGS = "meetings"
_SESSIONS = "sessions"
_NOTES = "notes"
_COLLECTIONS = "collections"
_HOT_WORDS = "hot_words"
_PEOPLE = "people"
_CONFIG = "config"
_TASKS = "tasks"

DOMAIN_BY_TYPE: dict[str, str] = {
    # file_mgmt: folders / files / paths (already emitted by src/file_mgmt/*)
    "folder.created": _FILES,
    "folder.renamed": _FILES,
    "folder.moved": _FILES,
    "folder.deleted": _FILES,
    "file.uploaded": _FILES,
    "file.updated": _FILES,
    "file.archived": _FILES,
    "file.unarchived": _FILES,
    "file.deleted": _FILES,
    "file.version_deleted": _FILES,
    "file.version_rolled_back": _FILES,
    "file_path.added": _FILES,
    "file_path.removed": _FILES,
    "file_path.promoted": _FILES,
    "archive.toggled": _FILES,
    # file_mgmt: collection messages (distinct from chat messages)
    "message.created": _FILES,
    "message.updated": _FILES,
    "message.deleted": _FILES,
    # file_mgmt: timeline / groups / chains / nodes
    "group.created": _TIMELINE,
    "group.renamed": _TIMELINE,
    "group.deleted": _TIMELINE,
    "chain.created": _TIMELINE,
    "chain.renamed": _TIMELINE,
    "chain.deleted": _TIMELINE,
    "chain.reopened": _TIMELINE,
    "node.created": _TIMELINE,
    "node.updated": _TIMELINE,
    "node.deleted": _TIMELINE,
    "node.reordered": _TIMELINE,
    # chat sessions and chat messages
    "session.created": _SESSIONS,
    "session.updated": _SESSIONS,
    "session.deleted": _SESSIONS,
    "session.message_created": _SESSIONS,
    # meetings and meeting groups
    "meeting.created": _MEETINGS,
    "meeting.updated": _MEETINGS,
    "meeting.deleted": _MEETINGS,
    "meeting.archived": _MEETINGS,
    "meeting.unarchived": _MEETINGS,
    "meeting.section_updated": _MEETINGS,
    "meeting.transcription_started": _MEETINGS,
    "meeting.generation_started": _MEETINGS,
    "meeting_group.created": _MEETINGS,
    "meeting_group.updated": _MEETINGS,
    "meeting_group.deleted": _MEETINGS,
    # notes
    "note.created": _NOTES,
    "note.updated": _NOTES,
    "note.deleted": _NOTES,
    "note.distilled": _NOTES,
    "note.propagation_started": _NOTES,
    "note.ingest_started": _NOTES,
    # collections
    "collection.created": _COLLECTIONS,
    "collection.deleted": _COLLECTIONS,
    "collection.archived": _COLLECTIONS,
    "collection.unarchived": _COLLECTIONS,
    "collection.renamed": _COLLECTIONS,
    "collection.updated": _COLLECTIONS,
    "collection.config_updated": _COLLECTIONS,
    # hot words
    "hot_words.library_created": _HOT_WORDS,
    "hot_words.library_updated": _HOT_WORDS,
    "hot_words.library_deleted": _HOT_WORDS,
    "hot_words.default_changed": _HOT_WORDS,
    "hot_words.pins_changed": _HOT_WORDS,
    # people / speakers
    "person.created": _PEOPLE,
    "person.updated": _PEOPLE,
    "person.deleted": _PEOPLE,
    "person.profile_started": _PEOPLE,
    # config
    "config.updated": _CONFIG,
    # tasks
    "task.queued": _TASKS,
    "task.cancelled": _TASKS,
    "task.retried": _TASKS,
    "task.cleared": _TASKS,
}

_FALLBACK_DOMAIN = "unknown"


def resolve_domain(event_type: str) -> str:
    """Map an event type to its frontend dispatch domain.

    Falls back to "unknown" for a type missing from the table: an unregistered
    type degrades to "no surface refetched" rather than routing somewhere wrong,
    and tests/test_event_bus.py asserts no declared type hits the fallback.
    """
    return DOMAIN_BY_TYPE.get(event_type, _FALLBACK_DOMAIN)


def make_event(
    event_type: str,
    *,
    scope: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    origin: dict[str, Any] | None = None,
    ts: float | None = None,
) -> EventEnvelope:
    """Build an envelope with domain resolved and seq left for the bus to assign."""
    return EventEnvelope(
        ts=time.time() if ts is None else ts,
        type=event_type,
        domain=resolve_domain(event_type),
        scope=scope or {},
        origin=origin or {},
        payload=payload or {},
    )
