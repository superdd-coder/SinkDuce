"""RewriteLoop — iterative query-rewrite retrieval loop.

Uses AgentState as the mutable state machine.  Does NOT call build_context(),
node_generate(), or any fallback decompose logic — those belong to upper layers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.rag.agent_state import AgentState
from src.rag.agent_nodes import (
    node_combined_grade,
    node_check_and_rewrite,
    _dedup_by_id,
)

logger = logging.getLogger(__name__)
from src.rag import get_log_ctx as _ctx


@dataclass
class RewriteLoopResult:
    """Result from RewriteLoop.run()."""
    chunks: list = field(default_factory=list)          # list[RetrievedChunk] — retained chunks
    retained_info: str = ""
    is_sufficient: bool = False
    iterations: int = 0
    steps: list = field(default_factory=list)            # list[dict]
    query_used: str = ""


class RewriteLoop:
    """Iterative retrieval loop with query rewriting.

    Retrieves chunks via *direct_module*, grades relevance via LLM, rewrites
    the query when insufficient, and repeats until the information is sufficient
    or the loop is exhausted.
    """

    def __init__(self, direct_module, llm, reranker=None):
        self.direct_module = direct_module
        self.llm = llm
        self.reranker = reranker

    def run(
        self,
        query: str,
        collections: list[str],
        *,
        task_query: str = "",
        max_iterations: int = 4,
        dry_streak_limit: int = 2,
        on_step=None,
        **retrieve_kwargs,
    ) -> RewriteLoopResult:
        """Run the rewrite loop for a single *query* across *collections*.

        Parameters
        ----------
        on_step:
            Optional callback(step: str, iteration: int, content: str).
            Steps: ``retrieving``, ``grading``, ``rewriting``, ``rewrite_loop_done``.
        retrieve_kwargs:
            Passed through to ``direct_module.retrieve()`` (top_k, search_mode, etc.).
        """
        steps: list[dict] = []

        def _emit(step: str, iteration: int, content: str = ""):
            steps.append({"step": step, "iteration": iteration, "content": content})
            if on_step:
                try:
                    on_step(step, iteration, content)
                except Exception:
                    logger.exception(_ctx() + "[Loop] on_step callback raised")

        state = AgentState(
            original_query=query,
            collections=list(collections),
            current_query=query,
            phase="rewrite",
            max_iterations=max_iterations,
        )

        logger.info(_ctx() + "[Loop] start q=%r cols=%s max_iter=%d dry_limit=%d",
                    query[:120], collections, max_iterations, dry_streak_limit)
        dry_streak = 0
        last_grading_had_new_chunks = False  # track whether grading promoted anything

        while state.iteration_count < max_iterations:
            iter_label = state.iteration_count + 1  # 1-based for display

            # ── Retrieve ─────────────────────────────────────────────
            _emit("retrieving", iter_label, f"Searching with: {state.current_query[:120]}")
            try:
                batch_result = self.direct_module.retrieve(
                    state.current_query, state.collections, **retrieve_kwargs,
                )
                batch = batch_result.chunks if hasattr(batch_result, "chunks") else list(batch_result)
            except Exception:
                logger.exception(_ctx() + "[Loop] direct_module.retrieve raised")
                _emit("retrieving", iter_label, "Retrieval failed")
                batch = []

            # ── Dedup ────────────────────────────────────────────────
            new_chunks, new_ids = _dedup_by_id(batch, state.seen_chunk_ids)

            if not new_chunks:
                dry_streak += 1
                _emit("retrieving", iter_label,
                      f"No new chunks (dry streak {dry_streak}/{dry_streak_limit})")
                if dry_streak >= dry_streak_limit:
                    break
                # Still need to check if we should rewrite
            else:
                dry_streak = 0
                state.seen_chunk_ids.update(new_ids)
                state.all_chunks.extend(new_chunks)
                _emit("retrieving", iter_label,
                      f"Retrieved {len(new_chunks)} new chunks (total seen: {len(state.seen_chunk_ids)})")

            # ── Grade ────────────────────────────────────────────────
            retained_before = len(state.retained_chunks)

            if new_chunks:
                _emit("grading", iter_label, f"Grading {len(new_chunks)} chunks")
                try:
                    node_combined_grade(state, new_chunks, llm=self.llm)
                except Exception:
                    logger.exception(_ctx() + "[Loop] node_combined_grade raised")
                    _emit("grading", iter_label, "Grade failed, continuing")

            retained_after = len(state.retained_chunks)

            if retained_after == retained_before and new_chunks:
                # LLM found no relevant chunks
                dry_streak += 1
                _emit("grading", iter_label,
                      f"No relevant chunks found (dry streak {dry_streak}/{dry_streak_limit})")
                if dry_streak >= dry_streak_limit:
                    break

            if state.is_sufficient:
                _emit("grading", iter_label, "Information sufficient")
                break

            # ── Rewrite ──────────────────────────────────────────────
            _emit("rewriting", iter_label, "Rewriting query")
            try:
                node_check_and_rewrite(state, llm=self.llm, task_query=task_query)
            except Exception:
                logger.exception(_ctx() + "[Loop] node_check_and_rewrite raised")
                # Fallback: use original query, increment
                state.history_queries.append(state.current_query)
                if state.iteration_count < max_iterations:
                    state.iteration_count += 1

            _emit("rewriting", iter_label,
                  f"Rewritten to: {state.current_query[:120]} (iter {state.iteration_count}/{max_iterations})")

            # If node_check_and_rewrite transitioned to decompose, ignore it
            if state.phase != "rewrite":
                state.phase = "rewrite"  # stay in rewrite loop

        # ── Build result ──────────────────────────────────────────────
        _emit("rewrite_loop_done", state.iteration_count,
              f"Done: {len(state.retained_chunks)} retained chunks, sufficient={state.is_sufficient}")

        logger.info(_ctx() + "[Loop] done: iter=%d chunks=%d sufficient=%s query=%r",
                    state.iteration_count, len(state.retained_chunks), state.is_sufficient,
                    state.current_query[:120])
        return RewriteLoopResult(
            chunks=list(state.retained_chunks),
            retained_info=state.retained_info,
            is_sufficient=state.is_sufficient,
            iterations=state.iteration_count,
            steps=steps,
            query_used=state.current_query,
        )
