from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

logger = logging.getLogger(__name__)


# Fixed UUID for storing collection config as a Qdrant point
_CONFIG_POINT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "my-rag-collection-config"))

_DEFAULT_COLLECTION_CONFIG = {
    "dimensions": 1536,
    "chunk_mode": "normal",
    "chunk_size": 512,
    "chunk_overlap": 64,
    "parent_strategy": "paragraph",
    "parent_chunk_size": 1024,
    "parent_chunk_overlap": 128,
    "child_chunk_size": 256,
    "child_chunk_overlap": 32,
    "buffer_ratio": 0.5,
    "embedding_provider": "openai_compatible",
    "embedding_model": "",
    "embedding_base_url": "",
    "embedding_api_key": "",
    "embedding_batch_size": 10,
    "rerank_provider": "qwen",
    "rerank_model": "",
    "rerank_base_url": "",
    "rerank_api_key": "",
    "rerank_top_k": 5,
    "contextual_enabled": True,
    "contextual_window": 1,
    "search_mode": "dense",
    "sparse_llm_tokenize": True,
    "summary_change_counter": 0,
    "summary_consolidate_threshold": 10,
    "sparse_recalc_threshold": 5000,
    "sparse_recalc_counter": 0,
    "sparse_lock": False,
    # Must match Collection Config UI default (frontend uses true when key missing).
    # Missing key used to read as False in upload_handler → MinerU never ran.
    "cloud_parsing": True,
}

# Fields that cannot be changed after collection creation
_LOCKED_FIELDS = {"dimensions", "chunk_mode"}


def get_default_collection_config() -> dict:
    """Return a copy of the default collection config."""
    return dict(_DEFAULT_COLLECTION_CONFIG)


