"""Agentic RAG v2 — iterative retrieval with LLM-guided grading, decomposition, and rewriting.

Pipeline (three-phase orchestration):
  Phase 1 (Rewrite Loop): Retrieve → Rerank → LLM Grade → Check & Rewrite → loop
  Phase 2 (Fallback Decompose): Decompose → Parallel Sub-query → Grade → merge
  Phase 3 (Synthesize): Cluster context → Stream answer
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from src.providers.base import EmbeddingProvider, LLMProvider
from src.rag.retriever import RetrievedChunk, Retriever
from src.rag.collection_utils import get_embedding_overrides

from src.rag.agent_state import AgentState
from src.rag.agent_nodes import (
    node_retrieve_and_rerank,
    node_llm_grade,
    node_check_and_rewrite,
    node_decompose_query,
    node_parallel_sub_queries,
    node_update_retained_info,
    node_build_context,
    node_generate,
    node_generate_stream,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    answer: str
    sources: list[dict] = field(default_factory=list)
    iterations: int = 0
    query_used: str = ""


class AgenticRAG:
    """Agentic RAG orchestrator — three-phase iterative retrieval pipeline.

    Constructor signature is backward-compatible with the v1 implementation.
    """

    def __init__(
        self,
        llm: LLMProvider,
        retriever: Retriever,
        reranker=None,
        rerank_top_k: int | None = None,
        max_iterations: int = 3,
        on_step: Callable[[str, str], None] | None = None,
        embedding_overrides: dict[str, EmbeddingProvider] | None = None,
        search_mode: str = "dense",
        min_score: float = 0.0,
        db=None,
        temperature: float | None = None,
    ):
        self.llm = llm
        self.retriever = retriever
        self.reranker = reranker
        self.rerank_top_k = rerank_top_k
        self.max_iterations = max_iterations
        self.on_step = on_step or (lambda step, content: None)
        self.embedding_overrides = embedding_overrides or {}
        self.search_mode = search_mode
        self.min_score = min_score
        self.db = db
        self.temperature = temperature

    # ── Public API ───────────────────────────────────────────────────

    def run(
        self,
        query: str,
        collections: list[str] | str = "default",
        top_k: int = 5,
        max_iterations: int | None = None,
        rerank_top_k: int | None = None,
    ) -> AgentResult:
        """Non-streaming pipeline. Returns AgentResult with answer and sources."""
        if isinstance(collections, str):
            collections = [collections]
        max_iter = max_iterations or self.max_iterations
        rtk = rerank_top_k if rerank_top_k is not None else self.rerank_top_k

        logger.info("[AgenticRAG] ═══ START ═══ query='%s' collections=%s top_k=%d max_iter=%d rerank_top_k=%s",
                    query[:80], collections, top_k, max_iter, rtk)

        state = self._init_state(query, collections, top_k, max_iter, rtk)

        # ── Phase 1: Rewrite Loop ──────────────────────────────────
        while state.phase == "rewrite":
            if state.iteration_count >= max_iter:
                logger.info("[AgenticRAG] ▶ P1 iter cap reached (%d/%d), → decompose", state.iteration_count, max_iter)
                state.phase = "decompose"
                break

            self._emit_step("retrieving", f"Iteration {state.iteration_count + 1}/{max_iter}: Searching {len(collections)} collection(s)...")
            logger.info("[AgenticRAG] ▶ P1 iter %d/%d query='%s'",
                        state.iteration_count + 1, max_iter, state.current_query[:50])

            current_batch = node_retrieve_and_rerank(
                state,
                retriever=self.retriever,
                reranker=self.reranker,
                embedding_overrides=self.embedding_overrides,
                search_mode=self.search_mode,
                min_score=self.min_score,
                db=self.db,
                llm=self.llm,
            )
            if not current_batch:
                self._emit_step("rewriting", "No results found, rewriting query...")
                logger.info("[AgenticRAG] ◇ No fresh chunks (all deduped or empty), rewriting")
                node_check_and_rewrite(state, llm=self.llm, temperature=self.temperature)
                if state.phase == "rewrite":
                    continue
                break  # transitioned to decompose

            self._emit_step("grading", "Evaluating relevance of results...")
            node_llm_grade(state, current_batch, llm=self.llm, temperature=self.temperature)
            logger.info("[AgenticRAG] ◀ Grade: sufficient=%s retained=%d info_len=%d gap='%s'",
                        state.is_sufficient, len(state.retained_chunks),
                        len(state.retained_info),
                        state.current_gap_analysis[:60] if state.current_gap_analysis else "(none)")

            if state.is_sufficient:
                state.phase = "synthesize"
                logger.info("[AgenticRAG] ✓ P1 SUFFICIENT after %d iterations", state.iteration_count + 1)
                break

            self._emit_step("rewriting", "Rewriting query for better results...")
            node_check_and_rewrite(state, llm=self.llm, temperature=self.temperature)

        # ── Phase 2: Fallback Decompose ─────────────────────────────
        if state.phase == "decompose":
            logger.info("[AgenticRAG] ▶ P2 decompose (P1 exhausted after %d iters, retained=%d)",
                        state.iteration_count, len(state.retained_chunks))
            self._emit_step("decomposing", "Breaking query into sub-questions for deeper search...")
            sub_queries = node_decompose_query(state, llm=self.llm, temperature=self.temperature)
            logger.info("[AgenticRAG] P2 decomposed into %d sub-queries: %s",
                        len(sub_queries), [sq[:40] for sq in sub_queries])
            for i in range(len(sub_queries)):
                self._emit_step("retrieving", f"Searching sub-question {i + 1}/{len(sub_queries)}...")
            node_parallel_sub_queries(
                state, sub_queries,
                retriever=self.retriever, reranker=self.reranker, llm=self.llm,
                embedding_overrides=self.embedding_overrides,
                search_mode=self.search_mode, min_score=self.min_score,
                db=self.db, temperature=self.temperature,
            )
            # Re-run Part 2 to update retained_info with new sub-query results
            self._emit_step("synthesizing", "Updating information summary...")
            node_update_retained_info(state, llm=self.llm, temperature=self.temperature)

        # ── Phase 3: Synthesize ────────────────────────────────────
        logger.info("[AgenticRAG] ▶ P3 synthesize (retained=%d chunks, info_len=%d)",
                    len(state.retained_chunks), len(state.retained_info))
        result = self._synthesize(state)
        logger.info("[AgenticRAG] ═══ DONE ═══ iterations=%d sources=%d answer_len=%d",
                    result.iterations, len(result.sources), len(result.answer))
        return result

    def run_stream(
        self,
        query: str,
        collections: list[str] | str = "default",
        top_k: int = 5,
        max_iterations: int | None = None,
        rerank_top_k: int | None = None,
    ):
        """Streaming pipeline. Yields (event_dict_or_result, token_stream_or_None) tuples."""
        if isinstance(collections, str):
            collections = [collections]
        max_iter = max_iterations or self.max_iterations
        rtk = rerank_top_k if rerank_top_k is not None else self.rerank_top_k

        logger.info("[AgenticRAG] ═══ START STREAM ═══ query='%s' collections=%s", query[:80], collections)

        state = self._init_state(query, collections, top_k, max_iter, rtk)

        # ── Phase 1: Rewrite Loop ──────────────────────────────────
        while state.phase == "rewrite":
            if state.iteration_count >= max_iter:
                logger.info("[AgenticRAG] ▶ P1 iter cap reached (%d/%d), → decompose", state.iteration_count, max_iter)
                state.phase = "decompose"
                break

            iter_num = state.iteration_count + 1
            yield {"type": "step", "step": "retrieving", "iteration": iter_num,
                   "content": f"Searching {len(collections)} collection(s)..."}, None
            logger.info("[AgenticRAG] ▶ P1 iter %d/%d query='%s'",
                        iter_num, max_iter, state.current_query[:50])

            current_batch = node_retrieve_and_rerank(
                state,
                retriever=self.retriever,
                reranker=self.reranker,
                embedding_overrides=self.embedding_overrides,
                search_mode=self.search_mode,
                min_score=self.min_score,
                db=self.db,
            )

            if not current_batch:
                yield {"type": "step", "step": "rewriting", "iteration": iter_num,
                       "content": "No results found, rewriting query..."}, None
                logger.info("[AgenticRAG] ◇ No fresh chunks, rewriting")
                node_check_and_rewrite(state, llm=self.llm, temperature=self.temperature)
                yield {"type": "detail", "iteration": iter_num,
                       "content": f"New query: {state.current_query}"}, None
                if state.phase == "rewrite":
                    continue
                break

            yield {"type": "step", "step": "grading", "iteration": iter_num,
                   "content": "Evaluating relevance of results..."}, None
            node_llm_grade(state, current_batch, llm=self.llm, temperature=self.temperature)
            logger.info("[AgenticRAG] ◀ Grade: sufficient=%s retained=%d info_len=%d gap='%s'",
                        state.is_sufficient, len(state.retained_chunks),
                        len(state.retained_info),
                        state.current_gap_analysis[:60] if state.current_gap_analysis else "(none)")

            # Detail: grading result
            suff_label = "yes" if state.is_sufficient else "no"
            gap_text = state.current_gap_analysis or ""
            yield {"type": "detail", "iteration": iter_num,
                   "content": f"{len(state.retained_chunks)} relevant | sufficient: {suff_label}" +
                              (f" | gap: {gap_text}" if gap_text else "")}, None

            if state.is_sufficient:
                state.phase = "synthesize"
                logger.info("[AgenticRAG] ✓ P1 SUFFICIENT after %d iterations", iter_num)
                break

            yield {"type": "step", "step": "rewriting", "iteration": iter_num,
                   "content": "Rewriting query for better results..."}, None
            node_check_and_rewrite(state, llm=self.llm, temperature=self.temperature)
            yield {"type": "detail", "iteration": iter_num,
                   "content": f"New query: {state.current_query}"}, None

        # ── Phase 2: Fallback Decompose ─────────────────────────────
        if state.phase == "decompose":
            logger.info("[AgenticRAG] ▶ P2 decompose (P1 exhausted after %d iters, retained=%d)",
                        state.iteration_count, len(state.retained_chunks))
            yield {"type": "step", "step": "decomposing", "iteration": 0,
                   "content": "Breaking query into sub-questions for deeper search..."}, None
            sub_queries = node_decompose_query(state, llm=self.llm, temperature=self.temperature)
            logger.info("[AgenticRAG] P2 decomposed %d sub-queries: %s",
                        len(sub_queries), [sq[:40] for sq in sub_queries])
            yield {"type": "detail", "iteration": 0,
                   "content": f"Sub-questions: {'; '.join(sub_queries)}"}, None
            sq_count = len(sub_queries)
            for i in range(sq_count):
                yield {"type": "step", "step": "retrieving", "iteration": 0,
                       "content": f"Sub-question {i + 1}/{sq_count}: {sub_queries[i][:80]}"}, None
            node_parallel_sub_queries(
                state, sub_queries,
                retriever=self.retriever, reranker=self.reranker, llm=self.llm,
                embedding_overrides=self.embedding_overrides,
                search_mode=self.search_mode, min_score=self.min_score,
                db=self.db, temperature=self.temperature,
            )
            # Re-run Part 2 to update retained_info with new sub-query results
            yield {"type": "step", "step": "synthesizing", "iteration": 0,
                   "content": "Updating information summary..."}, None
            node_update_retained_info(state, llm=self.llm, temperature=self.temperature)

        # ── Phase 3: Synthesize ────────────────────────────────────
        logger.info("[AgenticRAG] ▶ P3 synthesize (retained=%d chunks, info_len=%d)",
                    len(state.retained_chunks), len(state.retained_info))
        context = node_build_context(state)
        sources = [
            {"text": c.text, "score": c.score, "metadata": c.metadata}
            for c in state.retained_chunks
        ]

        if not context:
            logger.info("[AgenticRAG] ═══ DONE (empty) ═══")
            result = AgentResult(
                answer="I searched all relevant documents but could not find information matching your query. Please try providing more keywords or rephrasing your question.",
                sources=[],
                iterations=state.iteration_count,
                query_used=state.current_query,
            )
            yield result, iter([])
            return

        # Emit assembling step with chunk order details
        yield {"type": "step", "step": "assembling", "iteration": state.iteration_count + 1,
               "content": f"Assembling {len(state.retained_chunks)} chunks into context..."}, None
        assembly_parts: list[str] = []
        for idx, c in enumerate(state.retained_chunks):
            col = c.metadata.get("collection", "?")
            src = c.metadata.get("source", "?")
            ci = c.metadata.get("chunk_index", "?")
            assembly_parts.append(f"[{idx}] {col} | {src} | chunk #{ci}")
        yield {"type": "detail", "iteration": state.iteration_count + 1,
               "content": "\n".join(assembly_parts)}, None

        yield {"type": "step", "step": "generating", "iteration": state.iteration_count + 1,
               "content": f"Generating answer from {len(state.retained_chunks)} sources..."}, None

        result = AgentResult(
            answer="",
            sources=sources,
            iterations=state.iteration_count,
            query_used=state.current_query,
        )
        logger.info("[AgenticRAG] ═══ DONE ═══ streaming tokens (sources=%d)", len(sources))
        yield result, node_generate_stream(state, context, llm=self.llm, temperature=self.temperature)

    # ── Internal helpers ─────────────────────────────────────────────

    def _init_state(
        self,
        query: str,
        collections: list[str],
        top_k: int,
        max_iter: int,
        rerank_top_k: int | None,
    ) -> AgentState:
        """Build initial AgentState from parameters."""
        if not self.embedding_overrides or set(self.embedding_overrides.keys()) != set(collections):
            self.embedding_overrides = get_embedding_overrides(collections)

        return AgentState(
            original_query=query,
            current_query=query,
            collections=collections,
            max_iterations=max_iter,
            iteration_count=0,
            top_k=top_k,
            rerank_top_k=rerank_top_k if rerank_top_k is not None else top_k,
            phase="rewrite",
        )

    def _emit_step(self, step: str, content: str) -> None:
        """Notify progress via the on_step callback."""
        self.on_step(step, content)

    def _synthesize(self, state: AgentState) -> AgentResult:
        """Phase 3: build context and generate final answer (non-streaming)."""
        context = node_build_context(state)
        sources = [
            {"text": c.text, "score": c.score, "metadata": c.metadata}
            for c in state.retained_chunks
        ]

        if not context:
            logger.info("[AgenticRAG] ═══ DONE (empty) ═══")
            return AgentResult(
                answer="I searched all relevant documents but could not find information matching your query. Please try providing more keywords or rephrasing your question.",
                sources=[],
                iterations=state.iteration_count,
                query_used=state.current_query,
            )

        self._emit_step("generating", f"Generating answer from {len(state.retained_chunks)} sources...")
        answer = node_generate(state, context, llm=self.llm, temperature=self.temperature)

        return AgentResult(
            answer=answer,
            sources=sources,
            iterations=state.iteration_count,
            query_used=state.current_query,
        )
