"""Periodic sparse-vocabulary recalculation to fix IDF drift.

When files are uploaded or deleted the vocabulary (:term:`doc_freqs`,
:term:`_doc_count`, :term:`avg_dl`) drifts from the true state of the
collection.  Over a large number of changes (> *sparse_recalc_threshold*
chunks) the BM25 weights stored on each point become stale.  This module
provides a background task that rebuilds the vocabulary from scratch and
rewrites every sparse vector so that all chunks share the same statistics.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

from qdrant_client import models

from src.db.index_routing import STAGE_PREFIX, collection_work
from src.rag.chunker import Chunk
from src.rag.index_text import build_index_text
from src.rag.source_scope import QA_CHAT_KINDS
from src.rag.sparse_encoder import SparseEncoder

if TYPE_CHECKING:
    from src.db.qdrant import QdrantManager

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = 60   # seconds to wait for sparse_lock before giving up
LOCK_STALE_SEC = 900  # a lock older than this is from a dead process — take over
SPARSE_STAGE_PREFIX = STAGE_PREFIX + "sparse_"
SCROLL_BATCH = 200  # points per scroll call


class SparseIndexChanged(RuntimeError):
    """A writer changed the source while a replacement was being built."""


class SparseRecalcLocked(RuntimeError):
    """Another rebuild holds the sparse_lock; this run did not start."""

    def __init__(self, collection: str, timeout: int):
        self.collection = collection
        self.timeout = timeout
        super().__init__(
            f"waiting for sparse_lock timed out after {timeout}s "
            f"(another sparse rebuild is running) collection={collection}"
        )


def _acquire_lock(db: QdrantManager, collection: str, stage: str = "") -> bool:
    """Try to acquire the sparse_lock in the collection config. Returns True on success.

    The lock carries a timestamp: a holder older than :data:`LOCK_STALE_SEC`
    is from a dead process (quit mid-rebuild) and is taken over instead of
    timing out every future rebuild. Locks without a timestamp (legacy
    format) are never force-taken — startup lock-clearing handles those.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT
    while time.monotonic() < deadline:
        config = db.get_collection_config(collection)
        if not config.get("sparse_lock"):
            db.update_collection_config(collection, {
                "sparse_lock": True, "sparse_lock_at": time.time(), "sparse_rebuild_stage": stage,
            })
            return True
        lock_at = float(config.get("sparse_lock_at") or 0)
        if lock_at and (time.time() - lock_at) > LOCK_STALE_SEC:
            logger.warning(
                "[SparseRecalc] stale lock in %s (age %.0fs, holder likely quit) — taking over",
                collection, time.time() - lock_at,
            )
            db.update_collection_config(collection, {
                "sparse_lock": True, "sparse_lock_at": time.time(), "sparse_rebuild_stage": stage,
            })
            return True
        time.sleep(1)
    logger.warning("[SparseRecalc] could not acquire lock for %s after %ds", collection, LOCK_TIMEOUT)
    return False


def _release_lock(db: QdrantManager, collection: str) -> None:
    """Release the sparse_lock."""
    try:
        db.update_collection_config(collection, {"sparse_lock": False, "sparse_lock_at": 0, "sparse_rebuild_stage": ""})
    except Exception:
        logger.warning("[SparseRecalc] failed to release lock for %s", collection, exc_info=True)


def _batches(db, collection, *, with_vectors):
    from src.tasks.task_manager import check_cancelled

    offset = None
    while True:
        check_cancelled()
        points, offset = db.client.scroll(
            collection_name=collection, limit=SCROLL_BATCH, offset=offset,
            with_payload=True, with_vectors=with_vectors,
        )
        yield points
        if offset is None:
            break


def _index_text(payload):
    if payload.get("chunk_type") in ("__config__", "parent") or payload.get("source_kind") in QA_CHAT_KINDS:
        return ""
    return build_index_text(Chunk(payload.get("text", ""), payload))