class QdrantManager:
    def __init__(self, host: str = "localhost", port: int = 6333):
        self.client = QdrantClient(host=host, port=port)

    def create_collection(
        self,
        name: str,
        vector_size: int,
        chunk_config: dict | None = None,
    ) -> None:
        logger.info("Create collection: %s (vector_size=%d)", name, vector_size)
        try:
            self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                sparse_vectors_config={"sparse": SparseVectorParams()},
            )
        except Exception as exc:
            # 并发建库竞态（2026-09-19 P2）：两台设备同时向一个新 collection
            # 上传，双方都过了 exists 检查 → 后到的 create 撞 409。集合已被
            # 另一个任务建好且参数一致时，这**不是错误**，吞掉继续。
            if self.collection_exists(name):
                logger.info(
                    "[Qdrant] create_collection %s raced with a concurrent "
                    "creation; collection already exists — continuing",
                    name,
                )
            else:
                raise exc
        logger.info("[Qdrant] collection=%s sparse_vectors_config={\"sparse\": SparseVectorParams()}", name)
        # Store full collection config as a dedicated point
        if chunk_config:
            cfg = {**_DEFAULT_COLLECTION_CONFIG, **chunk_config}
            cfg["dimensions"] = vector_size
            self.upsert_points(
                collection=name,
                ids=[_CONFIG_POINT_ID],
                vectors=[[0.0] * vector_size],
                payloads=[{"collection_config": cfg, "chunk_type": "__config__"}],
            )

    def collection_exists(self, name: str) -> bool:
        collections = self.client.get_collections().collections
        return any(c.name == name for c in collections)

    def list_collections(self) -> list[str]:
        return [c.name for c in self.client.get_collections().collections]

    def clear_sparse_locks(self) -> int:
        """Clear sparse_lock flags left behind by a dead process.

        Quitting mid-rebuild leaves ``sparse_lock: True`` stuck in the
        collection config, and every later rebuild then times out waiting
        for a holder that no longer exists.  Only safe at startup — while
        this process is down nothing can be holding the lock.
        """
        cleared = 0
        for name in self.list_collections():
            try:
                cfg = self.get_collection_config(name)
                if cfg.get("sparse_lock"):
                    self.update_collection_config(
                        name, {"sparse_lock": False, "sparse_lock_at": 0}
                    )
                    cleared += 1
                    logger.info("[SparseRecalc] cleared stale lock for %s", name)
            except Exception:
                logger.warning(
                    "[SparseRecalc] lock clear failed for %s", name, exc_info=True
                )
        return cleared

    def delete_collection(self, name: str) -> None:
        self.client.delete_collection(collection_name=name)

    def get_vector_size(self, name: str) -> int | None:
        """Get the actual vector dimension from Qdrant (authoritative, not from config)."""
        try:
            info = self.client.get_collection(collection_name=name)
            vec = info.config.params.vectors
            if vec is None:
                return None
            if isinstance(vec, dict):
                # Named vectors — return size of first vector
                return next(iter(vec.values())).size
            return vec.size
        except Exception:
            return None

    def get_collection_info(self, name: str) -> dict[str, Any]:
        info = self.client.get_collection(collection_name=name)
        # Exclude __config__ points from count
        filter_cond = Filter(must_not=[FieldCondition(key="chunk_type", match=MatchValue(value="__config__"))])
        try:
            actual_count = self.client.count(collection_name=name, count_filter=filter_cond).count
        except Exception:
            actual_count = info.points_count
        return {
            "name": name,
            "vectors_count": getattr(info, "vectors_count", getattr(info, "indexed_vectors_count", 0)),
            "points_count": actual_count,
            "status": str(info.status),
        }

    def upsert_points(
        self,
        collection: str,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
    ) -> None:
        if len(ids) != len(vectors) or len(ids) != len(payloads):
            raise ValueError("Point ID, dense vector and payload lengths must match")
        if not ids:
            return  # Nothing to upsert — skip to avoid Qdrant "Empty update request" error
        points = [
            PointStruct(id=id_, vector=vec, payload=pl)
            for id_, vec, pl in zip(ids, vectors, payloads)
        ]
        self.client.upsert(collection_name=collection, points=points)

    def upsert_sparse_vectors(
        self,
        collection: str,
        ids: list[str],
        sparse_vectors: list[dict[int, float]],
    ) -> None:
        """Add sparse vectors to existing points without touching dense vectors.

        Uses Qdrant's update_vectors API to set only the ``"sparse"`` named vector,
        leaving the unnamed dense vector unchanged.  Written in batches so a
        full-collection rebuild on a large collection cannot issue one
        oversized request (which Qdrant rejects or times out on).
        """
        if len(ids) != len(sparse_vectors):
            raise ValueError("Sparse vector and point ID lengths must match")
        if not ids:
            return
        from qdrant_client.models import PointVectors
        # Chunks whose text tokenizes to nothing produce an empty sparse
        # vector; Qdrant rejects the whole update_vectors batch on any
        # empty entry ("must specify vectors to update"), so drop them.
        pairs = [(i, v) for i, v in zip(ids, sparse_vectors) if v]
        skipped = len(ids) - len(pairs)
        if skipped:
            logger.warning(
                "[Qdrant] upsert_sparse_vectors collection=%s skipped %d empty vector(s)",
                collection, skipped,
            )
        if not pairs:
            return
        BATCH = 500
        total = 0
        for start in range(0, len(pairs), BATCH):
            batch = pairs[start : start + BATCH]
            points = []
            for id_, sv in batch:
                points.append(PointVectors(
                    id=id_,
                    vector={"sparse": SparseVector(
                        indices=list(sv.keys()),
                        values=list(sv.values()),
                    )},
                ))
            self.client.update_vectors(collection_name=collection, points=points)
            total += len(points)
        logger.info(
            "[Qdrant] upsert_sparse_vectors collection=%s total=%d batches=%d",
            collection, total, (len(pairs) + BATCH - 1) // BATCH,
        )

    def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 10,
        filter_condition: Filter | None = None,
    ) -> list[dict[str, Any]]:
        results = self.client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
            query_filter=filter_condition,
        )
        return [
            {"id": str(r.id), "score": r.score, "payload": r.payload or {}}
            for r in results.points
        ]

    def delete_points(self, collection: str, ids: list[str]) -> None:
        self.client.delete(collection_name=collection, points_selector=ids)

    def delete_by_filter(self, collection: str, key: str, value: Any) -> int:
        """Delete points matching a filter. Returns the number of deleted points."""
        filter_condition = Filter(
            must=[FieldCondition(key=key, match=MatchValue(value=value))]
        )
        try:
            count = self.client.count(
                collection_name=collection,
                count_filter=filter_condition,
            ).count
        except Exception:
            count = 0
        self.client.delete(
            collection_name=collection,
            points_selector=filter_condition,
        )
        return count

    def count_points(self, collection: str) -> int:
        return self.client.count(collection_name=collection).count

    def count_by_filter(self, collection: str, filter_condition: Filter) -> int:
        return self.client.count(
            collection_name=collection,
            count_filter=filter_condition,
        ).count

    def scroll_points(
        self,
        collection: str,
        limit: int = 100,
        offset: Any = None,
        scroll_filter: Filter | None = None,
        with_payload: list[str] | bool = True,
        with_vectors: bool = False,
    ) -> tuple[list[dict[str, Any]], Any]:
        points, next_offset = self.client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=limit,
            offset=offset,
            with_payload=with_payload,
            with_vectors=with_vectors,
        )
        results = [
            {"id": str(p.id), "payload": p.payload or {}}
            for p in points
        ]
        return results, next_offset

    def get_collection_config(self, collection: str) -> dict:
        """Read collection config. Returns default if not set (backward compat).

        Always merge with ``_DEFAULT_COLLECTION_CONFIG`` so newly added keys
        (e.g. cloud_parsing) are present even for collections created before
        those fields existed.
        """
        try:
            points = self.client.retrieve(
                collection_name=collection,
                ids=[_CONFIG_POINT_ID],
                with_payload=True,
            )
            if points and points[0].payload:
                # New format: collection_config
                if "collection_config" in points[0].payload:
                    stored = points[0].payload["collection_config"] or {}
                    return {**_DEFAULT_COLLECTION_CONFIG, **stored}
                # Old format: chunk_config (backward compat)
                if "chunk_config" in points[0].payload:
                    return {
                        **_DEFAULT_COLLECTION_CONFIG,
                        **(points[0].payload["chunk_config"] or {}),
                    }
        except Exception:
            pass
        return dict(_DEFAULT_COLLECTION_CONFIG)

    def hybrid_search(
        self,
        collection: str,
        query_vector: list[float],
        sparse_vector: dict[int, float] | None = None,
        top_k: int = 10,
        filter_condition: Filter | None = None,
    ) -> list[dict[str, Any]]:
        """Dense + sparse hybrid search with RRF fusion.

        Uses Qdrant's built-in fusion when sparse vectors are provided,
        falls back to dense-only search otherwise.
        """
        if not sparse_vector:
            logger.warning("[Qdrant] hybrid_search: sparse_vector is None, falling back to dense")
            return self.search(collection, query_vector, top_k, filter_condition=filter_condition)

        from qdrant_client.models import FusionQuery, Prefetch

        sparse = SparseVector(
            indices=list(sparse_vector.keys()),
            values=list(sparse_vector.values()),
        )
        logger.info(
            "[Qdrant] hybrid_search collection=%s dense_dim=%d sparse_dim=%d top_k=%d",
            collection, len(query_vector), len(sparse.indices), top_k,
        )

        results = self.client.query_points(
            collection_name=collection,
            prefetch=[
                Prefetch(query=query_vector, limit=top_k * 3, filter=filter_condition),
                Prefetch(query=sparse, using="sparse", limit=top_k * 3, filter=filter_condition),
            ],
            query=FusionQuery(fusion="rrf"),
            limit=top_k,
        )
        scores = [f"{r.score:.4f}" for r in results.points]
        logger.info(
            "[Qdrant] hybrid_search results: %d points (top_k=%d) scores=%s",
            len(results.points), top_k, scores,
        )
        return [
            {"id": str(r.id), "score": r.score, "payload": r.payload or {}}
            for r in results.points
        ]

    def get_points_by_ids(
        self, collection: str, ids: list[str]
    ) -> list[dict[str, Any]]:
        """Retrieve points by their IDs."""
        points = self.client.retrieve(
            collection_name=collection,
            ids=ids,
            with_payload=True,
        )
        return [
            {"id": str(p.id), "payload": p.payload or {}}
            for p in points
        ]

    def update_collection_config(self, collection: str, updates: dict) -> dict:
        """Update collection config with partial updates. Returns error dict or merged config."""
        current = self.get_collection_config(collection)

        # Validate locked fields
        locked_attempted = set(updates.keys()) & _LOCKED_FIELDS
        if locked_attempted:
            return {"error": f"Cannot change locked fields: {', '.join(sorted(locked_attempted))}"}

        # Merge updates into current config
        merged = {**current, **{k: v for k, v in updates.items() if v is not None}}

        # Use actual collection vector size (authoritative), not config value
        vector_size = self.get_vector_size(collection)
        if vector_size is None:
            vector_size = merged.get("dimensions", 1536)

        # Save back
        self.upsert_points(
            collection=collection,
            ids=[_CONFIG_POINT_ID],
            vectors=[[0.0] * vector_size],
            payloads=[{"collection_config": merged, "chunk_type": "__config__"}],
        )
        return merged
