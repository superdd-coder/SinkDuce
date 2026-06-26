"""DirectQueryModule — atomic retrieval across one or more collections.

Routes each collection by its `chunk_mode` config (normal / parent_child),
merges results by score, optionally reranks.  Does NOT perform text dedup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)


@dataclass
class DirectQueryResult:
    """Result from DirectQueryModule.retrieve()."""
    chunks: list[RetrievedChunk] = field(default_factory=list)
    child_groups: dict[str, list[dict]] = field(default_factory=dict)
    answer: str | None = None       # set when generate_answer=True
    context: str = ""                # build_context(chunks), always set


_DIRECT_ANSWER_USER = """Question: {query}

Context:
{context}

Your goal is to answer the question above.
Using the context provided, write a complete, well-structured answer.
- Include specific data points (numbers, names, dates) where relevant.
- Use Markdown formatting with headers, bullet points, and tables where helpful.
- Do NOT fabricate information not present in the context."""


class DirectQueryModule:
    """Atomic retrieval entry point used by RewriteLoop and direct endpoints."""

    def __init__(self, retriever, db, reranker=None, llm=None):
        self.retriever = retriever
        self.db = db
        self.reranker = reranker
        self.llm = llm

    # ── public entry point ──────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        collections: list[str],
        top_k: int = 10,
        search_mode: str = "dense",
        rerank_enabled: bool = False,
        rerank_top_k: int | None = None,
        generate_answer: bool = False,
        on_step=None,
        min_score: float = 0.0,
        sparse_llm_tokenize: bool = False,
        embedding_overrides: dict | None = None,
        llm_for_sparse=None,
    ) -> DirectQueryResult:
        """Retrieve chunks across *collections*, routing each by chunk_mode.

        Parameters
        ----------
        rerank_enabled:
            If True and a reranker is available, rerank results. Default False.
            Callers should set this explicitly — it is NOT auto-enabled.
        rerank_top_k:
            Post-rerank truncation. When None and rerank is enabled, falls back
            to the reranker's own default top_k.

        on_step:
            Optional callback(event: dict). Events: ``retrieve_start``,
            ``retrieve_done``, ``synthesize_start``, ``synthesize_done``.

        Returns a DirectQueryResult whose ``chunks`` are sorted by score
        descending and truncated to *top_k* (or *rerank_top_k* after rerank).
        """

        def _emit(step: str, content: str = "", **meta):
            if on_step:
                try:
                    on_step({"step": step, "content": content, **meta})
                except Exception:
                    pass

        logger.info("[Direct] retrieve q=%r cols=%s top_k=%d mode=%s rerank=%s rerank_top_k=%s min_score=%.2f",
                    query[:120], collections, top_k, search_mode, rerank_enabled, rerank_top_k, min_score)

        all_chunks: list[RetrievedChunk] = []
        all_child_groups: dict[str, list[dict]] = {}

        # emit: step="retrieve_start", query, collections, top_k, search_mode
        _emit("retrieve_start", f"Searching {len(collections)} collection(s)",
              query=query[:200], collections=collections, top_k=top_k, search_mode=search_mode)

        overrides = embedding_overrides or {}

        for col in collections:
            try:
                col_config = self.db.get_collection_config(col)
            except Exception:
                logger.warning("[Direct] cannot read config for col=%r, defaulting to normal", col)
                col_config = {"chunk_mode": "normal"}

            chunk_mode = col_config.get("chunk_mode", "normal")
            emb_override = overrides.get(col)
            child_fetch = max(top_k * 10, 50) if chunk_mode == "parent_child" else 0
            logger.info("[Direct] col=%s -%s | mode=%s top_k=%d%s",
                        col, chunk_mode, search_mode, top_k,
                        f" child_fetch={child_fetch}" if child_fetch else "")

            try:
                if chunk_mode == "parent_child":
                    parent_chunks, child_groups = self._retrieve_parent_child(
                        query, col, top_k,
                        embedding=emb_override,
                        min_score=min_score,
                        search_mode=search_mode,
                        sparse_llm_tokenize=sparse_llm_tokenize,
                        llm_for_sparse=llm_for_sparse,
                    )
                    for c in parent_chunks:
                        c.metadata["collection"] = col
                    all_chunks.extend(parent_chunks)
                    for pid, children in child_groups.items():
                        if pid not in all_child_groups:
                            all_child_groups[pid] = []
                        all_child_groups[pid].extend(children)
                else:
                    chunks = self._retrieve_normal(
                        query, col, top_k,
                        embedding_override=emb_override,
                        search_mode=search_mode,
                        min_score=min_score,
                        sparse_llm_tokenize=sparse_llm_tokenize,
                        llm_for_sparse=llm_for_sparse,
                    )
                    for c in chunks:
                        c.metadata["collection"] = col
                    all_chunks.extend(chunks)
            except Exception:
                logger.exception("[Direct] retrieval failed for col=%r, skipping", col)

        # Merge: sort by score descending, NO text dedup
        all_chunks.sort(key=lambda c: c.score, reverse=True)

        # ── Optional rerank ────────────────────────────────────────────
        do_rerank = rerank_enabled and self.reranker and all_chunks
        if do_rerank:
            try:
                k = rerank_top_k if rerank_top_k else None
                all_chunks = self.reranker.rerank(query, all_chunks, top_k=k)
                logger.info("[Direct] reranked: %d chunks → top_k=%s", len(all_chunks), k or "default")
            except Exception:
                logger.exception("[Direct] rerank failed, using un-reranked results")
                do_rerank = False

        # ── Truncate ───────────────────────────────────────────────────
        # After rerank: use rerank_top_k; without rerank: use top_k
        if do_rerank and rerank_top_k:
            limit = rerank_top_k
        else:
            limit = top_k
        all_chunks = all_chunks[:limit]

        # Filter child_groups to only include parents kept after truncation
        kept_ids = {c.metadata.get("id", "") for c in all_chunks}
        filtered_groups = {
            pid: children for pid, children in all_child_groups.items()
            if pid in kept_ids
        }

        total_children = sum(len(v) for v in filtered_groups.values())
        logger.info("[Direct] done: %d parents, %d children, %d collections",
                    len(all_chunks), total_children, len(collections))

        # emit: step="retrieve_done", chunks, collections
        _emit("retrieve_done", f"{len(all_chunks)} chunks", chunks=len(all_chunks), collections=len(collections))

        # ── Build context (always) ─────────────────────────────────────
        from src.rag.context_builder import build_context as _bc
        context = _bc(all_chunks) if all_chunks else ""

        # ── Generate answer (optional) ─────────────────────────────────
        answer: str | None = None
        if generate_answer and self.llm and all_chunks:
            # emit: step="synthesize_start", query
            _emit("synthesize_start", "Generating answer", query=query[:200])
            prompt = _DIRECT_ANSWER_USER.format(
                query=query,
                context=context[:12000],
            )
            try:
                answer = self.llm.generate(prompt, max_tokens=8192, thinking=True).strip()
                logger.info("[Direct] answer: %d chars", len(answer))
                # emit: step="synthesize_done", answer_len
                _emit("synthesize_done", f"{len(answer)} chars", answer_len=len(answer))
            except Exception:
                logger.exception("[Direct] answer generation failed")
                _emit("synthesize_done", "failed", error=True)

        return DirectQueryResult(
            chunks=all_chunks, child_groups=filtered_groups,
            answer=answer, context=context,
        )

    # ── private helpers ─────────────────────────────────────────────────

    def _retrieve_normal(
        self,
        query: str,
        collection: str,
        top_k: int,
        *,
        embedding_override=None,
        search_mode: str = "dense",
        min_score: float = 0.0,
        sparse_llm_tokenize: bool = False,
        llm_for_sparse=None,
    ) -> list[RetrievedChunk]:
        """Standard dense/hybrid retrieval for a single collection."""
        llm = llm_for_sparse if sparse_llm_tokenize else None
        return self.retriever.retrieve(
            query,
            collection=collection,
            top_k=top_k,
            embedding_override=embedding_override,
            search_mode=search_mode,
            min_score=min_score,
            llm=llm,
        )

    def _retrieve_parent_child(
        self,
        query: str,
        collection: str,
        top_k: int,
        *,
        embedding=None,
        min_score: float = 0.0,
        search_mode: str = "dense",
        sparse_llm_tokenize: bool = False,
        llm_for_sparse=None,
    ) -> tuple[list[RetrievedChunk], dict[str, list[dict]]]:
        """Parent-child retrieval for a single collection.

        Searches child chunks, resolves parent_ids, and returns parent-level
        RetrievedChunk objects (scored by best child score) plus child grouping.
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        llm = llm_for_sparse if sparse_llm_tokenize else None

        child_filter = Filter(
            must=[FieldCondition(key="chunk_type", match=MatchValue(value="child"))]
        )
        top_k = int(top_k) if top_k else 10
        child_search_limit = max(top_k * 10, 50)

        if search_mode == "hybrid":
            child_chunks = self.retriever.retrieve(
                query, collection=collection, top_k=child_search_limit,
                search_mode="hybrid", min_score=min_score,
                filter_condition=child_filter, llm=llm,
            )
            child_results = [
                {"id": c.metadata.get("id", ""), "score": c.score,
                 "payload": {**c.metadata, "text": c.text}}
                for c in child_chunks
            ]
        else:
            emb = embedding
            if emb is None:
                # Fall back to retriever's default embedding
                query_vector = self.retriever.embedding.embed_query(query)
            else:
                query_vector = emb.embed_query(query)
            child_results = self.db.search(
                collection=collection,
                query_vector=query_vector,
                top_k=child_search_limit,
                filter_condition=child_filter,
            )

        if min_score > 0 and search_mode != "hybrid":
            child_results = [r for r in child_results if r["score"] >= min_score]

        if not child_results:
            return [], {}

        # Collect unique parent IDs
        parent_ids = list({
            r["payload"]["parent_id"]
            for r in child_results
            if r["payload"].get("parent_id")
        })

        if not parent_ids:
            chunks = [RetrievedChunk(
                text=r["payload"].get("text", ""),
                score=r["score"],
                metadata={k: v for k, v in r["payload"].items() if k != "text"},
            ) for r in child_results]
            return chunks, {}

        # Retrieve parent chunks
        parent_points = self.db.get_points_by_ids(collection, parent_ids)
        parent_map = {p["id"]: p["payload"] for p in parent_points}

        child_groups: dict[str, list[dict]] = {}
        seen_parents: dict[str, RetrievedChunk] = {}

        for r in child_results:
            pid = r["payload"].get("parent_id")
            if not pid:
                continue

            if pid not in child_groups:
                child_groups[pid] = []
            child_groups[pid].append({
                "id": str(r["id"]),
                "text": r["payload"].get("text", ""),
                "score": r["score"],
                "source": r["payload"].get("source", ""),
                "collection": collection,
                "chunk_index": r["payload"].get("chunk_index", 0),
                "chunk_type": "child",
                "context": r["payload"].get("context"),
                "parent_id": pid,
            })

            if pid not in seen_parents or r["score"] > seen_parents[pid].score:
                parent_payload = parent_map.get(pid, r["payload"])
                seen_parents[pid] = RetrievedChunk(
                    text=parent_payload.get("text", ""),
                    score=r["score"],
                    metadata={k: v for k, v in parent_payload.items() if k != "text"} | {"id": pid},
                )

        results = sorted(seen_parents.values(), key=lambda c: c.score, reverse=True)
        return results[:top_k], child_groups
