"""Agentic RAG v2 node functions — pure functions for each pipeline stage.

Each node mutates AgentState in-place. Retrieval/grading helpers are module-private.

Grade is split into two phases:
  Part 1: relevance judgment (cheap, focused)
  Part 2: retained_info synthesis + gap analysis + sufficiency
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.rag.agent_state import AgentState
from src.rag.agent_prompts import (
    GRADE_PART1_SYSTEM,
    GRADE_PART1_USER,
    GRADE_PART2_SYSTEM,
    GRADE_PART2_USER,
    REWRITE_SYSTEM,
    REWRITE_USER,
    DECOMPOSE_SYSTEM,
    DECOMPOSE_USER,
    SUB_GRADE_SYSTEM,
    SUB_GRADE_USER,
    GENERATE_SYSTEM,
    GENERATE_USER,
)
from src.rag.retriever import RetrievedChunk, Retriever
from src.rag.reranker import Reranker
from src.rag.collection_utils import retrieve_parent_child
from src.providers.base import EmbeddingProvider, LLMProvider
from src.db.qdrant import QdrantManager

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _parse_json(text: str) -> dict | list:
    """Parse JSON from LLM output, handling markdown fences and leading/trailing noise."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove opening fence line
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def _llm_generate_json(
    llm: LLMProvider,
    prompt: str,
    system: str,
    temperature: float | None = None,
    max_retries: int = 2,
) -> dict | list:
    """Call LLM and parse JSON response. Retry with correction hint on failure."""
    last_error = None
    augmented_prompt = prompt
    for attempt in range(max_retries + 1):
        raw = llm.generate(augmented_prompt, system=system, temperature=temperature).strip()
        try:
            return _parse_json(raw)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = e
            logger.warning(
                "[AgenticRAG] JSON parse failed (attempt %d/%d): %s",
                attempt + 1, max_retries + 1, e,
            )
            if attempt < max_retries:
                augmented_prompt = (
                    f"{prompt}\n\n"
                    "[SYSTEM NOTE: Your previous response was not valid JSON. "
                    "Respond with ONLY valid JSON. No markdown fences, no extra text. "
                    "Follow the requested JSON structure exactly.]"
                )
    raise ValueError(f"Failed to parse JSON after {max_retries + 1} attempts: {last_error}")


def _retrieve_across_collections(
    query: str,
    collections: list[str],
    top_k: int,
    *,
    retriever: Retriever,
    embedding_overrides: dict[str, EmbeddingProvider],
    search_mode: str = "dense",
    min_score: float = 0.0,
    db: QdrantManager | None = None,
    llm=None,
) -> list[RetrievedChunk]:
    """Retrieve chunks from multiple collections, merging results.

    Handles both normal and parent-child chunk modes per collection.
    """
    all_chunks: list[RetrievedChunk] = []
    for col in collections:
        emb = embedding_overrides.get(col)
        try:
            is_pc = db and db.get_collection_config(col).get("chunk_mode") == "parent_child"
            if is_pc:
                chunks, _ = retrieve_parent_child(
                    query, col, top_k,
                    embedding=emb, db=db, min_score=min_score,
                    retriever=retriever, search_mode=search_mode, llm=llm,
                )
            else:
                chunks = retriever.retrieve(
                    query, collection=col, top_k=top_k,
                    embedding_override=emb, search_mode=search_mode,
                    min_score=min_score, llm=llm,
                )
            logger.info(
                "[AgenticRAG] Collection '%s': %d chunks (pc=%s)",
                col, len(chunks), is_pc,
            )
            if chunks:
                for i, c in enumerate(chunks[:3]):
                    src = c.metadata.get("source", "?")
                    logger.info(
                        "[AgenticRAG]   chunk %d score=%.3f src=%s text=%s",
                        i + 1, c.score, str(src).rsplit("/", 1)[-1],
                        c.text[:120].replace("\n", " "),
                    )
                if len(chunks) > 3:
                    logger.info("[AgenticRAG]   ... +%d more chunks", len(chunks) - 3)
        except Exception as e:
            logger.error("[AgenticRAG] Retrieve failed for '%s': %s", col, e)
            chunks = []

        for c in chunks:
            c.metadata["collection"] = col
        all_chunks.extend(chunks)

    return all_chunks


