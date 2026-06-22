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
        logger.info("Retriever.retrieve: collection=%s, search_mode=%s, vector_dim=%d, top_k=%d, min_score=%.2f",
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

        logger.info("Retriever.retrieve: collection=%s, got %d results", collection, len(chunks))
        # Threshold only applies to dense mode (cosine scores 0-1), not hybrid (RRF rank scores)
        if min_score > 0 and search_mode != "hybrid":
            chunks = [c for c in chunks if c.score >= min_score]
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
            from src.rag.sparse_encoder import preprocess_query_for_sparse, _tokenize
            sparse_query, keywords = preprocess_query_for_sparse(query, llm)

            if llm is not None and sparse_query != query:
                # Show tokenization comparison: raw vs LLM-preprocessed
                raw_tokens = _tokenize(query)
                proc_tokens = _tokenize(sparse_query)
                raw_unique = set(raw_tokens)
                proc_unique = set(proc_tokens)
                added = proc_unique - raw_unique
                removed = raw_unique - proc_unique
                logger.info(
                    "[HYBRID-VERIFY] LLM preprocessing: raw_query=%r raw_tokens=%d "
                    "proc_query=%r proc_tokens=%d added=%d removed=%d added_samples=%s removed_samples=%s",
                    query[:120], len(raw_tokens),
                    sparse_query[:120], len(proc_tokens),
                    len(added), len(removed),
                    sorted(added)[:10] if added else "[]",
                    sorted(removed)[:10] if removed else "[]",
                )
            elif llm is None:
                logger.info(
                    "[HYBRID-VERIFY] LLM preprocessing SKIPPED (llm=None), "
                    "using raw query for sparse encoding: %r",
                    query[:120],
                )

            sparse_vector = encoder.encode_query(sparse_query)

            if not sparse_vector:
                # Show what tokens were in the query but not in vocab
                from src.rag.sparse_encoder import _tokenize
                q_tokens = _tokenize(query)
                known = [t for t in q_tokens if t in encoder.term_to_id]
                unknown = [t for t in q_tokens if t not in encoder.term_to_id]
                logger.info(
                    "[HYBRID-VERIFY] _hybrid_retrieve: empty sparse vector "
                    "query=%r query_tokens=%d known=%d unknown=%d unknown_samples=%s",
                    query, len(q_tokens), len(known), len(unknown),
                    unknown[:10] if unknown else "[]",
                )
                raise ValueError("Query produced empty sparse vector (no known terms)")

            # Log query encoding details
            id_to_term = {v: k for k, v in encoder.term_to_id.items()}
            top5 = sorted(sparse_vector.items(), key=lambda x: x[1], reverse=True)[:5]
            top_terms = [f"{id_to_term.get(tid, '?')}({w:.2f})" for tid, w in top5]
            logger.info(
                "[HYBRID-VERIFY] _hybrid_retrieve: raw_query=%r sparse_query=%r "
                "sparse_dim=%d top_terms=[%s] vocab_terms=%d",
                query[:120], sparse_query[:120],
                len(sparse_vector), ", ".join(top_terms), len(encoder.term_to_id),
            )

            results = self.db.hybrid_search(
                collection=collection,
                query_vector=query_vector,
                sparse_vector=sparse_vector,
                top_k=top_k,
                filter_condition=filter_condition,
            )
            chunks = self._to_chunks(results)
            logger.info(
                "[HYBRID-VERIFY] _hybrid_retrieve: hybrid_search returned %d chunks "
                "top_score=%.4f",
                len(chunks), chunks[0].score if chunks else 0,
            )

            # ── Per-chunk term-level diagnostics ──
            top_ids = [r["id"] for r in results[:3]]
            if top_ids:
                try:
                    from qdrant_client.models import SparseVector
                    id_to_term = {v: k for k, v in encoder.term_to_id.items()}
                    # Fetch points with sparse vectors
                    points_with_vecs = self.db.client.retrieve(
                        collection_name=collection,
                        ids=top_ids,
                        with_payload=True,
                        with_vectors=["sparse"],
                    )
                    # Build set of query term IDs that actually contributed
                    query_terms = set(sparse_vector.keys())
                    for pt in points_with_vecs:
                        pt_id = str(pt.id)
                        pt_text = (pt.payload or {}).get("text", "")[:80]
                        # Extract named sparse vector
                        pt_sparse = None
                        if pt.vector and isinstance(pt.vector, dict):
                            sv = pt.vector.get("sparse")
                            if sv is not None:
                                if isinstance(sv, SparseVector):
                                    pt_sparse = dict(zip(sv.indices, sv.values))
                                elif isinstance(sv, dict):
                                    pt_sparse = sv
                        if pt_sparse:
                            # Intersect chunk sparse vector with query sparse vector
                            overlap = {
                                tid: (pt_sparse.get(tid, 0), sparse_vector.get(tid, 0))
                                for tid in query_terms
                                if tid in pt_sparse
                            }
                            if overlap:
                                term_details = [
                                    f"{id_to_term.get(tid, '?')}(chunk={pt_sparse[tid]:.2f}, query={sparse_vector[tid]:.2f})"
                                    for tid in sorted(overlap, key=lambda t: overlap[t][0], reverse=True)[:10]
                                ]
                                logger.info(
                                    "[HYBRID-VERIFY] chunk=%s score=%.4f matched_terms=%d "
                                    "details=[%s] text=%.80s",
                                    pt_id,
                                    next((r["score"] for r in results[:3] if r["id"] == pt_id), 0),
                                    len(overlap),
                                    ", ".join(term_details),
                                    pt_text,
                                )
                            else:
                                logger.info(
                                    "[HYBRID-VERIFY] chunk=%s ZERO sparse overlap text=%.80s",
                                    pt_id, pt_text,
                                )
                        else:
                            logger.info(
                                "[HYBRID-VERIFY] chunk=%s NO sparse vector stored text=%.80s",
                                pt_id, pt_text,
                            )
                except Exception as diag_err:
                    logger.warning(
                        "[HYBRID-VERIFY] chunk diagnostics failed: %s", diag_err,
                    )
            return chunks
        except Exception as e:
            logger.warning(
                "[HYBRID-VERIFY] _hybrid_retrieve: fallback to dense "
                "collection=%s reason=%s",
                collection, e,
            )
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
    logger.info("multi_collection_retrieve: collections=%s, top_k=%d, search_mode=%s, has_overrides=%s",
                collections, top_k, search_mode, bool(embedding_overrides))
    all_results: list[RetrievedChunk] = []
    seen_texts: set[str] = set()

    for col in collections:
        override = embedding_overrides.get(col) if embedding_overrides else None
        logger.info("multi_collection_retrieve: searching col=%s, has_override=%s", col, override is not None)
        try:
            chunks = retriever.retrieve(
                query, collection=col, top_k=top_k,
                embedding_override=override, search_mode=search_mode, min_score=min_score,
                llm=llm,
            )
            logger.info("multi_collection_retrieve: col=%s returned %d chunks", col, len(chunks))
        except Exception as e:
            logger.error("multi_collection_retrieve: col=%s failed: %s", col, e)
            chunks = []
        for c in chunks:
            if c.text not in seen_texts:
                seen_texts.add(c.text)
                c.metadata["collection"] = col
                all_results.append(c)

    logger.info("multi_collection_retrieve: total %d results from %d collections", len(all_results), len(collections))
    if reranker and all_results:
        all_results = reranker.rerank(query, all_results)

    # Sort by score descending so best results across all collections survive the top_k cut
    all_results.sort(key=lambda c: c.score, reverse=True)
    return all_results[:top_k]