def _build_replacement(db, collection, stage, config):
    info = db.client.get_collection(collection)
    params = info.config.params
    strict_mode = getattr(info.config, "strict_mode_config", None)
    db.client.create_collection(
        collection_name=stage, vectors_config=params.vectors,
        sparse_vectors_config=params.sparse_vectors or {"sparse": models.SparseVectorParams()},
        on_disk_payload=params.on_disk_payload, shard_number=params.shard_number,
        replication_factor=params.replication_factor, write_consistency_factor=params.write_consistency_factor,
        hnsw_config=models.HnswConfigDiff(**info.config.hnsw_config.model_dump(exclude_none=True)),
        optimizers_config=models.OptimizersConfigDiff(**info.config.optimizer_config.model_dump(exclude_none=True)),
        wal_config=models.WalConfigDiff(**info.config.wal_config.model_dump(exclude_none=True)),
        quantization_config=info.config.quantization_config,
        strict_mode_config=(models.StrictModeConfig(**strict_mode.model_dump(exclude_none=True)) if strict_mode else None),
        **({"metadata": info.config.metadata} if getattr(info.config, "metadata", None) else {}),
    )
    for name, schema in info.payload_schema.items():
        db.client.create_payload_index(stage, name, field_schema=schema.params or schema.data_type, wait=True)

    encoder = SparseEncoder()
    vocab = config.get("sparse_vocab")
    if vocab:
        encoder.vector_format = vocab.get("vector_format", "bm25_idf_v1")
        if encoder.vector_format not in ("bm25_idf_v1", "bm25_tf_v2"):
            raise ValueError("Unsupported sparse vector format")
    for points in _batches(db, collection, with_vectors=False):
        encoder.build_vocab([text for point in points if (text := _index_text(point.payload or {}))])

    copied = 0
    for points in _batches(db, collection, with_vectors=True):
        batch = []
        for point in points:
            payload = point.payload or {}
            vectors = point.vector if point.vector is not None else {}
            if payload.get("source_kind") not in QA_CHAT_KINDS:
                # The unnamed dense vector may be returned as a list or under the empty key.
                vectors = dict(vectors) if isinstance(vectors, dict) else {"": vectors}
                vectors.pop("sparse", None)
                text = _index_text(payload)
                if text and (weights := encoder.document_weights(text)):
                    vectors["sparse"] = models.SparseVector(indices=list(weights), values=list(weights.values()))
            batch.append(models.PointStruct(id=point.id, vector=vectors, payload=payload))
        if batch:
            db.client.upsert(collection_name=stage, points=batch, wait=True)
            copied += len(batch)
    encoder.save(db, stage)
    db.update_collection_config(stage, {"sparse_recalc_counter": 0, "sparse_repair_pending": False,
                                        "sparse_lock": False, "sparse_lock_at": 0, "sparse_rebuild_stage": ""})
    if db.count_points(stage) != copied or db.count_points(collection) != copied:
        raise SparseIndexChanged("The replacement keyword index is incomplete.")
    return {"collection": collection, "rebuilt_chunks": encoder._doc_count, "terms": len(encoder.term_to_id),
            "avg_dl": round(encoder.avg_dl, 1), "remaining_counter": 0}


def run_sparse_recalc(db: QdrantManager, collection: str) -> dict:
    """Publish document vectors and their vocabulary together, after both are durable."""
    from src.tasks.task_manager import check_cancelled

    state = collection_work(collection)
    with state.rebuild():
        stage = SPARSE_STAGE_PREFIX + uuid.uuid4().hex
        if not _acquire_lock(db, collection, stage):
            raise SparseRecalcLocked(collection, LOCK_TIMEOUT)
        published = False
        try:
            with state.commit:
                revision = state.revision
                config = db.get_collection_config(collection)
            result = _build_replacement(db, collection, stage, config)
            with state.commit:
                check_cancelled()
                if state.revision != revision:
                    raise SparseIndexChanged("Collection changed during keyword rebuild. Please retry.")
                previous = db.client.publish(collection, stage)
                published = True
            db.client.retire(collection, previous)
            return result
        finally:
            if not published:
                # Publication may have succeeded even if its response was lost.
                if stage not in db.client.aliases().values():
                    if db.client.raw.collection_exists(stage):
                        db.client.raw.delete_collection(stage)
                    _release_lock(db, collection)
