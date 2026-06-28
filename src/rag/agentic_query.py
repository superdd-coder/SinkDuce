"""AgenticQueryService — one-layer decompose → parallel VariantFetcher → group-aware aggregate."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from src.rag.decomposer import AtomicQuery
from src.rag.aggregator import SubQueryResult
from src.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)


@dataclass
class AgenticQueryResult:
    answer: str | None
    context: str = ""         # build_context(all_chunks) — always set
    all_chunks: list = field(default_factory=list)  # deduplicated, score desc
    tasks: list[dict] = field(default_factory=list)


# ── Inline generate prompt ────────────────────────────────────────────────

_GENERATE_ANSWER_SYSTEM = """You are a helpful research assistant. Answer the user's question based on the provided context and retained information.

Rules:
1. Use only the provided information — do NOT fabricate
2. Cite sources when possible
3. Be clear about what information is incomplete or missing
4. Use Markdown formatting for readability"""

_GENERATE_ANSWER_USER = """Question: {question}

Retained information:
{retained_info}

Relevant context:
{context}

Answer the question based on the above information."""


class AgenticQueryService:
    """One-layer Agentic RAG: decompose → parallel VariantFetcher → group-aware aggregate.

    Fires ``on_step(event)`` callbacks for progress reporting. Each event is a dict:

    .. code-block::

        {
          "step": str,       # decompose | task_start | aq_start | retrieving |
                             # grading | variant_generation | aq_done | synthesize_task | synthesize_merge
          "content": str,    # human-readable description
          "aq_id": str,      # set on aq_start, aq_done, retrieving, grading, variant_generation
          "iteration": int,  # set on variant-fetch events (always 0)
          "task": str,       # set on task_start, aq_start, aggregate_task
          "task_query": str, # set on task_start
          "aq_count": int,   # set on task_start
          "chunks": int,     # set on aq_done
          "has_gaps": bool,  # set on aq_done (bool(gap_analysis))
        }

    Callers can group events by ``aq_id`` to show parallel progress per AQ.
    """

    def __init__(self, direct_module, variant_fetcher, catalog, decomposer, aggregator, llm):
        self.direct_module = direct_module
        self.variant_fetcher = variant_fetcher
        self.catalog = catalog
        self.decomposer = decomposer
        self.aggregator = aggregator
        self.llm = llm
        self._max_parallel_queries = 8  # overridden by config.rag.max_parallel_queries on first run

    # ── public entry ──────────────────────────────────────────────────────

    def run(
        self,
        raw_query: str,
        *,
        collections: list[str] | None = None,
        generate_answer: bool = True,
        on_step=None,
        top_k: int = 20,
        rerank_enabled: bool = True,
        rerank_top_k: int = 5,
        search_mode: str = "dense",
        min_score: float = 0.0,
        sparse_llm_tokenize: bool = False,
        decompose: bool = True,
    ) -> AgenticQueryResult:
        logger.info("[Agentic] run q=%r gen_ans=%s decompose=%s top_k=%d rerank=%s rerank_top_k=%s mode=%s",
                    raw_query[:200], generate_answer, decompose, top_k, rerank_enabled, rerank_top_k, search_mode)

        if not raw_query or not raw_query.strip():
            return AgenticQueryResult(answer=None, context="", all_chunks=[])

        steps: list[dict] = []

        def _emit(step: str, content: str = "", **meta):
            event = {"step": step, "content": content, **meta}
            steps.append(event)
            if on_step:
                try:
                    on_step(event)
                except Exception:
                    logger.exception("[Agentic] on_step callback raised")

        # ── ① Decompose (one LLM call, skippable) ──────────────────────
        catalog_entries = self.catalog.get_catalog(collections)
        if decompose:
            _emit("decompose", f"Decomposing: {raw_query[:200]}")
            try:
                aqs = self.decomposer.decompose(raw_query, catalog_entries)
            except Exception as e:
                logger.exception("[Agentic] decompose failed")
                _emit("decompose", f"Decompose failed: {e}")
                aqs = [AtomicQuery(query=raw_query)]

            if not aqs:
                _emit("decompose", "No retrieval needed — non-retrieval query")
                return AgenticQueryResult(answer=None, context="", all_chunks=[])

            _emit("decompose", f"Found {len(aqs)} atomic query(s)")
        else:
            # Lightweight collection routing only — no task decomposition
            _emit("decompose", f"Routing: {raw_query[:200]}")
            target_cols = self.decomposer.route_collections(raw_query, catalog_entries)
            aqs = [AtomicQuery(query=raw_query, target_collections=target_cols,
                               task="(single)", task_query=raw_query)]
            _emit("decompose", f"Routed to {len(target_cols)} collection(s)"
                  if target_cols else "Routed to all collections")
        # emit: step="task_start", task, task_query, aq_count (once per task)
        tasks_seen = set()
        for aq in aqs:
            t = aq.task or "(single)"
            if t not in tasks_seen:
                tasks_seen.add(t)
                _emit("task_start", aq.task_query or aq.query[:120],
                      task=t, task_query=aq.task_query, aq_count=sum(1 for x in aqs if (x.task or "") == aq.task))

        # ── ② Fan-out: all AQs in parallel ─────────────────────────────
        # Pre-flight summary
        task_counts: dict[str, int] = {}
        for aq in aqs:
            t = aq.task or "(none)"
            task_counts[t] = task_counts.get(t, 0) + 1
        logger.info("[Agentic] fan-out: %d AQs, %d tasks %s",
                    len(aqs), len(task_counts), dict(task_counts))

        # emit: step="retrieve", content=summary count
        _emit("retrieve", f"Running {len(aqs)} atomic queries in parallel")

        # Resolve fallback collections: user-specified > all available
        _fallback_collections = list(collections) if collections else [
            e.id for e in catalog_entries
        ]
        sq_results: list[SubQueryResult] = []
        seen_chunk_ids: set[str] = set()
        _lock = threading.Lock()

        # Track pending counts per group for pipeline aggregation
        group_pending: dict[str, int] = {}
        group_results: dict[str, list[SubQueryResult]] = {}
        group_answers: dict[str, str] = {}
        for aq in aqs:
            g = aq.task or ""
            group_pending[g] = group_pending.get(g, 0) + 1
            group_results.setdefault(g, [])

        def _run_one(aq_item: AtomicQuery, aq_idx: int) -> SubQueryResult:
            from src.rag import set_log_ctx
            task_short = (aq_item.task or "")[:12]
            ctx = f"[aq{aq_idx}/{task_short}]" if task_short else f"[aq{aq_idx}]"
            set_log_ctx(ctx)
            # emit: step="aq_start", aq_id, task, content=query text
            _emit("aq_start", aq_item.query[:120], aq_id=f"aq{aq_idx}", task=aq_item.task or "")
            try:
                # Routing: LLM-specified > user-specified > all collections
                _aq_collections = (
                    aq_item.target_collections if aq_item.target_collections
                    else _fallback_collections
                )
                vf = self.variant_fetcher.run(
                    aq_item.query,
                    collections=_aq_collections,
                    task_query=aq_item.task_query or "",
                    top_k=top_k, rerank_enabled=rerank_enabled,
                    rerank_top_k=rerank_top_k, search_mode=search_mode,
                    min_score=min_score,
                    sparse_llm_tokenize=sparse_llm_tokenize,
                    # emit: step=retrieving|grading|variant_generation, aq_id, iteration, content
                    on_step=lambda step, iteration, content, extra=None: _emit(
                        step, content, aq_id=f"aq{aq_idx}", iteration=iteration,
                        **(extra if extra else {}),
                    ),
                )
                has_gaps = bool(vf.gap_analysis)
                sqr = SubQueryResult(
                    query=aq_item.query, retained_chunks=list(vf.chunks),
                    retained_info=vf.retained_info, gap_analysis=vf.gap_analysis,
                    task=aq_item.task or "", task_query=aq_item.task_query or "",
                )
                # emit: step="aq_done", aq_id, chunks, has_gaps, content=summary
                _emit("aq_done", f"{len(vf.chunks)} chunks" + (", has gaps" if has_gaps else ""),
                      aq_id=f"aq{aq_idx}", chunks=len(vf.chunks), has_gaps=has_gaps)
                return sqr
            except Exception:
                logger.exception("[Agentic] AQ failed: %s", aq_item.query)
                # emit: step="aq_done", aq_id, error=True (failure case)
                _emit("aq_done", "failed",
                      aq_id=f"aq{aq_idx}", chunks=0, has_gaps=True, error=True)
                return SubQueryResult(
                    query=aq_item.query, retained_chunks=[],
                    retained_info="", gap_analysis="AQ execution failed",
                    task=aq_item.task or "", task_query=aq_item.task_query or "",
                )
            finally:
                set_log_ctx("")

        max_parallel = self._max_parallel_queries
        try:
            from src.services import services
            if services.config:
                max_parallel = services.config.rag.max_parallel_queries
        except Exception:
            pass
        workers = min(len(aqs), max_parallel) if aqs else 1
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_run_one, aq, i): (aq, i)
                for i, aq in enumerate(aqs)
            }
            for f in as_completed(futures):
                sqr = f.result()
                if not sqr:
                    continue
                sq_results.append(sqr)

                g = sqr.task
                with _lock:
                    group_results[g].append(sqr)
                    group_pending[g] -= 1
                    if group_pending[g] == 0:
                        # All AQs in this group done → build context (no LLM)
                        if generate_answer and any(r.answer or r.retained_info for r in group_results[g]):
                            label = g or "(ungrouped)"
                            _emit("synthesize_task", f"Task complete: {label}",
                                  task=label, aq_count=len(group_results[g]))
                            logger.info("[Agentic]   ↳ task %r complete (%d AQs) → building context", label, len(group_results[g]))
                            group_answers[g] = self.aggregator.build_prompt(
                                list(group_results[g]),
                                original_query=raw_query if g else "",
                            )

        # ── ③ Final: concat task contexts → Chat LLM synthesizes ──────
        final_answer: str | None = None
        if generate_answer and sq_results:
            if len(group_answers) == 1:
                final_answer = f"<search_results>\n{list(group_answers.values())[0]}\n</search_results>"
            elif len(group_answers) > 1:
                _emit("synthesize_merge", "Merging task contexts")
                parts = "\n".join(group_answers.values())
                final_answer = f"<search_results>\n{parts}\n</search_results>"
                logger.info("[Agentic] merge %d task contexts → %d chars", len(group_answers), len(final_answer))
            else:
                # Pipeline didn't run (generate_answer=False or all AQs empty)
                final_answer = self.aggregator.build_prompt(
                    sq_results, original_query=raw_query,
                )

        # ── Collect chunks (dedup by ID, sort by score desc) ───────────
        all_chunks: list[RetrievedChunk] = []
        for sq in sq_results:
            for c in (sq.retained_chunks or []):
                cid = c.metadata.get("id", "") if hasattr(c, "metadata") else ""
                if cid and cid not in seen_chunk_ids:
                    seen_chunk_ids.add(cid)
                    all_chunks.append(c)
        all_chunks.sort(key=lambda c: c.score, reverse=True)

        # Build aggregate prompt as context (always, even without generate_answer)
        context = ""
        if sq_results:
            try:
                context = self.aggregator.build_prompt(
                    sq_results, original_query=raw_query,
                )
            except Exception:
                logger.exception("[Agentic] build_prompt failed")

        # Build task detail (backward-compat format)
        task_details = [{
            "task_id": "aqs",
            "task_query": raw_query,
            "sub_queries": [
                {
                    "query": sq.query,
                    "target_collections": aqs[i].target_collections if i < len(aqs) else [],
                    "chunks": [
                        {"id": c.metadata.get("id", ""), "text": c.text[:200]}
                        if hasattr(c, "text") else c
                        for c in (sq.retained_chunks or [])
                    ],
                    "retained_info": sq.retained_info,
                    "gap_analysis": sq.gap_analysis,
                    "answer": sq.answer,
                }
                for i, sq in enumerate(sq_results)
            ],
        }]

        logger.info("[Agentic] done — aqs=%d chunks=%d answer_len=%d",
                    len(aqs), len(all_chunks), len(final_answer) if final_answer else 0)
        return AgenticQueryResult(
            answer=final_answer, context=context,
            all_chunks=all_chunks, tasks=task_details,
        )
