"""Agentic queries: preliminary evidence → plan gaps → fetch → aggregate."""

from __future__ import annotations

import logging
import threading
import copy
import html
from concurrent.futures import as_completed
from dataclasses import dataclass, field, replace

from src.parsers.image_utils import _IMAGE_BLOCK_RE
from src.rag.decomposer import AtomicQuery, Decomposer
from src.rag.aggregator import SubQueryResult
from src.rag.retriever import RetrievedChunk
from src.rag.source_scope import is_qa_chat_chunk, qa_chat_projects, qa_chat_scope

logger = logging.getLogger(__name__)


def _collection_visible(collection: str) -> bool:
    """Recheck the current request actor before planning or reusing evidence."""
    try:
        from src.identity import get_actor
        from src.identity.visibility import is_collection_visible
        return is_collection_visible(get_actor(), collection) is True
    except Exception:
        logger.exception("[Agentic] preliminary source visibility check failed")
        return False


@dataclass
class AgenticQueryResult:
    answer: str | list[dict] | None   # str when include_images=False; list[dict] (multimodal) when True
    context: str = ""                 # build_context(all_chunks) — always str
    all_chunks: list = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    images: dict[str, dict] = field(default_factory=dict)  # image_id → {base64, mime}
    candidate_chunks: list = field(default_factory=list)  # pre-grade candidates
    retained_chunks: list = field(default_factory=list)   # actual context evidence
    trace: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    qa_hits: list[RetrievedChunk] = field(default_factory=list)
    coverage_complete: bool = False


def _subquery_result(aq, targets, fetched, *, qa_enabled):
    retrieved = list(getattr(fetched, "retrieved_chunks", None) or [])
    retained = list(fetched.chunks)
    qa_hits = []
    retained_info = fetched.retained_info
    gap_analysis = fetched.gap_analysis
    errors = list(getattr(fetched, "errors", None) or [])
    if qa_enabled and any(is_qa_chat_chunk(c) for c in retrieved + retained):
        grade_error = next((error for error in errors if error.get("stage") == "grade"), None)
        if grade_error:
            errors.append({**grade_error, "stage": "qa_grade"})
        else:
            qa_hits = [c for c in retained if is_qa_chat_chunk(c)]
        retrieved = [c for c in retrieved if not is_qa_chat_chunk(c)]
        retained = [c for c in retained if not is_qa_chat_chunk(c)]
        # Chat revalidates Q&A before presenting it.
        retained_info = ""
        gap_analysis = "Required evidence remains missing." if gap_analysis else ""
    return SubQueryResult(
        query=aq.query, retained_chunks=retained, qa_hits=qa_hits,
        retained_info=retained_info, gap_analysis=gap_analysis,
        task=aq.task or "", task_query=aq.task_query or "",
        retrieved_chunks=retrieved, target_collections=targets,
        trace=list(getattr(fetched, "trace", None) or []),
        errors=errors,
    )


# ── Inline generate prompt ────────────────────────────────────────────────

def _stitch_images_from_chunks(chunks: list) -> dict[str, dict]:
    """Extract image references from chunk metadata and encode them as base64.

    Each chunk's metadata carries ``collection`` (set during retrieval),
    so image path resolution is O(1) — no directory scanning.

    Returns a dict mapping image_id → {base64, mime} for all images
    referenced in the chunks' metadata.
    """
    from src.parsers.image_utils import _encode_image_base64_direct, encode_image_base64, iter_image_fence_fields

    images: dict[str, dict] = {}
    seen: set[str] = set()
    total_refs = 0
    not_found = 0

    for chunk in chunks:
        meta = getattr(chunk, "metadata", {}) if hasattr(chunk, "metadata") else {}
        col = meta.get("collection", "")
        raw_refs = meta.get("images", [])
        chunk_images = list(raw_refs) if isinstance(raw_refs, list) else []
        # Metadata may be partial. Include every textual reference as well,
        # without appending to the canonical chunk's metadata list.
        chunk_images += iter_image_fence_fields(getattr(chunk, "text", "") or "")
        for img_ref in chunk_images:
            if not isinstance(img_ref, dict):
                continue
            img_id = img_ref.get("image_id", "")
            file_id = img_ref.get("file_id", "") or meta.get("file_id", "")
            if not img_id or not file_id:
                continue
            if img_id in seen:
                continue
            seen.add(img_id)
            total_refs += 1
            if col:
                encoded = _encode_image_base64_direct(img_id, file_id, col)
            else:
                encoded = encode_image_base64(img_id, file_id)
            if encoded:
                b64, mime = encoded
                images[img_id] = {"base64": b64, "mime": mime}
            else:
                not_found += 1

    logger.info(
        "[ImageStitch] chunks=%d refs=%d found=%d not_found=%d",
        len(chunks), total_refs, len(images), not_found,
    )
    return images


