"""Routing and recovery of transcript indexes when embedding connections change."""
from __future__ import annotations

import hashlib
import logging
import time

from src.config import EmbeddingBinding, EmbeddingConfig
from src.meeting.transcript_index import TRANSCRIPT_COLLECTION

logger = logging.getLogger(__name__)


def space_collection(binding: EmbeddingBinding) -> str:
    identity = f"{binding.source}\n{binding.model}\n{binding.dimensions}"
    return TRANSCRIPT_COLLECTION + hashlib.sha256(identity.encode()).hexdigest()


def previous_index(meeting, settings, db):
    if meeting.transcript_embedding:
        return meeting.transcript_embedding, meeting.transcript_index_collection
    if meeting.transcript_index_status == "ready" and settings.legacy_default:
        size = db.get_vector_size(TRANSCRIPT_COLLECTION)
        if size:
            return settings.legacy_default.model_copy(update={"dimensions": size}), TRANSCRIPT_COLLECTION
    return None, ""


def lookup_space(meeting_id):
    from src.meeting import store
    from src.providers.embedding import create_embedding_provider
    from src.services import services

    from src.identity import get_actor, visibility
    if not visibility.is_meeting_visible(get_actor(), meeting_id):
        return "", None
    if not isinstance(getattr(services.config, "embedding", None), EmbeddingConfig):
        return TRANSCRIPT_COLLECTION, None
    meeting = store.get_meeting(meeting_id)
    if not meeting or meeting.archived or meeting.transcript_index_status != "ready":
        return "", None
    settings = services.config.embedding
    binding, collection = previous_index(meeting, settings, services.db)
    provider = binding.resolve(settings.providers) if binding else None
    return (collection, create_embedding_provider(provider)) if provider else ("", None)


def reconcile(*, retry_now=False):
    """Recover interrupted work and schedule exactly the meetings needing a new space."""
    from src.identity import get_actor, may_see_everything
    from src.meeting import store
    from src.services import services
    from src.tasks import task_manager

    if not may_see_everything(get_actor()) or services.config is None or services.db is None:
        return
    settings = services.config.embedding
    active = {task["filename"] for task in task_manager.get_active_tasks(task_type="meeting_transcript_index")}
    for meeting in store.list_meetings():
        status = meeting.transcript_index_status
        filename = f"tx-index:{meeting.id}"
        if meeting.archived or filename in active:
            continue
        try:
            if not status:
                if meeting.status.value != "completed":
                    continue
                from src.meeting.transcript_index import load_sentences_for_meeting
                if not load_sentences_for_meeting(meeting.id):
                    continue
            binding, collection = previous_index(meeting, settings, services.db)
            if binding and not meeting.transcript_embedding:
                store.update_meeting(meeting.id, transcript_embedding=binding, transcript_index_collection=collection)
            original = binding.resolve(settings.providers) if binding else None
            if original and status in ("ready", "waiting_model") and collection:
                if status == "waiting_model":
                    store.update_meeting(meeting.id, transcript_index_status="ready", transcript_index_error="")
                continue
            if not settings.default:
                if status != "waiting_model":
                    store.update_meeting(meeting.id, transcript_index_status="waiting_model",
                                         transcript_index_error="No embedding model configured.")
                continue
            if status == "failed" and not meeting.transcript_retry_after:
                continue
            if not retry_now and meeting.transcript_retry_after > time.time():
                continue
            store.update_meeting(meeting.id, transcript_index_status="building", transcript_index_error="")
            task_manager.create_task(filename=filename, task_type="meeting_transcript_index", meeting_id=meeting.id)
        except Exception:
            logger.exception("Could not reconcile transcript index for meeting %s", meeting.id)


_watcher = None


def connections_changed():
    if _watcher is not None:
        loop, wake = _watcher
        loop.call_soon_threadsafe(wake.set)


async def watch_connections():
    import asyncio
    global _watcher
    wake = asyncio.Event()
    _watcher = (asyncio.get_running_loop(), wake)
    try:
        while True:
            retry_now = wake.is_set()
            wake.clear()
            try:
                await asyncio.to_thread(reconcile, retry_now=retry_now)
            except Exception:
                logger.exception("Transcript index reconciliation failed")
            try:
                await asyncio.wait_for(wake.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
    finally:
        _watcher = None