def _merge_and_rerank(
    chunks: list[RetrievedChunk],
    query: str,
    top_k: int,
    *,
    reranker: Reranker | None,
) -> list[RetrievedChunk]:
    """Sort by score, rerank globally, return top_k."""
    if not chunks:
        return []
    # Sort by score descending so best across all collections survive
    chunks.sort(key=lambda c: c.score, reverse=True)
    # Rerank on more candidates than top_k for better quality
    rerank_input = chunks[:max(top_k * 2, len(chunks))]
    if reranker:
        try:
            reranked = reranker.rerank(query, rerank_input, top_k=top_k)
            logger.info("[AgenticRAG] Reranked %d -> %d chunks", len(rerank_input), len(reranked))
            if reranked:
                for i, c in enumerate(reranked):
                    src = c.metadata.get("source", "?")
                    col = c.metadata.get("collection", "?")
                    logger.info(
                        "[AgenticRAG]   reranked %d score=%.3f col=%s src=%s text=%s",
                        i + 1, c.score, col, str(src).rsplit("/", 1)[-1],
                        c.text[:120].replace("\n", " "),
                    )
            return reranked
        except Exception as e:
            logger.warning("[AgenticRAG] Rerank failed, using score-sorted results: %s", e)
    return rerank_input[:top_k]


def _dedup_by_id(
    chunks: list[RetrievedChunk],
    seen_ids: set[str],
) -> tuple[list[RetrievedChunk], set[str]]:
    """Filter chunks whose Qdrant point ID is already in seen_ids.

    Returns (new_chunks, updated_seen_ids). The seen_ids set is NOT mutated;
    the caller should update the state.
    """
    new_ids: set[str] = set()
    new_chunks: list[RetrievedChunk] = []
    for c in chunks:
        cid = c.metadata.get("id", "")
        if cid and cid not in seen_ids:
            new_ids.add(cid)
            new_chunks.append(c)
    return new_chunks, new_ids


def _build_retained_chunks_text(chunks: list[RetrievedChunk]) -> str:
    """Build formatted text from retained_chunks with source info, no truncation."""
    parts = []
    for i, c in enumerate(chunks):
        src = c.metadata.get("source", "unknown")
        col = c.metadata.get("collection", "unknown")
        parts.append(f"[{i}] (database: {col}, source: {src}) {c.text}")
    return "\n---\n".join(parts) if parts else "None"


# ══════════════════════════════════════════════════════════════════════════
# Node 1: Retrieve & Rerank
# ══════════════════════════════════════════════════════════════════════════

