"""Build a replacement index alongside the searchable one, then swap its Qdrant alias."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import ExitStack, closing
from dataclasses import asdict

from qdrant_client.models import PointStruct

from src.config import EmbeddingBinding
from src.identity import get_actor, reset_actor, set_actor
from src.db.index_routing import STAGE_PREFIX, collection_work
from src.db.qdrant import _CONFIG_POINT_ID
from src.providers.embedding import create_embedding_provider
from src.rag.chunker import Chunk
from src.rag.embedding_validation import validate_vectors
from src.rag.index_text import build_index_text
from src.rag.source_scope import QA_CHAT_KINDS
from src.tasks import history_store

logger = logging.getLogger(__name__)


class IndexChanged(RuntimeError):
    pass


def _queued_job(config, stage_id):
    job = config.get("embedding_rebuild") or {}
    return job.get("state") == "queued" and job.get("stage_id") == stage_id


def fail_rebuild(collection, stage_id, error):
    from src.services import services

    with collection_work(collection).commit:
        db = services.db
        if not db.collection_exists(collection):
            return False
        config = db.get_collection_config(collection)
        if not _queued_job(config, stage_id):
            return False
        job = {**config["embedding_rebuild"], "state": "failed", "error": error}
        db.update_collection_config(collection, {"embedding_rebuild": job})
        history_store.mark_failed(collection, stage_id, error)
        return True


def _report_progress(task, stage, processed=0, total=None):
    if task is None:
        return
    # Reserve completion for publication of the index. Counts measure processed
    # source entries, including parent chunks and retired Q&A entries.
    progress = min(99, 100 * processed // total) if total else 0
    task.stage = stage
    task.progress = progress
    task.message = f"{processed}/{total}" if total is not None else stage
    history_store.update_rebuild_progress(task.collection, task.id, stage, progress, processed, total)


def rebuild_collection(collection, requested, *, task=None, stage_id=None):
    from src.file_mgmt import store as files
    from src.qa.search_index import _fragments, model_key_for
    from src.services import services
    from src.tasks.task_manager import check_cancelled

    db = services.db
    state = collection_work(collection)
    with state.rebuild(), ExitStack() as stack:
        with state.commit:
            active = db.get_collection_config(collection)
            if task and not _queued_job(active, stage_id):
                raise ValueError("This rebuild is no longer pending.")
            revision = state.revision
        _report_progress(task, "checking_model")
        config = requested.resolve(services.config.embedding.providers)
        if config is None:
            raise ValueError("The chosen embedding model is unavailable.")
        embedding = create_embedding_provider(config)
        dimensions = embedding.dimensions
        validate_vectors([embedding.embed_query("Embedding connection test")], 1, dimensions)
        config = config.model_copy(update={"dimensions": dimensions})
        target = EmbeddingBinding.from_provider(config)
        final_config = {**active, "dimensions": dimensions, "embedding_model": config.model,
                        "embedding_provider_id": config.id, "embedding_binding": target.model_dump(),
                        "embedding_rebuild": {"state": "complete", "stage_id": stage_id}}
        for field in ("embedding_api_key", "embedding_base_url", "embedding_provider"):
            final_config.pop(field, None)
        stage = STAGE_PREFIX + (uuid.UUID(stage_id).hex if stage_id else uuid.uuid4().hex)
        db.create_collection(stage, dimensions, final_config)
        published = False
        try:
            total = max(0, db.count_points(collection) - 1) if task is not None else None
            batch_size = min(64, max(1, config.batch_size or 10))
            processed = 0
            _report_progress(task, "reindexing", processed, total)
            conn = None
            qa_ids = []
            model_key = model_key_for(config)
            offset = None
            copied = 0
            while True:
                check_cancelled()
                points, offset = db.client.scroll(collection_name=collection, limit=batch_size, offset=offset,
                                                  with_payload=True, with_vectors=True)
                batch = []
                texts, searchable = [], []
                for point in points:
                    if str(point.id) == _CONFIG_POINT_ID:
                        continue
                    processed += 1
                    payload = dict(point.payload or {})
                    dense = [0.0] * dimensions
                    vectors = dict(point.vector) if isinstance(point.vector, dict) else {}
                    vectors[""] = dense
                    data = PointStruct(id=point.id, payload=payload, vector=vectors)
                    if payload.get("source_kind") in QA_CHAT_KINDS:
                        if conn is None:
                            if not files.db_initialized(collection):
                                raise ValueError("The Q&A source database is unavailable.")
                            conn = stack.enter_context(closing(files.get_db(collection)))
                        row = conn.execute("SELECT body FROM qa_text_revisions WHERE id=?",
                                           (payload["qa_text_revision_id"],)).fetchone()
                        if not row:
                            continue  # A retired revision, already deleted from the source.
                        text = _fragments(row["body"])[payload["qa_fragment"]]
                        data.payload["qa_model_key"] = model_key
                        qa_ids.append(str(point.id))
                    elif payload.get("chunk_type") == "parent":
                        batch.append(data)
                        continue
                    else:
                        text = build_index_text(Chunk(text=payload.get("text", ""), metadata=payload))
                    texts.append(text)
                    searchable.append(data)
                    batch.append(data)
                if texts:
                    encoded = embedding.embed_texts(texts)
                    validate_vectors(encoded, len(texts), dimensions)
                    for data, vector in zip(searchable, encoded):
                        data.vector[""] = vector
                if batch:
                    db.client.upsert(collection_name=stage, points=batch, wait=True)
                    copied += len(batch)
                if total is not None:
                    total = max(total, processed)
                _report_progress(task, "reindexing", processed, total)
                if offset is None:
                    break
            _report_progress(task, "publishing_index", processed, total)
            if db.count_points(stage) != copied + 1:
                raise RuntimeError("The replacement index is incomplete.")
            with state.commit:
                check_cancelled()
                if task and not _queued_job(db.get_collection_config(collection), stage_id):
                    raise ValueError("This rebuild is no longer pending.")
                if state.revision != revision:
                    raise IndexChanged("Collection content changed during the rebuild. Please retry.")
                if not target.resolve(services.config.embedding.providers):
                    raise ValueError("The chosen embedding model was removed during the rebuild.")
                previous = db.client.publish(collection, stage)
                published = True
                if conn is not None and qa_ids:
                    try:
                        with conn:
                            conn.executemany("UPDATE qa_index_points SET model_key=? WHERE point_id=? AND state='ready'",
                                             [(model_key, pid) for pid in qa_ids])
                    except Exception:
                        db.client.publish(collection, previous)
                        published = False
                        raise
                if task is not None:
                    history_store.mark_success(collection, task.id)
            db.client.retire(collection, previous)
            return {"points": copied, "dimensions": dimensions}
        finally:
            if not published:
                # A lost response to the alias operation can follow a successful
                # commit. Never remove a collection that Qdrant reports as live.
                if stage not in db.client.aliases().values():
                    db.client.raw.delete_collection(stage)


def _run_job(task, collection, binding, stage_id):
    from src.tasks.task_manager import set_current_task, clear_current_task

    set_current_task(task.id)
    try:
        requested = EmbeddingBinding.model_validate(binding)
        for attempt in range(2):
            try:
                result = rebuild_collection(collection, requested, task=task, stage_id=stage_id)
                break
            except IndexChanged:
                if attempt:
                    raise
        from src.events.emit import emit
        emit("collection.updated", scope={"collection_id": collection})
        return result
    except Exception as exc:
        from src.api.routes.config import _clean_error
        error = _clean_error(exc)
        if not fail_rebuild(collection, stage_id, error):
            # A removed collection cannot store the failure on its index.
            row = history_store.get_record_by_task(collection, task.id)
            if row and row["status"] not in history_store.TERMINAL_STATUSES:
                history_store.mark_failed(collection, task.id, error)
        raise RuntimeError(error) from exc
    finally:
        clear_current_task()


async def rebuild_task(task, collection, binding, stage_id=None):
    return await asyncio.to_thread(_run_job, task, collection, binding, stage_id)


def queue_rebuild(collection, binding):
    from src.services import services
    from src.tasks import task_manager

    with collection_work(collection).commit:
        if task_manager.has_active_task(collection, "embedding_rebuild"):
            raise ValueError("An embedding rebuild is already running.")
        requested = binding.model_dump()
        stage_id = uuid.uuid4().hex
        services.db.update_collection_config(collection, {"embedding_rebuild": {
            "state": "queued", "target": requested, "stage_id": stage_id}})
        return _enqueue_rebuild(collection, requested, stage_id)


def _enqueue_rebuild(collection, binding, stage_id):
    from src.tasks import task_manager

    try:
        history_store.record_queued(collection, stage_id, "Embedding rebuild",
                                    kwargs={"_requested_by": asdict(get_actor())},
                                    task_type="embedding_rebuild", model=binding["model"])
        return task_manager.create_task(collection=collection, filename="Embedding rebuild", task_type="embedding_rebuild",
                                        task_id=stage_id, stage_id=stage_id, binding=binding)
    except Exception:
        fail_rebuild(collection, stage_id, "The rebuild could not be queued.")
        raise


def retry_failed_rebuild(collection, stage_id):
    from src.services import services

    with collection_work(collection).commit:
        job = services.db.get_collection_config(collection).get("embedding_rebuild") or {}
        if job.get("state") != "failed" or job.get("stage_id") != stage_id:
            return None
        binding = EmbeddingBinding.model_validate(job["target"])
        if not binding.resolve(services.config.embedding.providers):
            return None
        return queue_rebuild(collection, binding)


def recover_rebuilds():
    from fastapi import HTTPException
    from src.tasks.access import require_execution_permission, restore_task_requester
    from src.db.index_routing import IndexRoutingClient
    from src.meeting.transcript_index import is_internal_collection
    from src.services import services

    db = services.db
    if db is None or not isinstance(db.client, IndexRoutingClient):
        return
    for collection in db.list_collections():
        if is_internal_collection(collection):
            continue
        try:
            config = db.pin_legacy_embedding(collection, services.config.embedding)
        except Exception:
            logger.warning("Could not migrate embedding settings for %s", collection, exc_info=True)
            continue
        job = config.get("embedding_rebuild", {})
        stage_id = job.get("stage_id")
        if stage_id and job.get("state") in ("complete", "failed"):
            row = history_store.get_record_by_task(collection, stage_id)
            if row and row["status"] not in history_store.TERMINAL_STATUSES:
                if job["state"] == "complete":
                    history_store.mark_success(collection, stage_id)
                else:
                    history_store.mark_failed(collection, stage_id, job.get("error", ""))
        elif job.get("state") == "queued" and job.get("target"):
            if stage_id:
                row = history_store.get_record_by_task(collection, stage_id) or {}
                requester = restore_task_requester((row.get("kwargs") or {}).get("_requested_by"))
                try:
                    require_execution_permission(requester, "embedding_rebuild", collection, {})
                except (PermissionError, HTTPException):
                    fail_rebuild(collection, stage_id, "Rebuild authorization expired. Select the model again to retry.")
                    continue
                stage = STAGE_PREFIX + uuid.UUID(stage_id).hex
                if db.client.raw.collection_exists(stage) and stage not in db.client.aliases().values():
                    db.client.raw.delete_collection(stage)
                token = set_actor(requester)
                try:
                    _enqueue_rebuild(collection, job["target"], stage_id)
                finally:
                    reset_actor(token)
