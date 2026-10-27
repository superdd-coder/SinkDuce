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
from typing import TYPE_CHECKING

from src.rag.sparse_encoder import SparseEncoder
from src.rag.chunker import Chunk
from src.rag.index_text import build_index_text

if TYPE_CHECKING:
    from src.db.qdrant import QdrantManager

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = 60   # seconds to wait for sparse_lock before giving up
LOCK_STALE_SEC = 900  # a lock older than this is from a dead process — take over
SCROLL_BATCH = 200  # points per scroll call


class SparseRecalcLocked(RuntimeError):
    """Another rebuild holds the sparse_lock; this run did not start."""

    def __init__(self, collection: str, timeout: int):
        self.collection = collection
        self.timeout = timeout
        super().__init__(
            f"waiting for sparse_lock timed out after {timeout}s "
            f"(another sparse rebuild is running) collection={collection}"
        )


def _acquire_lock(db: QdrantManager, collection: str) -> bool:
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
                "sparse_lock": True, "sparse_lock_at": time.time(),
            })
            return True
        lock_at = float(config.get("sparse_lock_at") or 0)
        if lock_at and (time.time() - lock_at) > LOCK_STALE_SEC:
            logger.warning(
                "[SparseRecalc] stale lock in %s (age %.0fs, holder likely quit) — taking over",
                collection, time.time() - lock_at,
            )
            db.update_collection_config(collection, {
                "sparse_lock": True, "sparse_lock_at": time.time(),
            })
            return True
        time.sleep(1)
    logger.warning("[SparseRecalc] could not acquire lock for %s after %ds", collection, LOCK_TIMEOUT)
    return False


def _release_lock(db: QdrantManager, collection: str) -> None:
    """Release the sparse_lock."""
    try:
        db.update_collection_config(collection, {"sparse_lock": False, "sparse_lock_at": 0})
    except Exception:
        logger.warning("[SparseRecalc] failed to release lock for %s", collection, exc_info=True)


def run_sparse_recalc(db: QdrantManager, collection: str) -> dict:
    """Full rebuild of sparse vocabulary and vectors for a collection.

    Scrolls every point, rebuilds the term→id mapping and document
    frequencies from scratch, computes new BM25 vectors, and writes them
    back in batches.  The lock ensures no concurrent upload/delete can
    interleave a partial write.

    Returns a summary dict on success.  Raises :class:`SparseRecalcLocked`
    when another rebuild holds the lock, and propagates the real
    exception on failure (the caller turns it into task.error so the UI
    shows the actual cause instead of a generic message).  The old
    vocabulary stays untouched on failure — the collection remains
    consistent and the counter still triggers a later retry.
    """
    if not _acquire_lock(db, collection):
        raise SparseRecalcLocked(collection, LOCK_TIMEOUT)

    try:
        # ── 1. Scroll all texts ──
        logger.info("[SparseRecalc] col=%s: scrolling all points...", collection)
        all_ids: list[str] = []
        all_texts: list[str] = []
        offset = None

        while True:
            batch, next_offset = db.scroll_points(
                collection,
                limit=SCROLL_BATCH,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not batch:
                break
            for pt in batch:
                payload = pt.get("payload") or {}
                if payload.get("chunk_type") in ("parent", "__config__"):
                    continue
                text = build_index_text(Chunk(payload.get("text", ""), payload))
                if text:
                    all_ids.append(pt["id"])
                    all_texts.append(text)
            if next_offset is None:
                break  # all points scrolled
            offset = next_offset

        if not all_texts:
            logger.info("[SparseRecalc] col=%s: collection empty, skipping", collection)
            db.update_collection_config(collection, {"sparse_recalc_counter": 0})
            return {"collection": collection, "rebuilt_chunks": 0}

        logger.info("[SparseRecalc] col=%s: scrolled %d chunks", collection, len(all_texts))

        # ── 2. Rebuild vocabulary + vectors from scratch ──
        # Preserve existing formats; routine rebuilds must not migrate a legacy
        # index implicitly. A new collection uses query-time IDF (v2).
        config = db.get_collection_config(collection)
        vocab = config.get("sparse_vocab") or {}
        vector_format = vocab.get("vector_format", "bm25_idf_v1") if vocab else "bm25_tf_v2"
        encoder, vectors = SparseEncoder.rebuild_from_texts(all_texts, vector_format=vector_format)

        # ── 3. Write back ──
        db.upsert_sparse_vectors(collection, all_ids, vectors)
        encoder.save(db, collection)

        # ── 4. Adjust counter (subtract the chunks we just handled) ──
        config = db.get_collection_config(collection)
        counter = max(0, config.get("sparse_recalc_counter", 0) - len(all_texts))
        db.update_collection_config(collection, {"sparse_recalc_counter": counter})

        summary = {
            "collection": collection,
            "rebuilt_chunks": len(all_texts),
            "terms": len(encoder.term_to_id),
            "avg_dl": round(encoder.avg_dl, 1),
            "remaining_counter": counter,
        }
        logger.info("[SparseRecalc] col=%s: done — %s", collection, summary)
        return summary

    finally:
        _release_lock(db, collection)