def node_retrieve_and_rerank(
    state: AgentState,
    *,
    retriever: Retriever,
    reranker: Reranker | None,
    embedding_overrides: dict[str, EmbeddingProvider],
    search_mode: str = "dense",
    min_score: float = 0.0,
    db: QdrantManager | None = None,
    llm=None,
) -> list[RetrievedChunk]:
    """Retrieve, rerank, dedup; returns fresh batch for grading.

    Side effects:
    - Adds new chunks to state.all_chunks
    - Adds new chunk IDs to state.seen_chunk_ids
    """
    result_top_k = state.rerank_top_k if state.rerank_top_k > 0 else state.top_k

    # 1. Retrieve across all collections
    raw_chunks = _retrieve_across_collections(
        state.current_query,
        state.collections,
        state.top_k,
        retriever=retriever,
        embedding_overrides=embedding_overrides,
        search_mode=search_mode,
        min_score=min_score,
        db=db,
        llm=llm,
    )

    if not raw_chunks:
        logger.info("[AgenticRAG] Node 1: zero results across all collections")
        return []

    # 2. Global rerank
    reranked = _merge_and_rerank(
        raw_chunks, state.current_query, result_top_k, reranker=reranker,
    )

    # 3. Dedup against seen_chunk_ids
    fresh, new_ids = _dedup_by_id(reranked, state.seen_chunk_ids)
    state.seen_chunk_ids.update(new_ids)
    logger.info("[AgenticRAG] N1 dedup: %d reranked -> %d fresh (already_seen=%d)",
                len(reranked), len(fresh), len(reranked) - len(fresh))

    # 4. Add to elite pool
    state.all_chunks.extend(fresh)

    logger.info("[AgenticRAG] N1 done: %d raw → %d reranked → %d fresh (elite=%d retained=%d seen=%d)",
                len(raw_chunks), len(reranked), len(fresh),
                len(state.all_chunks), len(state.retained_chunks), len(state.seen_chunk_ids))
    return fresh


def _chunk_in_list(chunk: RetrievedChunk, chunks: list[RetrievedChunk]) -> bool:
    """Check if chunk is already in list by Qdrant point ID."""
    cid = chunk.metadata.get("id", "")
    return any(c.metadata.get("id") == cid for c in chunks if cid)


# ══════════════════════════════════════════════════════════════════════════
# Node 2: LLM Grade (split into Part 1 + Part 2)
# ══════════════════════════════════════════════════════════════════════════

def node_llm_grade(
    state: AgentState,
    current_batch: list[RetrievedChunk],
    *,
    llm: LLMProvider,
    temperature: float | None = None,
) -> None:
    """Two-phase grade: Part 1 judges relevance, Part 2 synthesizes info.

    Side effects:
    - Part 1: Moves relevant chunks from current_batch to state.retained_chunks
    - Part 2: Updates state.retained_info, state.current_gap_analysis, state.is_sufficient
    """
    if not current_batch:
        logger.info("[AgenticRAG] N2 grade skipped (empty batch)")
        state.is_sufficient = False
        state.current_gap_analysis = "No new results found for the current query."
        return

    # ── Part 1: Relevance judgment ──────────────────────────────────
    _grade_part1(state, current_batch, llm=llm, temperature=temperature)

    # ── Part 2: Synthesize retained_info + gap + sufficient ────────
    node_update_retained_info(state, llm=llm, temperature=temperature)


def _grade_part1(
    state: AgentState,
    current_batch: list[RetrievedChunk],
    *,
    llm: LLMProvider,
    temperature: float | None = None,
) -> None:
    """Part 1: Judge relevance of candidate chunks. Promote relevant ones to retained_chunks."""
    logger.info("[AgenticRAG] N2-P1 grading %d chunks (retained so far: %d)",
                len(current_batch), len(state.retained_chunks))

    # Build candidate text with indices (no truncation)
    chunks_text_parts = []
    for i, c in enumerate(current_batch):
        src = c.metadata.get("source", "unknown")
        col = c.metadata.get("collection", "unknown")
        chunks_text_parts.append(f"[{i}] (database: {col}, source: {src}) {c.text}")
    chunks_text = "\n---\n".join(chunks_text_parts)

    prompt = GRADE_PART1_USER.format(
        original_query=state.original_query,
        chunks_text=chunks_text,
    )

    try:
        result = _llm_generate_json(llm, prompt, GRADE_PART1_SYSTEM, temperature=temperature)
    except ValueError as e:
        logger.error("[AgenticRAG] Part 1 grade JSON parsing failed: %s", e)
        _grade_part1_fallback(state, current_batch)
        return

    if not isinstance(result, dict):
        logger.error("[AgenticRAG] Part 1 grade result is not a dict: %s", type(result))
        _grade_part1_fallback(state, current_batch)
        return

    # Extract relevant indices
    relevant_indices: list[int] = []
    raw_indices = result.get("relevant_indices", [])
    if isinstance(raw_indices, list):
        n = len(current_batch)
        relevant_indices = [int(i) for i in raw_indices if isinstance(i, (int, float)) and 0 <= int(i) < n]

    # Promote relevant chunks to retained_chunks
    promoted = 0
    for idx in relevant_indices:
        chunk = current_batch[idx]
        if not _chunk_in_list(chunk, state.retained_chunks):
            state.retained_chunks.append(chunk)
            promoted += 1

    logger.info("[AgenticRAG] N2-P1 result: %d/%d relevant, promoted %d",
                len(relevant_indices), len(current_batch), promoted)


