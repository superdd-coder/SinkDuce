from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.db.qdrant import QdrantManager
from src.providers.base import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


class Retriever:
    def __init__(self, db: QdrantManager, embedding: EmbeddingProvider):
        self.db = db
        self.embedding = embedding
        self._sparse_encoder = None

    def _get_sparse_encoder(self):
        if self._sparse_encoder is None:
            from src.rag.sparse_encoder import SparseEncoder
            self._sparse_encoder = SparseEncoder()
        return self._sparse_encoder

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
    ) -> list[RetrievedChunk]:
        emb = embedding_override or self.embedding
        query_vector = emb.embed_query(query)
        logger.info("[Retriever] col=%s mode=%s dim=%d top_k=%d min_score=%.2f",
                     collection, search_mode, len(query_vector), top_k, min_score)

        if search_mode == "hybrid":
            chunks = self._hybrid_retrieve(query, query_vector, collection, top_k,
                                           filter_condition=filter_condition, llm=llm)
        else:
            results = self.db.search(
                collection=collection, query_vector=query_vector, top_k=top_k,
                filter_condition=filter_condition,
            )
            chunks = self._to_chunks(results)

        # Threshold only applies to dense mode (cosine scores 0-1), not hybrid (RRF rank scores)
        if min_score > 0 and search_mode != "hybrid":
            chunks = [c for c in chunks if c.score >= min_score]
        logger.info("[Retriever] col=%s → %d results", collection, len(chunks))
        return chunks

    def _hybrid_retrieve(
        self, query: str, query_vector: list[float], collection: str, top_k: int,
        filter_condition=None, llm=None,
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
            vocab_path = Path("data") / collection / "sparse_vocab.json"
            if not vocab_path.exists():
                raise FileNotFoundError(
                    f"No sparse vocabulary for collection {collection} at {vocab_path}"
                )

            encoder = self._get_sparse_encoder()
            encoder.load(str(vocab_path))

            # LLM-powered query preprocessing for sparse encoding
            from src.rag.sparse_encoder import preprocess_query_for_sparse
            sparse_query, _keywords = preprocess_query_for_sparse(query, llm)

            if llm is not None and sparse_query != query:
                logger.info("[Retriever] hybrid: LLM expanded query %r → %r", query[:80], sparse_query[:80])

            sparse_vector = encoder.encode_query(sparse_query)

            if not sparse_vector:
                logger.info("[Retriever] hybrid: empty sparse vector for %r, falling back to dense", query[:80])
                raise ValueError("Query produced empty sparse vector (no known terms)")

            id_to_term = {v: k for k, v in encoder.term_to_id.items()}
            top5 = sorted(sparse_vector.items(), key=lambda x: x[1], reverse=True)[:5]
            top_terms = [f"{id_to_term.get(tid, '?')}({w:.2f})" for tid, w in top5]
            logger.info("[Retriever] hybrid: sparse_dim=%d top_terms=[%s]",
                        len(sparse_vector), ", ".join(top_terms))

            results = self.db.hybrid_search(
                collection=collection,
                query_vector=query_vector,
                sparse_vector=sparse_vector,
                top_k=top_k,
                filter_condition=filter_condition,
            )
            chunks = self._to_chunks(results)
            logger.info("[Retriever] hybrid: col=%s → %d chunks top_score=%.4f",
                        collection, len(chunks), chunks[0].score if chunks else 0)
            return chunks
        except Exception as e:
            logger.warning("[Retriever] hybrid fallback → dense: col=%s reason=%s", collection, e)
            results = self.db.search(
                collection=collection, query_vector=query_vector, top_k=top_k,
                filter_condition=filter_condition,
            )
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

    logger.info("[Retriever] multi: %d results from %d collections", len(all_results), len(collections))
    if reranker and all_results:
        all_results = reranker.rerank(query, all_results)

    # Sort by score descending so best results across all collections survive the top_k cut
    all_results.sort(key=lambda c: c.score, reverse=True)
    return all_results[:top_k]
