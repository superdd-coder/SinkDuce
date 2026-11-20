from __future__ import annotations

import logging
import uuid
from typing import Any

from src.db.index_routing import IndexRoutingClient
from qdrant_client.qdrant_client import QdrantClient as RealQdrantClient
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
_LOCKED_FIELDS = {"dimensions", "chunk_mode", "embedding_binding", "embedding_provider_id",
                  "embedding_provider", "embedding_model", "embedding_base_url", "embedding_api_key"}


def get_default_collection_config() -> dict:
    """Return a copy of the default collection config."""
    return dict(_DEFAULT_COLLECTION_CONFIG)


class QdrantManager:
    def __init__(self, host: str = "localhost", port: int = 6333):
        self.endpoint = (host, port)
        self.client = QdrantClient(host=host, port=port)

    @property
    def client(self):
        return self._client

    @client.setter
    def client(self, client):
        self._client = IndexRoutingClient(client) if isinstance(client, RealQdrantClient) else client

    def create_collection(
        self,
        name: str,
        vector_size: int,
        chunk_config: dict | None = None,
    ) -> None:
        logger.info("Create collection: %s (vector_size=%d)", name, vector_size)
        if not name.startswith("__"):
            from src.config import EmbeddingBinding, EmbeddingConfig
            from src.rag.collection_utils import new_collection_provider
            from src.services import services

            if isinstance(getattr(services.config, "embedding", None), EmbeddingConfig):
                chunk_config = dict(chunk_config or {})
                provider = new_collection_provider(chunk_config.get("embedding_provider_id"))
                chunk_config.update(embedding_binding=EmbeddingBinding.from_provider(provider, vector_size).model_dump(),
                                    embedding_provider_id=provider.id, embedding_model=provider.model)
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
                return
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
        if isinstance(self.client, IndexRoutingClient):
            return self.client.collection_exists(name)
        collections = self.client.get_collections().collections
        return any(c.name == name for c in collections)

    def list_collections(self) -> list[str]:
        if isinstance(self.client, IndexRoutingClient):
            return self.client.list_names()
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
        if isinstance(self.client, IndexRoutingClient) and not name.startswith("__"):
            self.client.delete_logical(name)
        else:
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
        expected_binding=None,
    ) -> None:
        if len(ids) != len(vectors) or len(ids) != len(payloads):
            raise ValueError("Point ID, dense vector and payload lengths must match")
        if not ids:
            return  # Nothing to upsert — skip to avoid Qdrant "Empty update request" error
        points = [
            PointStruct(id=id_, vector=vec, payload=pl)
            for id_, vec, pl in zip(ids, vectors, payloads)
        ]
        if expected_binding is None:
            self.client.upsert(collection_name=collection, points=points)
            return
        from src.db.index_routing import collection_work
        from src.rag.collection_utils import get_collection_embedding_config
        with collection_work(collection).commit:
            current = get_collection_embedding_config(self.get_collection_config(collection), collection)
            if current is None or not expected_binding.resolve([current]) or current.dimensions != expected_binding.dimensions:
                raise ValueError("The collection's embedding model changed before the upload completed.")
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
        from src.rag.source_scope import source_filter
        filter_condition = source_filter(filter_condition)
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

    def search_qa(
        self, collection: str, query_vector: list[float], point_ids: list[str], top_k: int
    ) -> list[dict]:
        """Search only the Q&A points authorized by the current SQLite projection."""
        if not point_ids:
            return []
        from qdrant_client.models import HasIdCondition, MatchAny
        from src.rag.source_scope import QA_CHAT_KINDS

        results = self.client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
            with_payload=False,
            query_filter=Filter(must=[
                HasIdCondition(has_id=point_ids),
                FieldCondition(key="source_kind", match=MatchAny(any=list(QA_CHAT_KINDS))),
            ]),
        )
        return [{"id": str(point.id), "score": point.score} for point in results.points]

    def delete_points(self, collection: str, ids: list[str]) -> None:
        self.client.delete(collection_name=collection, points_selector=ids)

    def delete_by_filter(self, collection: str, key: str, value: Any, *, keep_ids: list[str] | None = None) -> int:
        """Delete points matching a filter. Returns the number of deleted points."""
        filter_condition = Filter(
            must=[FieldCondition(key=key, match=MatchValue(value=value))]
        )
        if keep_ids:
            from qdrant_client.models import HasIdCondition
            filter_condition.must_not = [HasIdCondition(has_id=keep_ids)]
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
        include_qa_materials: bool = False,
    ) -> tuple[list[dict[str, Any]], Any]:
        from src.rag.source_scope import source_filter
        # Index maintenance must rebuild the entire vocabulary. Retrieval callers
        # retain the scoped default and never expose this switch as user input.
        scroll_filter = source_filter(scroll_filter, include_qa_materials=include_qa_materials)
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
            logger.exception("Could not read the index configuration for %s", collection)
            raise
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

        from src.rag.source_scope import source_filter
        filter_condition = source_filter(filter_condition)
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
        if not ids:
            return []
        from qdrant_client.models import HasIdCondition
        from src.rag.source_scope import source_filter
        points, _ = self.client.scroll(
            collection_name=collection,
            scroll_filter=source_filter(Filter(must=[HasIdCondition(has_id=ids)])),
            limit=len(ids), with_payload=True,
        )
        by_id = {str(p.id): {"id": str(p.id), "payload": p.payload or {}} for p in points}
        return [by_id[str(pid)] for pid in ids if str(pid) in by_id]

    def pin_legacy_embedding(self, collection, settings):
        """Write the upgrade-time identity once, without changing document vectors."""
        from src.db.index_routing import collection_work
        from src.rag.collection_utils import collection_binding

        with collection_work(collection).commit:
            config = self.get_collection_config(collection)
            if config.get("embedding_binding"):
                return config
            size = self.get_vector_size(collection)
            binding = collection_binding(config, settings, size)
            if size and binding:
                config.update(embedding_binding=binding.model_dump(), embedding_provider_id=binding.provider_id,
                              embedding_model=binding.model, dimensions=size)
                self._store_config(collection, config, size)
            return config

    def update_collection_config(self, collection: str, updates: dict) -> dict:
        """Update collection config with partial updates. Returns error dict or merged config."""
        from src.db.index_routing import collection_work
        with collection_work(collection).commit:
            return self._update_collection_config(collection, updates)

    def _update_collection_config(self, collection: str, updates: dict) -> dict:
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

        self._store_config(collection, merged, vector_size)
        return merged

    def _store_config(self, collection, config, vector_size):
        self.upsert_points(
            collection=collection,
            ids=[_CONFIG_POINT_ID],
            vectors=[[0.0] * vector_size],
            payloads=[{"collection_config": config, "chunk_type": "__config__"}],
        )