def _grade_part1_fallback(state: AgentState, current_batch: list[RetrievedChunk]) -> None:
    """Conservative fallback: assume first min(3, len) chunks are relevant."""
    logger.warning("[AgenticRAG] N2-P1 FALLBACK (taking first %d chunks)", min(3, len(current_batch)))
    for c in current_batch[:3]:
        if not _chunk_in_list(c, state.retained_chunks):
            state.retained_chunks.append(c)


def node_update_retained_info(
    state: AgentState,
    *,
    llm: LLMProvider,
    temperature: float | None = None,
) -> None:
    """Run Part 2 grade to update retained_info from current retained_chunks.

    Used both within the normal grade flow and as a standalone update after
    Phase 2 sub-query merging.
    """
    if not state.retained_chunks:
        logger.info("[AgenticRAG] N2-P2 skipped (no retained chunks)")
        state.retained_info = "No relevant information found yet."
        state.is_sufficient = False
        state.current_gap_analysis = "No relevant information has been found."
        return

    logger.info("[AgenticRAG] N2-P2 synthesizing from %d retained chunks", len(state.retained_chunks))

    retained_chunks_text = _build_retained_chunks_text(state.retained_chunks)

    prompt = GRADE_PART2_USER.format(
        original_query=state.original_query,
        retained_chunks_text=retained_chunks_text,
    )

    try:
        result = _llm_generate_json(llm, prompt, GRADE_PART2_SYSTEM, temperature=temperature)
    except ValueError as e:
        logger.error("[AgenticRAG] Part 2 grade JSON parsing failed: %s", e)
        _grade_part2_fallback(state)
        return

    if not isinstance(result, dict):
        logger.error("[AgenticRAG] Part 2 grade result is not a dict: %s", type(result))
        _grade_part2_fallback(state)
        return

    state.retained_info = str(result.get("retained_info", state.retained_info))
    state.current_gap_analysis = str(result.get("gap_analysis", ""))
    state.is_sufficient = bool(result.get("is_sufficient", False))

    logger.info("[AgenticRAG] N2-P2 result: sufficient=%s info_len=%d gap='%s'",
                state.is_sufficient, len(state.retained_info),
                state.current_gap_analysis[:120] if state.current_gap_analysis else "(none)")


def _grade_part2_fallback(state: AgentState) -> None:
    """Fallback when Part 2 JSON parse fails: keep previous retained_info, mark insufficient."""
    logger.warning("[AgenticRAG] N2-P2 FALLBACK (keeping previous retained_info)")
    if not state.retained_info:
        state.retained_info = "Unable to synthesize information summary."
    state.is_sufficient = False
    state.current_gap_analysis = "Unable to evaluate — the synthesizer did not produce valid output."


# ══════════════════════════════════════════════════════════════════════════
# Node 3: Check & Rewrite
# ══════════════════════════════════════════════════════════════════════════

