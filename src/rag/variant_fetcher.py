"""VariantFetcher — one-shot retrieval with query paraphrasing for broader coverage.

Generates paraphrased variants of an atomic query, retrieves all variants in parallel,
deduplicates by Qdrant point ID, and grades relevance in a single pass.
No iterative rewriting — gap_analysis tells the upper layer what's still missing.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import as_completed
from dataclasses import dataclass, field

from src.rag.agent_nodes import _dedup_by_id, node_combined_grade
from src.rag.agent_prompts import VARIANT_GENERATION_SYSTEM, VARIANT_GENERATION_USER
from src.rag.agent_state import AgentState

logger = logging.getLogger(__name__)
from src.rag import get_log_ctx as _ctx


@dataclass
class VariantFetcherResult:
    """Result from VariantFetcher.run()."""
    chunks: list = field(default_factory=list)          # list[RetrievedChunk] — retained chunks
    retrieved_chunks: list = field(default_factory=list)  # pre-grade unique chunks
    retained_info: str = ""
    gap_analysis: str = ""       # what's still missing (empty = complete)
    steps: list = field(default_factory=list)            # list[dict]
    query_used: str = ""
    variants: list[str] = field(default_factory=list)   # generated variant queries
    total_retrieved: int = 0    # unique chunks before grading
    trace: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


class VariantFetcher:
    """One-shot retrieval with query variants for broader coverage.

    Generates paraphrased variants of the AQ, retrieves all variants in parallel,
    deduplicates by point ID, and grades relevance in a single pass.
    No iterative rewriting — gap_analysis tells the upper layer what's still missing.
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
        original_user_question: str = "",
        variant_count: int = 2,
        on_step=None,
        **retrieve_kwargs,
    ) -> VariantFetcherResult:
        """Run variant-based retrieval for a single *query* across *collections*.

        Parameters
        ----------
        on_step:
            Optional callback(step: str, iteration: int, content: str).
            Steps: ``variant_generation``, ``retrieving``, ``grading``,
            ``rewrite_loop_done`` (name kept for SSE compatibility).
            iteration is always 0 (no iterative loop).
        retrieve_kwargs:
            Passed through to ``direct_module.retrieve()`` (top_k, search_mode, etc.).
        """
        steps: list[dict] = []

        def _emit(step: str, iteration: int, content: str = "", **extra):
            steps.append({"step": step, "iteration": iteration, "content": content, **extra})
            if on_step:
                try:
                    on_step(step, iteration, content, extra if extra else None)
                except Exception:
                    logger.exception(_ctx() + "on_step error")

        state = AgentState(
            original_query=query,
            original_user_question=original_user_question,
            collections=list(collections),
        )

        logger.info(_ctx() + "start  q=%r  cols=%s",
                    query[:80], collections)

        # ── ① Generate variants ──────────────────────────────────────────
        variants: list[str] = []
        if variant_count > 0 and self.llm:
            try:
                prompt = VARIANT_GENERATION_USER.format(
                    query=query,
                    task_query=task_query or "",
                    count=variant_count,
                )
                raw = self.llm.generate(
                    prompt, system=VARIANT_GENERATION_SYSTEM,
                    max_tokens=512, thinking=False,
                ).strip()
                parsed = self._parse_json(raw)
                if isinstance(parsed, list):
                    variants = [str(v).strip() for v in parsed if str(v).strip()]
                    variants = variants[:variant_count]
            except Exception:
                logger.exception(_ctx() + "variant generation failed, using original only")

        if not variants:
            _emit("variant_generation", 0, "No variants generated, using original query only",
                  variants=[], variant_count=0)
            logger.info(_ctx() + "variants: 0 (using original only)")
        else:
            _emit("variant_generation", 0,
                  f"Generated {len(variants)} variant(s)",
                  variants=variants, variant_count=len(variants))
            logger.info(_ctx() + "variants: %d — %s",
                        len(variants),
                        " | ".join(v[:60] for v in variants))

        # ── ② Parallel retrieval: original + all variants ────────────────
        all_queries: list[tuple[str, str]] = [("original", query)] + [
            (f"variant{i+1}", v) for i, v in enumerate(variants)
        ]

        logger.info(_ctx() + "retrieve: %d queries × %d cols",
                    len(all_queries), len(collections))

        all_batches: list = []  # list[list[RetrievedChunk]]
        trace, errors = [], []
        trace_lock = threading.Lock()

        def _record(result, query):
            with trace_lock:
                trace.extend(dict(event, query=query) for event in getattr(result, "trace", []) or [])
                errors.extend(dict(event, query=query) for event in getattr(result, "errors", []) or [])
        _emit("retrieving", 0,
              f"Running {len(all_queries)} search variants across {len(collections)} collection(s)")

        dm = self.direct_module  # local ref for closure

        if len(all_queries) <= 1:
            # Single query — no threading overhead
            for label, q in all_queries:
                try:
                    batch_result = dm.retrieve(
                        q, collections, **retrieve_kwargs,
                    )
                    _record(batch_result, q)
                    batch = batch_result.chunks if hasattr(batch_result, "chunks") else list(batch_result)
                except Exception as exc:
                    logger.exception(_ctx() + "retrieval failed for %s", label)
                    errors.append({"stage": "variant_retrieve", "query": q, "error": type(exc).__name__})
                    batch = []
                all_batches.append(batch)
        else:
            def _retrieve_one(q: str, cols: list[str], kwargs: dict):
                """Retrieve in thread — must not share mutable state."""
                try:
                    result = dm.retrieve(q, cols, **kwargs)
                    _record(result, q)
                    return result.chunks if hasattr(result, "chunks") else list(result)
                except Exception as exc:
                    logger.exception(_ctx() + "retrieval failed for variant")
                    with trace_lock:
                        errors.append({"stage": "variant_retrieve", "query": q, "error": type(exc).__name__})
                    return []

            # ContextThreadPoolExecutor：worker 里 dm.retrieve() 要读 get_actor()
            # 做可见性判定（见 src/async_ctx.py 的"线程池嵌套，每层都要管"）。
            from src.async_ctx import ContextThreadPoolExecutor

            with ContextThreadPoolExecutor(max_workers=len(all_queries)) as executor:
                futures = {}
                for label, q in all_queries:
                    futures[executor.submit(_retrieve_one, q, collections, retrieve_kwargs)] = label

                for f in as_completed(futures):
                    try:
                        batch = f.result()
                    except Exception:
                        logger.exception(_ctx() + "retrieval future failed")
                        batch = []
                    all_batches.append(batch)

        # ── ③ Merge + dedup ──────────────────────────────────────────────
        all_chunks: list = []
        for batch in all_batches:
            new_chunks, new_ids = _dedup_by_id(batch, state.seen_chunk_ids)
            state.seen_chunk_ids.update(new_ids)
            all_chunks.extend(new_chunks)

        all_chunks.sort(key=lambda c: c.score, reverse=True)
        state.all_chunks = all_chunks
        unique_before_grade = len(all_chunks)

        _emit("retrieving", 0,
              f"Retrieved {unique_before_grade} unique chunks across {len(all_queries)} search variants")

        # ── ④ Grade ──────────────────────────────────────────────────────
        if all_chunks:
            _emit("grading", 0, f"Grading {len(all_chunks)} chunks")
            try:
                node_combined_grade(state, all_chunks, llm=self.llm)
            except Exception as exc:
                logger.exception(_ctx() + "node_combined_grade raised")
                state.retained_chunks = all_chunks[:10]
                state.retained_info = "Grade step failed; raw retrieval results retained."
                state.current_gap_analysis = "Relevance and completeness remain unverified because grading failed."
                state.grade_error = type(exc).__name__
            if state.grade_error:
                errors.append({"stage": "grade", "query": query, "error": state.grade_error})
                trace.append({
                    "stage": "grade", "kind": "grading_fallback", "query": query,
                    "error": state.grade_error, "retained_count": len(state.retained_chunks),
                    "scope": "scoped" if original_user_question else "legacy",
                })
                _emit("grading", 0, "Grade could not verify all evidence; retained sources require checking",
                      error=True)
        else:
            _emit("grading", 0, "No chunks to grade")
            state.current_gap_analysis = "No relevant documents found."

        # ── Build result ──────────────────────────────────────────────────
        _emit("rewrite_loop_done", 0,
              f"Done: {len(state.retained_chunks)} retained chunks, "
              f"{len(variants)} variants, gaps={bool(state.current_gap_analysis)}")

        gap_flag = "!" if state.current_gap_analysis else "✓"
        logger.info(_ctx() + "done  chunks=%d/%d  gaps=%s",
                    len(state.retained_chunks), unique_before_grade, gap_flag)
        return VariantFetcherResult(
            chunks=list(state.retained_chunks),
            retrieved_chunks=list(state.all_chunks),
            retained_info=state.retained_info,
            gap_analysis=state.current_gap_analysis,
            steps=steps,
            query_used=query,
            variants=variants,
            total_retrieved=unique_before_grade,
            trace=trace, errors=errors,
        )

    @staticmethod
    def _parse_json(text: str) -> dict | list:
        """Parse JSON from LLM output, handling markdown fences."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)
