from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.db.qdrant import QdrantManager
from src.providers.base import EmbeddingProvider

logger = logging.getLogger(__name__)


def _notify_trace(callback, event):
    if callback:
        try:
            callback(event)
        except Exception:
            logger.debug("Retrieval trace callback failed", exc_info=True)


def trace_vector_search(callback, event, binding, provider_name, dimensions):
    if binding is not None:
        event = {**event, "model": binding.model, "provider_id": binding.provider_id,
                 "provider_name": provider_name, "dimensions": dimensions}
    _notify_trace(callback, event)


@dataclass
class RetrievedChunk:
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


class Retriever:
    def __init__(self, db: QdrantManager, embedding: EmbeddingProvider):
        self.db = db
        self.embedding = embedding

    def _get_sparse_encoder(self):
        # Vocabulary is mutable and collection-specific. Each retrieval needs
        # its own snapshot, including while sparse query preprocessing waits.
        from src.rag.sparse_encoder import SparseEncoder
        return SparseEncoder()

    def exclusion_filter(self):
        """Exclude configuration points, archived/non-current and Unassigned-only.

        v2 R6: chunks of files mounted only in the Unassigned folder are
        excluded from retrieval, alongside archived and superseded chunks.
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        return Filter(
            must_not=[
                FieldCondition(key="chunk_type", match=MatchValue(value="__config__")),
                FieldCondition(key="archived", match=MatchValue(value=True)),
                FieldCondition(key="is_current", match=MatchValue(value=False)),
                FieldCondition(key="unassigned", match=MatchValue(value=True)),
            ]
        )

    def retrieve(
        self,
        query: str,
        collection: str = "default",
        top_k: int = 10,
        embedding_override: EmbeddingProvider | None = None,
        search_mode: str = "dense",
        min_score: float = 0.0,
        filter_condition=None,
        llm=None,
        on_trace=None,
        meeting_ids: tuple[str, ...] = (),
    ) -> list[RetrievedChunk]:
        # RBAC（2026-09-16）：不可见的库**根本不查**——这是"隐藏"最根本的一道。
        # 在这里拦住而不是"查出来再过滤"：后者意味着内容已经进了结果集，任何
        # 下游疏漏都会把它带进模型上下文。判定失败同样拒绝（fail-closed）。
        try:
            from src.identity import get_actor
            from src.identity import visibility as _visibility

            from src.meeting.transcript_index import TRANSCRIPT_COLLECTION
            if collection.startswith(TRANSCRIPT_COLLECTION):
                # Transcript lookups always build a meeting-ID filter, after the
                # meeting-domain read gate. Never allow an unscoped internal query.
                allowed = bool(meeting_ids) and all(_visibility.is_meeting_visible(get_actor(), mid) for mid in meeting_ids)
                if allowed:
                    from qdrant_client.models import FieldCondition, Filter, MatchAny
                    scope = FieldCondition(key="meeting_id", match=MatchAny(any=list(meeting_ids)))
                    filter_condition = Filter(must=[scope, filter_condition] if filter_condition else [scope])
            else:
                allowed = _visibility.is_collection_visible(get_actor(), collection)
            if not allowed:
                logger.info("[Retriever] collection %r not visible to actor — refused", collection)
                return []
        except Exception:
            logger.exception("[Retriever] visibility gate failed for %r — refused", collection)
            return []

        from contextlib import nullcontext
        from src.config import EmbeddingBinding
        from src.db.index_routing import IndexRoutingClient
        from src.rag.collection_utils import get_collection_embedding_config, EmbeddingDisconnected
        from src.services import services

        client = getattr(self.db, "client", None)
        routed = isinstance(client, IndexRoutingClient) and not collection.startswith("__")
        with client.snapshot(collection) if routed else nullcontext():
            pin = getattr(embedding_override, "index_binding", None)
            provider_name = ""
            if routed and services.config is not None and (embedding_override is None or isinstance(pin, EmbeddingBinding)):
                cfg = get_collection_embedding_config(self.db.get_collection_config(collection), collection)
                if cfg is None:
                    raise EmbeddingDisconnected("Embedding model unavailable.")
                active = EmbeddingBinding.from_provider(cfg)
                if pin != active:
                    from src.providers.embedding import create_embedding_provider
                    embedding_override = create_embedding_provider(cfg)
                    if embedding_override is None:
                        raise EmbeddingDisconnected("Embedding model unavailable.")
                pin = active
                provider_name = cfg.name
            binding = pin if isinstance(pin, EmbeddingBinding) else None
            return self._search(query, collection, top_k, embedding_override or self.embedding,
                                search_mode, min_score, filter_condition, llm, on_trace, binding, provider_name)

    def _search(self, query, collection, top_k, emb, search_mode, min_score, filter_condition, llm,
                on_trace, binding, provider_name):
        query_vector = emb.embed_query(query)
        if binding:
            from src.rag.embedding_validation import validate_vectors
            validate_vectors([query_vector], 1, binding.dimensions or len(query_vector))

        def report_search(event):
            trace_vector_search(on_trace, event, binding, provider_name, len(query_vector))

        logger.debug("[Retriever] col=%s mode=%s dim=%d top_k=%d min_score=%.2f",
                     collection, search_mode, len(query_vector), top_k, min_score)

        # Exclude archived / non-current / Unassigned-only chunks
        archive_filter = self.exclusion_filter()
        if filter_condition:
            from qdrant_client.models import Filter as QdrantFilter
            # Merge: combine with AND logic
            merged_filter = QdrantFilter(
                must=[
                    archive_filter,
                    filter_condition,
                ]
            )
        else:
            merged_filter = archive_filter

        if search_mode == "hybrid":
            chunks = self._hybrid_retrieve(query, query_vector, collection, top_k,
                                           filter_condition=merged_filter, llm=llm, on_trace=report_search)
        else:
            results = self.db.search(
                collection=collection, query_vector=query_vector, top_k=top_k,
                filter_condition=merged_filter,
            )
            report_search({"kind": "retrieval", "collection": collection, "mode": "dense"})
            chunks = self._to_chunks(results)

        # Threshold only applies to dense mode (cosine scores 0-1), not hybrid (RRF rank scores)
        if min_score > 0 and search_mode != "hybrid":
            chunks = [c for c in chunks if c.score >= min_score]
        logger.debug("[Retriever] col=%s → %d results", collection, len(chunks))
        return chunks

    def _hybrid_retrieve(
        self, query: str, query_vector: list[float], collection: str, top_k: int,
        filter_condition=None, llm=None, on_trace=None,
    ) -> list[RetrievedChunk]:
        """Hybrid dense + sparse search using persisted vocabulary.

        Loads the collection's vocabulary from disk, encodes the query into
        a BM25 sparse vector, and lets Qdrant fuse both via RRF.
        Falls back to dense-only when the vocabulary is missing or the query
        produces no known terms.

        When llm is provided, the query text is preprocessed by the LLM
        (keyword extraction + synonym expansion) before sparse encoding.
        Dense encoding always uses the original query_vector.
        """
        try:
            encoder = self._get_sparse_encoder()
            encoder.load(self.db, collection)
            if not encoder.term_to_id:
                raise FileNotFoundError(
                    f"No sparse vocabulary for collection {collection}"
                )

            # LLM-powered query preprocessing for sparse encoding
            from src.rag.sparse_encoder import preprocess_query_for_sparse
            sparse_query, _keywords = preprocess_query_for_sparse(query, llm)

            if llm is not None and sparse_query != query:
                logger.debug("[Retriever] hybrid: LLM expanded query %r → %r", query[:80], sparse_query[:80])

            sparse_vector = encoder.encode_query(sparse_query)

            if not sparse_vector:
                logger.debug("[Retriever] hybrid: empty sparse vector for %r, falling back to dense", query[:80])
                raise ValueError("Query produced empty sparse vector (no known terms)")

            id_to_term = {v: k for k, v in encoder.term_to_id.items()}
            top5 = sorted(sparse_vector.items(), key=lambda x: x[1], reverse=True)[:5]
            top_terms = [f"{id_to_term.get(tid, '?')}({w:.2f})" for tid, w in top5]
            logger.debug("[Retriever] hybrid: sparse_dim=%d top_terms=[%s]",
                        len(sparse_vector), ", ".join(top_terms))

            results = self.db.hybrid_search(
                collection=collection,
                query_vector=query_vector,
                sparse_vector=sparse_vector,
                top_k=top_k,
                filter_condition=filter_condition,
            )
            _notify_trace(on_trace, {"kind": "retrieval", "collection": collection, "mode": "hybrid"})
            chunks = self._to_chunks(results)
            logger.debug("[Retriever] hybrid: col=%s → %d chunks top_score=%.4f",
                        collection, len(chunks), chunks[0].score if chunks else 0)
            return chunks
        except Exception as e:
            logger.warning("[Retriever] hybrid fallback → dense: col=%s reason=%s", collection, e)
            results = self.db.search(
                collection=collection, query_vector=query_vector, top_k=top_k,
                filter_condition=filter_condition,
            )
            _notify_trace(on_trace, {"kind": "retrieval_fallback", "collection": collection,
                                     "requested_mode": "hybrid", "mode": "dense", "error": type(e).__name__})
            return self._to_chunks(results)

    @staticmethod
    def _to_chunks(results: list[dict]) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                text=r["payload"].get("text", ""),
                score=r["score"],
                metadata={k: v for k, v in r["payload"].items() if k != "text"} | {"id": r["id"]},
            )
            for r in results
        ]


def multi_collection_retrieve(
    retriever: Retriever,
    query: str,
    collections: list[str],
    top_k: int = 10,
    reranker=None,
    embedding_overrides: dict[str, EmbeddingProvider] | None = None,
    search_mode: str = "dense",
    min_score: float = 0.0,
    llm=None,
) -> list[RetrievedChunk]:
    """Search across multiple collections with optional cross-collection reranking."""
    all_results: list[RetrievedChunk] = []
    seen_texts: set[str] = set()

    for col in collections:
        override = embedding_overrides.get(col) if embedding_overrides else None
        try:
            chunks = retriever.retrieve(
                query, collection=col, top_k=top_k,
                embedding_override=override, search_mode=search_mode, min_score=min_score,
                llm=llm,
            )
        except Exception as e:
            logger.error("[Retriever] multi: col=%s failed: %s", col, e)
            chunks = []
        for c in chunks:
            if c.text not in seen_texts:
                seen_texts.add(c.text)
                c.metadata["collection"] = col
                all_results.append(c)

    logger.debug("[Retriever] multi: %d results from %d collections", len(all_results), len(collections))
    if reranker and all_results:
        all_results = reranker.rerank(query, all_results)

    # Sort by score descending so best results across all collections survive the top_k cut
    all_results.sort(key=lambda c: c.score, reverse=True)
    return all_results[:top_k]