def node_check_and_rewrite(
    state: AgentState,
    *,
    llm: LLMProvider,
    temperature: float | None = None,
) -> None:
    """Add query to history, then either rewrite or transition to decompose.

    Side effects:
    - Appends current_query to history_queries
    - If iteration < max: sets current_query to rewritten query, increments iteration_count
    - If iteration >= max: sets phase to "decompose"
    """
    state.history_queries.append(state.current_query)
    logger.info("[AgenticRAG] N3 history=%d queries, iter=%d/%d",
                len(state.history_queries), state.iteration_count, state.max_iterations)

    if state.iteration_count >= state.max_iterations:
        logger.info("[AgenticRAG] N3 max iterations (%d) reached, → P2 decompose", state.max_iterations)
        state.phase = "decompose"
        return

    # Generate new query (with retained_info context)
    history_text = "\n".join(f"- {q}" for q in state.history_queries)
    prompt = REWRITE_USER.format(
        original_query=state.original_query,
        retained_info=state.retained_info or "No information gathered yet.",
        gap_analysis=state.current_gap_analysis or "No relevant information found in previous searches.",
        history_queries=history_text,
    )

    try:
        result = _llm_generate_json(llm, prompt, REWRITE_SYSTEM, temperature=temperature)
        if isinstance(result, dict) and result.get("new_query"):
            new_query = str(result["new_query"]).strip()
            if new_query and new_query != state.current_query:
                state.current_query = new_query
                state.iteration_count += 1
                logger.info("[AgenticRAG] Node 3: rewritten query -> '%s'", new_query[:80])
                return
    except (ValueError, KeyError) as e:
        logger.warning("[AgenticRAG] Rewrite failed: %s, keeping original query", e)

    # Fallback: keep original query but still increment
    state.iteration_count += 1
    logger.info("[AgenticRAG] Node 3: rewrite fallback, iteration=%d", state.iteration_count)


# ══════════════════════════════════════════════════════════════════════════
# Node 4: Decompose Query
# ══════════════════════════════════════════════════════════════════════════

def node_decompose_query(
    state: AgentState,
    *,
    llm: LLMProvider,
    temperature: float | None = None,
) -> list[str]:
    """Break original_query into 2-3 sub-questions for independent retrieval.

    Returns list of sub-query strings.
    """
    prompt = DECOMPOSE_USER.format(
        original_query=state.original_query,
        retained_info=state.retained_info or "No information gathered yet.",
        gap_analysis=state.current_gap_analysis or "No relevant information has been found so far.",
    )

    try:
        result = _llm_generate_json(llm, prompt, DECOMPOSE_SYSTEM, temperature=temperature)
        if isinstance(result, list) and result:
            sub_queries = [str(s) for s in result if s]
            if sub_queries:
                logger.info("[AgenticRAG] N4 decomposed into %d sub-queries: %s",
                            len(sub_queries), [sq[:50] for sq in sub_queries])
                return sub_queries
    except (ValueError, KeyError) as e:
        logger.warning("[AgenticRAG] N4 decompose LLM failed: %s", e)

    # Fallback: use original query as single sub-question
    logger.info("[AgenticRAG] N4 fallback → single sub-query")
    return [state.original_query]


# ══════════════════════════════════════════════════════════════════════════
# Node 5: Parallel Sub-query Retrieval & Grade (no split — stays lightweight)
# ══════════════════════════════════════════════════════════════════════════

