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

if TYPE_CHECKING:
    from src.db.qdrant import QdrantManager

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = 60   # seconds to wait for sparse_lock before giving up
SCROLL_BATCH = 200  # points per scroll call


def _acquire_lock(db: QdrantManager, collection: str) -> bool:
    """Try to acquire the sparse_lock in the collection config. Returns True on success."""
    deadline = time.monotonic() + LOCK_TIMEOUT
    while time.monotonic() < deadline:
        config = db.get_collection_config(collection)
        if not config.get("sparse_lock"):
            db.update_collection_config(collection, {"sparse_lock": True})
            return True
        time.sleep(1)
    logger.warning("[SparseRecalc] could not acquire lock for %s after %ds", collection, LOCK_TIMEOUT)
    return False


def _release_lock(db: QdrantManager, collection: str) -> None:
    """Release the sparse_lock."""
    try:
        db.update_collection_config(collection, {"sparse_lock": False})
    except Exception:
        logger.warning("[SparseRecalc] failed to release lock for %s", collection, exc_info=True)


def run_sparse_recalc(db: QdrantManager, collection: str) -> dict | None:
    """Full rebuild of sparse vocabulary and vectors for a collection.

    Scrolls every point, rebuilds the term→id mapping and document
    frequencies from scratch, computes new BM25 vectors, and writes them
    back in a single batch.  The lock ensures no concurrent upload/delete
    can interleave a partial write.

    Returns a summary dict on success, ``None`` on failure.
    """
    if not _acquire_lock(db, collection):
        return None

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
                with_payload=["text"],
                with_vectors=False,
            )
            if not batch:
                break
            for pt in batch:
                text = (pt.get("payload") or {}).get("text", "")
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
        encoder, vectors = SparseEncoder.rebuild_from_texts(all_texts)

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

    except Exception:
        logger.error("[SparseRecalc] col=%s: failed", collection, exc_info=True)
        # Leave counter unchanged so it will trigger again on the next
        # upload/delete — the collection is still consistent (old vocab
        # untouched because we only save on success).
        return None
    finally:
        _release_lock(db, collection)