def _build_multimodal_context(
    content: str,
    images: dict[str, dict],
    skip_ids: set[str] | None = None,
    *,
    include_alt_text: bool = False,
) -> list[dict]:
    """Compatibility wrapper: originals replace machine readings when available.

    ``include_alt_text`` is retained for callers using the older signature;
    attached originals always supersede OCR and generated descriptions.
    """
    from src.rag.image_evidence import render_image_evidence
    rendered = render_image_evidence(content, images, is_visual=True, skip_ids=skip_ids)
    return rendered if isinstance(rendered, list) else [{"type": "text", "text": rendered}]


class AgenticQueryService:
    """Agentic RAG with preliminary-source reuse and parallel gap retrieval.

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
        include_images: bool = False,
        on_step=None,
        top_k: int = 20,
        rerank_enabled: bool = True,
        rerank_top_k: int = 5,
        search_mode: str = "dense",
        min_score: float = 0.0,
        sparse_llm_tokenize: bool = False,
        decompose: bool = True,
        include_archived: bool = False,
        original_user_question: str = "",
        preliminary_retrieval: bool = True,
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

        qa_projects = qa_chat_projects.get()
        if qa_projects:
            requested = collections if collections is not None else qa_projects
            collections = [project for project in qa_projects if project in requested]
            if not collections:
                return AgenticQueryResult(answer=None)
            include_archived = False

        planning_trace: list[dict] = []
        fetch_options = dict(
            original_user_question=original_user_question, top_k=top_k,
            rerank_enabled=rerank_enabled, rerank_top_k=rerank_top_k,
            search_mode=search_mode, min_score=min_score,
            sparse_llm_tokenize=sparse_llm_tokenize,
            llm_for_sparse=self.llm if sparse_llm_tokenize else None,
        )
        probe = None
        probe_adequate = False
        planned = False
        complete = False
        scoped_first_pass = bool(qa_projects and len(collections) == 1 and preliminary_retrieval and original_user_question)
        if scoped_first_pass:
            _emit("aq_start", raw_query[:120], aq_id="scoped", task="")
            try:
                fetched = self.variant_fetcher.run(
                    raw_query, collections=collections, variant_count=0,
                    grade_query=original_user_question, **fetch_options,
                    on_step=lambda step, iteration, content, extra=None: _emit(
                        step, content, aq_id="scoped", iteration=iteration, **(extra or {}),
                    ),
                )
                probe = _subquery_result(
                    AtomicQuery(query=raw_query, task_query=raw_query), collections, fetched, qa_enabled=True,
                )
                has_answer_evidence = bool(probe.retained_chunks) or any(
                    hit.metadata.get("source_kind") == "qa_reply" for hit in probe.qa_hits
                )
                probe_adequate = has_answer_evidence and not fetched.gap_analysis and not probe.errors
                complete = probe_adequate and not decompose
                _emit("aq_done", f"{len(fetched.chunks)} chunks", aq_id="scoped",
                      chunks=len(fetched.chunks), has_gaps=not probe_adequate)
                planning_trace.append({
                    "kind": "scoped_mixed_first_pass", "coverage_complete": complete,
                    "collections": list(collections),
                })
            except Exception as exc:
                logger.warning("[Agentic] scoped mixed search failed (%s)", type(exc).__name__)
                _emit("aq_done", "failed", aq_id="scoped", chunks=0, has_gaps=True, error=True)

        # ── ① Decompose when the first search leaves a gap ────────────
        catalog_entries = [] if complete else self.catalog.get_catalog(
            collections, include_archived=include_archived
        )
        if complete:
            aqs = []
        elif decompose:
            _emit("decompose", f"Decomposing: {raw_query[:200]}")
            seed_chunks = []
            if probe and not probe.errors and isinstance(self.decomposer, Decomposer):
                from src.rag.evidence_planner import select_seed_chunks

                seed_chunks = select_seed_chunks(probe.retrieved_chunks, collections)
            elif preliminary_retrieval and not scoped_first_pass and isinstance(self.decomposer, Decomposer):
                from src.rag.evidence_planner import select_seed_chunks

                seed_collections = [
                    e.id if hasattr(e, "id") else e.get("id", "")
                    for e in catalog_entries
                ]
                seed_collections = list(dict.fromkeys(
                    c for c in seed_collections
                    if c and (not collections or c in collections) and _collection_visible(c)
                ))
                if seed_collections:
                    _emit("retrieving", "Retrieving preliminary evidence before planning",
                          aq_id="preliminary", iteration=0)
                    try:
                        with qa_chat_scope(()):
                            preliminary = self.direct_module.retrieve(
                                raw_query, seed_collections, top_k=top_k,
                                rerank_enabled=rerank_enabled, rerank_top_k=rerank_top_k,
                                search_mode=search_mode, min_score=min_score,
                                sparse_llm_tokenize=sparse_llm_tokenize,
                                llm_for_sparse=self.llm if sparse_llm_tokenize else None,
                                generate_answer=False,
                            )
                        if preliminary.errors:
                            _emit("retrieving", "Preliminary retrieval failed", aq_id="preliminary", error=True)
                            return AgenticQueryResult(
                                answer=None, errors=list(preliminary.errors),
                                trace=list(preliminary.trace),
                            )
                        seed_chunks = select_seed_chunks(preliminary.chunks, seed_collections)
                        planning_trace = list(preliminary.trace) + [{
                            "kind": "preliminary_retrieval_before_decomposition", "query": raw_query,
                            "collections": seed_collections, "source_count": len(seed_chunks),
                            "source_ids": [(c.metadata["collection"], c.metadata["id"]) for c in seed_chunks],
                        }]
                    except Exception as exc:
                        logger.exception("[Agentic] preliminary retrieval failed")
                        _emit("retrieving", "Preliminary retrieval failed", aq_id="preliminary", error=True)
                        return AgenticQueryResult(answer=None, errors=[{
                            "stage": "preliminary_retrieval", "error": type(exc).__name__,
                        }])
            try:
                _emit("decompose", "Planning searches from preliminary evidence"
                      if seed_chunks else "Planning searches")
                if seed_chunks:
                    aqs = self.decomposer.plan_with_evidence(
                        raw_query, catalog_entries, seed_chunks,
                        original_user_question=original_user_question,
                    )
                else:
                    aqs = self.decomposer.decompose(
                        raw_query, catalog_entries, original_user_question=original_user_question,
                    )
                planned = bool(aqs)
            except Exception as e:
                logger.exception("[Agentic] decompose failed")
                _emit("decompose", f"Decompose failed: {e}")
                if seed_chunks and not qa_projects:
                    return AgenticQueryResult(answer=None, trace=planning_trace, errors=[{
                        "stage": "evidence_plan", "error": type(e).__name__,
                    }])
                aqs = [AtomicQuery(query=raw_query)]

            if not aqs and not probe:
                _emit("decompose", "No retrieval needed — non-retrieval query")
                return AgenticQueryResult(answer=None, context="", all_chunks=[], trace=planning_trace)

            _emit("decompose", f"Found {len(aqs)} atomic query(s)")
        else:
            # Lightweight collection routing only — no task decomposition
            _emit("decompose", f"Routing: {raw_query[:200]}")
            target_cols = list(collections) if qa_projects else self.decomposer.route_collections(raw_query, catalog_entries)
            aqs = [AtomicQuery(query=raw_query, target_collections=target_cols,
                               task="(single)", task_query=raw_query)]
            _emit("decompose", f"Routed to {len(target_cols)} collection(s)"
                  if target_cols else "Routed to all collections")
        if qa_projects and not complete:
            # Preserve evidence for every project in scope, including those
            # that the search planner omitted.
            aqs = [
                replace(aq, target_collections=[project])
                for aq in aqs
                for project in (aq.target_collections or collections)
                if project in collections
            ]
            planned_projects = {aq.target_collections[0] for aq in aqs}
            aqs.extend(
                AtomicQuery(query=raw_query, target_collections=[project], task_query=raw_query)
                for project in collections if project not in planned_projects
            )

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
        sq_results: list[SubQueryResult] = [probe] if probe else []
        graded_documents = {
            (chunk.metadata.get("collection"), chunk.metadata.get("id"))
            for chunk in probe.retained_chunks
        } if probe and not probe.errors else set()
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
                if aq_item.covered_chunks and (not qa_projects or all(
                    (chunk.metadata.get("collection"), chunk.metadata.get("id")) in graded_documents
                    for chunk in aq_item.covered_chunks
                )):
                    from src.rag.variant_fetcher import VariantFetcherResult

                    chunks = copy.deepcopy(aq_item.covered_chunks)
                    if any(
                        c.metadata.get("collection") not in _aq_collections
                        or c.metadata.get("is_current") is not True
                        or not _collection_visible(c.metadata.get("collection", ""))
                        for c in chunks
                    ):
                        raise RuntimeError("Preliminary source is no longer available for reuse")
                    vf = VariantFetcherResult(
                        chunks=chunks, retrieved_chunks=copy.deepcopy(chunks),
                        retained_info="\n".join(html.escape(q, quote=False) for q in aq_item.covered_quotes),
                        query_used=aq_item.query,
                        trace=[{
                            "kind": "covered_need_source_reuse", "query": aq_item.query,
                            "source_ids": [(c.metadata["collection"], c.metadata["id"]) for c in chunks],
                            "new_retrieval_calls": 0, "native_variant_and_grade_skipped": True,
                            "semantic_entailment_certified": False,
                        }],
                    )
                else:
                    vf = self.variant_fetcher.run(
                        aq_item.query,
                        collections=_aq_collections,
                        task_query=aq_item.task_query or "",
                        **fetch_options,
                        # emit: step=retrieving|grading|variant_generation, aq_id, iteration, content
                        on_step=lambda step, iteration, content, extra=None: _emit(
                            step, content, aq_id=f"aq{aq_idx}", iteration=iteration,
                            **(extra if extra else {}),
                        ),
                    )
                has_gaps = bool(vf.gap_analysis)
                sqr = _subquery_result(aq_item, _aq_collections, vf, qa_enabled=bool(qa_projects))
                # emit: step="aq_done", aq_id, chunks, has_gaps, content=summary
                _emit("aq_done", f"{len(vf.chunks)} chunks" + (", has gaps" if has_gaps else ""),
                      aq_id=f"aq{aq_idx}", chunks=len(vf.chunks), has_gaps=has_gaps)
                return sqr
            except Exception as exc:
                logger.exception("[Agentic] AQ failed: %s", aq_item.query)
                # emit: step="aq_done", aq_id, error=True (failure case)
                _emit("aq_done", "failed",
                      aq_id=f"aq{aq_idx}", chunks=0, has_gaps=True, error=True)
                return SubQueryResult(
                    query=aq_item.query, retained_chunks=[],
                    retained_info="", gap_analysis="AQ execution failed",
                    task=aq_item.task or "", task_query=aq_item.task_query or "",
                    target_collections=aq_item.target_collections or _fallback_collections,
                    errors=[{"stage": "atomic_query", "query": aq_item.query,
                             "error": type(exc).__name__}],
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
        # ContextThreadPoolExecutor：每 AQ 的 worker 会一路调到 Retriever 里的
        # is_collection_visible(get_actor())，裸池会让身份退回 owner → 过滤关掉。
        # 见 src/async_ctx.py 的"线程池嵌套，每一层都要管"。
        from src.async_ctx import ContextThreadPoolExecutor

        with ContextThreadPoolExecutor(max_workers=workers) as executor:
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
            if probe:
                final_answer = self.aggregator.build_prompt(sq_results, original_query=raw_query)
            elif len(group_answers) == 1:
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
        # Prefer grade-retained chunks. When the grader drops everything
        # (common on hard queries), surface pre-grade retrieval candidates
        # so Search/Recall can still inspect ranked hits + agent context.
        all_chunks: list[RetrievedChunk] = []
        for sq in sq_results:
            source = list(sq.retained_chunks or [])
            if not source and not qa_projects:
                source = list(getattr(sq, "retrieved_chunks", None) or [])
            for c in source:
                meta = c.metadata if hasattr(c, "metadata") else {}
                cid = (
                    (meta.get("id") or meta.get("chunk_id") or "")
                    if isinstance(meta, dict) else ""
                )
                if not cid:
                    # Still include chunks missing ids (dedupe by text prefix)
                    cid = f"txt:{hash((getattr(c, 'text', '') or '')[:240])}"
                if cid not in seen_chunk_ids:
                    seen_chunk_ids.add(cid)
                    all_chunks.append(c)
        all_chunks.sort(key=lambda c: c.score, reverse=True)
        candidate_by_id = {}
        for sq in sq_results:
            for chunk in sq.retrieved_chunks or sq.retained_chunks:
                cid = (chunk.metadata.get("collection", ""),
                       chunk.metadata.get("id") or chunk.metadata.get("chunk_id") or chunk.text)
                candidate_by_id.setdefault(cid, chunk)
        candidate_chunks = sorted(candidate_by_id.values(), key=lambda c: c.score, reverse=True)
        from src.rag.aggregator import Aggregator
        retained_chunks = Aggregator.context_chunks(sq_results)

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
                    "target_collections": sq.target_collections,
                    "chunks": [
                        {"id": c.metadata.get("id", ""), "text": c.text[:200]}
                        if hasattr(c, "text") else c
                        for c in (sq.retained_chunks or [])
                    ],
                    "retained_info": sq.retained_info,
                    "gap_analysis": sq.gap_analysis,
                    "answer": sq.answer,
                }
                for sq in sq_results
            ],
        }]

        logger.info("[Agentic] done — aqs=%d chunks=%d answer_len=%d",
                    len(aqs), len(all_chunks), len(final_answer) if final_answer else 0)

        # ── Image stitching ─────────────────────────────────────────
        images_payload: dict[str, dict] = {}
        if include_images and all_chunks:
            images_payload = _stitch_images_from_chunks(all_chunks)
            if images_payload:
                target = final_answer or context
                if target:
                    final_answer = _build_multimodal_context(target, images_payload)
                    logger.info(
                        "[Agentic] IMAGE STITCH: type=%s parts=%d images=%d",
                        type(final_answer).__name__,
                        len(final_answer) if isinstance(final_answer, list) else 0,
                        len(images_payload),
                    )
            else:
                logger.debug(
                    "[Agentic] include_images=True, no embeddable images in %d chunks",
                    len(all_chunks),
                )

        # A scoped grade only judges its candidates. With planning enabled,
        # every planned need must also have supporting evidence before Chat
        # closes further search for the turn.
        coverage_complete = complete or (probe_adequate and planned and all(
            (sq.retained_chunks or sq.qa_hits) and not sq.gap_analysis and not sq.errors
            for sq in sq_results
        ))
        return AgenticQueryResult(
            answer=final_answer, context=context,
            all_chunks=all_chunks, tasks=task_details,
            images=images_payload,
            candidate_chunks=candidate_chunks, retained_chunks=retained_chunks,
            trace=planning_trace + [event for sq in sq_results for event in sq.trace],
            errors=[event for sq in sq_results for event in sq.errors],
            qa_hits=[hit for sq in sq_results for hit in sq.qa_hits],
            coverage_complete=coverage_complete,
        )