def node_parallel_sub_queries(
    state: AgentState,
    sub_queries: list[str],
    *,
    retriever: Retriever,
    reranker: Reranker | None,
    llm: LLMProvider,
    embedding_overrides: dict[str, EmbeddingProvider],
    search_mode: str = "dense",
    min_score: float = 0.0,
    db: QdrantManager | None = None,
    temperature: float | None = None,
) -> None:
    """Process each sub-query concurrently: retrieve -> rerank -> dedup -> grade.

    Each sub-query runs in its own thread. Results are merged into state.retained_chunks.
    """
    logger.info("[AgenticRAG] Node 5: processing %d sub-queries in parallel", len(sub_queries))

    def _process_one(sub_query: str, index: int) -> list[RetrievedChunk]:
        """Process a single sub-query and return relevant chunks."""
        logger.info("[AgenticRAG] Sub-query %d/%d: '%s'", index + 1, len(sub_queries), sub_query[:80])
        try:
            # Retrieve
            raw = _retrieve_across_collections(
                sub_query, state.collections, state.top_k,
                retriever=retriever, embedding_overrides=embedding_overrides,
                search_mode=search_mode, min_score=min_score, db=db, llm=llm,
            )
            if not raw:
                logger.info("[AgenticRAG] N5 sq%d: 0 raw results", index + 1)
                return []

            # Rerank
            result_top_k = state.rerank_top_k if state.rerank_top_k > 0 else state.top_k
            reranked = _merge_and_rerank(raw, sub_query, result_top_k, reranker=reranker)

            # Dedup
            fresh, _ = _dedup_by_id(reranked, state.seen_chunk_ids)

            if not fresh:
                logger.info("[AgenticRAG] N5 sq%d: all %d deduped", index + 1, len(reranked))
                return []

            # Grade with LLM (lightweight, no split)
            chunks_text_parts = [
                f"[{i}] {c.text[:600]}" for i, c in enumerate(fresh)
            ]
            chunks_text = "\n---\n".join(chunks_text_parts)
            prompt = SUB_GRADE_USER.format(
                original_query=state.original_query,
                sub_query=sub_query,
                chunks_text=chunks_text,
            )

            try:
                result = _llm_generate_json(llm, prompt, SUB_GRADE_SYSTEM, temperature=temperature)
            except ValueError:
                logger.warning("[AgenticRAG] Sub-query %d grade failed, taking first 3 chunks", index + 1)
                return fresh[:3]

            if not isinstance(result, dict):
                return fresh[:3]

            raw_indices = result.get("relevant_indices", [])
            if not isinstance(raw_indices, list):
                return fresh[:3]

            relevant_indices = [
                int(i) for i in raw_indices
                if isinstance(i, (int, float)) and 0 <= int(i) < len(fresh)
            ]
            relevant = [fresh[i] for i in relevant_indices]
            logger.info("[AgenticRAG] N5 sq%d: %d raw→%d reranked→%d fresh→%d relevant",
                        index + 1, len(raw), len(reranked), len(fresh), len(relevant))
            return relevant
        except Exception as e:
            logger.error("[AgenticRAG] N5 sq%d crashed: %s", index + 1, e)
            return []

    # Parallel execution
    with ThreadPoolExecutor(max_workers=min(len(sub_queries), 4)) as executor:
        futures = {
            executor.submit(_process_one, sq, i): i
            for i, sq in enumerate(sub_queries)
        }
        for future in as_completed(futures):
            relevant = future.result()
            # Merge into state.retained_chunks (thread-safe: single-thread merge)
            for c in relevant:
                if not _chunk_in_list(c, state.retained_chunks):
                    state.retained_chunks.append(c)
                # Also update seen_chunk_ids
                cid = c.metadata.get("id", "")
                if cid:
                    state.seen_chunk_ids.add(cid)

    logger.info("[AgenticRAG] N5 done: %d sub-queries → retained=%d chunks",
                len(sub_queries), len(state.retained_chunks))
    state.phase = "synthesize"


# ══════════════════════════════════════════════════════════════════════════
# Node 6: Build Context & Generate
# ══════════════════════════════════════════════════════════════════════════

