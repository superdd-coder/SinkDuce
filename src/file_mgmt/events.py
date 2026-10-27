"""Event emission layer for file management.

The public `emit_event` signature is unchanged from the original no-op MVP, so
every existing call site in files.py / timeline.py / messages.py is untouched.
The body now publishes to the process-wide event bus
(development_doc/2026-09-16-multi-device-state-sync-design.md §5).

Emission is synchronous and safe from any thread: the bus is queue-based and
publish() takes no event loop, so calls from the event loop, Starlette's
sync-route threadpool, and detached worker threads are all valid.
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
    """Publish a file-management event for `collection_id`.

    `user_id` is retained for call-site compatibility; the authoritative
    attribution is the request-scoped client context (client id / pairing key),
    which background writes do not have — correctly yielding a bare origin so
    server-side completions reach every client.
    """
    if user_id is None:
        from src.identity import get_actor

        user_id = get_actor().id

    # Imported at call time so tests can swap the process-wide bus.
    from src.events.bus import bus
    from src.events.context import current_client
    from src.events.models import make_event

    event = make_event(
        event_type,
        scope={"collection_id": collection_id},
        payload=payload,
        origin=current_client().as_origin(),
    )
    bus.publish(event)
    logger.debug(
        "event: %s collection=%s user=%s seq=%s",
        event_type,
        collection_id,
        user_id,
        event.seq,
    )


def emit_ingest_queued(
    collection_id: str,
    *,
    file_id: str | None,
    task_id: str | None,
    filename: str | None = None,
) -> None:
    """Announce that an ingest task was enqueued for `file_id`.

    Called from every ingest-enqueueing upload path (folder upload, new version).
    Peers use this to draw the Ingesting lock: the lock is a projection of server
    task state, so without this signal a device that did not start the upload sees
    the new file but no spinner.

    `file_id` keys the lock; a task without one (should not happen for uploads)
    still emits, so the peer can refresh, but cannot be keyed to a row.

    Multi-device sync: development_doc/2026-09-16-multi-device-state-sync-design.md §9.
    """
    emit_event(
        "task.queued",
        collection_id,
        {"task_id": task_id, "file_id": file_id, "filename": filename},
    )


# Event type catalog (for reference / type-checking). Every type listed here must
# resolve to a real domain in src/events/models.py DOMAIN_BY_TYPE — asserted by
# tests/test_events_emission.py, so a typo fails the suite instead of silently
# routing an event to no frontend surface.
EVENT_TYPES = frozenset({
    # files / folders / paths
    "folder.created", "folder.renamed", "folder.moved", "folder.deleted",
    "file.uploaded", "file.updated", "file.archived", "file.unarchived", "file.deleted",
    "file.version_deleted", "file.version_rolled_back",
    "file_path.added", "file_path.removed", "file_path.promoted",
    "archive.toggled",
    # collection messages
    "message.created", "message.updated", "message.deleted",
    # timeline
    "group.created", "group.renamed", "group.deleted",
    "chain.created", "chain.renamed", "chain.deleted", "chain.reopened",
    "node.created", "node.updated", "node.deleted", "node.reordered",
    # chat sessions
    "session.created", "session.updated", "session.deleted", "session.message_created",
    # meetings
    "meeting.created", "meeting.updated", "meeting.deleted",
    "meeting.archived", "meeting.unarchived", "meeting.section_updated",
    "meeting.transcription_started", "meeting.generation_started",
    "meeting_group.created", "meeting_group.updated", "meeting_group.deleted",
    # notes
    "note.created", "note.updated", "note.deleted", "note.distilled",
    "note.propagation_started", "note.ingest_started",
    # collections
    "collection.created", "collection.deleted", "collection.archived",
    "collection.unarchived", "collection.renamed", "collection.updated",
    "collection.config_updated",
    # hot words
    "hot_words.library_created", "hot_words.library_updated",
    "hot_words.library_deleted", "hot_words.default_changed",
    "hot_words.pins_changed",
    # people
    "person.created", "person.updated", "person.deleted", "person.profile_started",
    # config
    "config.updated",
    # tasks
    "task.queued", "task.cancelled", "task.retried", "task.cleared",
})