def node_build_context(state: AgentState) -> str:
    """Build clustered context string from retained_chunks.

    Clustering: Database -> Source -> chunk_index order.
    Final dedup by chunk ID (safety check).
    """
    if not state.retained_chunks:
        return ""

    # Final dedup by chunk ID
    seen: set[str] = set()
    unique: list[RetrievedChunk] = []
    for c in state.retained_chunks:
        cid = c.metadata.get("id", "")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(c)
        elif not cid:
            # No ID — keep it (shouldn't happen in practice)
            unique.append(c)

    if not unique:
        return ""

    # Cluster: {collection: {source: [chunks sorted by chunk_index]}}
    clusters: dict[str, dict[str, list[RetrievedChunk]]] = defaultdict(lambda: defaultdict(list))
    for c in unique:
        col = c.metadata.get("collection", "Unknown DB")
        src = c.metadata.get("source", "Unknown Source")
        clusters[col][src].append(c)

    # Sort within each source by chunk_index
    for col_sources in clusters.values():
        for src_chunks in col_sources.values():
            src_chunks.sort(key=lambda c: c.metadata.get("chunk_index", 0))

    # Build context string
    parts: list[str] = []
    for col_name in sorted(clusters.keys()):
        col_sources = clusters[col_name]
        parts.append(f"## Database: {col_name}")
        for src_name in sorted(col_sources.keys()):
            src_chunks = col_sources[src_name]
            parts.append(f"### Source: {src_name}")
            for c in src_chunks:
                text = c.text
                # Prepend context/summary if available
                meta = c.metadata
                summ = meta.get("summary", "")
                ctx = meta.get("context", "")
                if summ:
                    text = f"[Document: {summ}]\n{text}"
                if ctx:
                    text = f"[Context: {ctx}]\n{text}"
                parts.append(text)
            parts.append("")  # blank line between sources

    context = "\n".join(parts)

    # Log exact assembly order
    assembly_order: list[str] = []
    for idx, c in enumerate(unique):
        col = c.metadata.get("collection", "Unknown DB")
        src = c.metadata.get("source", "Unknown Source")
        ci = c.metadata.get("chunk_index", "?")
        assembly_order.append(f"  [{idx}] {col} | {src} | chunk #{ci}")
    logger.info("[AgenticRAG] N6 assembly order (actual feed to LLM):\n%s", "\n".join(assembly_order))

    # Count stats for log
    col_count = len(clusters)
    src_count = sum(len(sources) for sources in clusters.values())
    # Multi-collection hint: warn LLM when chunks span different collections
    if col_count > 1:
        from src.collections import store as _cs
        col_list = sorted(
            _cs.get_collection_meta(c).get("name", c) if _cs.get_collection_meta(c) else c
            for c in clusters.keys()
        )
        hint = (
            f"[IMPORTANT: The following context comes from {col_count} DIFFERENT "
            f"collections: {', '.join(col_list)}. These are separate knowledge bases "
            f"that may contain overlapping or conflicting information. When answering, "
            f"be mindful of collection boundaries — note which collection each key "
            f"fact originates from, and do not assume consistency across collections.]"
        )
        context = hint + "\n\n" + context

    logger.info("[AgenticRAG] N6 context: %d chunks → %d DBs, %d sources, ~%d chars",
                len(unique), col_count, src_count, len(context))
    return context


def node_generate(
    state: AgentState,
    context: str,
    *,
    llm: LLMProvider,
    temperature: float | None = None,
) -> str:
    """Generate non-streaming answer from retained_info + clustered context."""
    if not context:
        return "I searched all relevant documents in the knowledge base but could not find information matching your query. Please try providing more keywords or rephrasing your question."

    prompt = GENERATE_USER.format(
        retained_info=state.retained_info or "No summary available.",
        context=context[:8000],
        question=state.original_query,
    )
    return llm.generate(prompt, system=GENERATE_SYSTEM, temperature=temperature).strip()


def node_generate_stream(
    state: AgentState,
    context: str,
    *,
    llm: LLMProvider,
    temperature: float | None = None,
):
    """Generator yielding tokens from LLM streaming."""
    if not context:
        yield "I searched all relevant documents but could not find information matching your query. Please try providing more keywords or rephrasing your question."
        return

    prompt = GENERATE_USER.format(
        retained_info=state.retained_info or "No summary available.",
        context=context[:8000],
        question=state.original_query,
    )
    yield from llm.generate_stream(prompt, system=GENERATE_SYSTEM, temperature=temperature)
