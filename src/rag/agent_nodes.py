"""Agentic RAG v2 node functions — pure functions for each pipeline stage.

Each node mutates AgentState in-place. Retrieval/grading helpers are module-private.

Grade is split into two phases:
  Part 1: relevance judgment (cheap, focused)
  Part 2: retained_info synthesis + gap analysis + sufficiency
"""

from __future__ import annotations

import json
import logging

from src.rag.agent_state import AgentState
from src.rag.agent_prompts import (
    GRADE_PART1_SYSTEM,
    GRADE_PART1_USER,
    GRADE_PART2_SYSTEM,
    GRADE_PART2_USER,
    REWRITE_SYSTEM,
    REWRITE_USER,
)
from src.rag.retriever import RetrievedChunk
from src.providers.base import LLMProvider

logger = logging.getLogger(__name__)
from src.rag import get_log_ctx as _ctx


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
    thinking: bool | None = None,
) -> dict | list:
    """Call LLM and parse JSON response. Retry with correction hint on failure."""
    last_error = None
    augmented_prompt = prompt
    for attempt in range(max_retries + 1):
        raw = llm.generate(augmented_prompt, system=system, temperature=temperature, max_tokens=1024, thinking=thinking).strip()
        try:
            return _parse_json(raw)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = e
            logger.warning(
                "[Grade] JSON parse retry %d/%d: %s",
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


# _retrieve_across_collections — REMOVED (migrated to DirectQueryModule.retrieve)
# _merge_and_rerank — REMOVED (migrated to DirectQueryModule.retrieve)

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

# node_retrieve_and_rerank — REMOVED
# (retrieval is now handled by DirectQueryModule + RewriteLoop)

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
        logger.info(_ctx() + "[Grade] skipped: empty batch")
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
    logger.info(_ctx() + "[Grade] relevance: judging %d chunks (%d retained so far)",
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
        result = _llm_generate_json(llm, prompt, GRADE_PART1_SYSTEM, temperature=temperature, thinking=True)
    except ValueError as e:
        logger.error(_ctx() + "[Grade] relevance: JSON parse failed — %s", e)
        _grade_part1_fallback(state, current_batch)
        return

    if not isinstance(result, dict):
        logger.error(_ctx() + "[Grade] relevance: expected dict, got %s", type(result))
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

    logger.info(_ctx() + "[Grade] relevance: %d/%d relevant → %d promoted",
                len(relevant_indices), len(current_batch), promoted)


def _grade_part1_fallback(state: AgentState, current_batch: list[RetrievedChunk]) -> None:
    """Conservative fallback: assume first min(3, len) chunks are relevant."""
    logger.warning(_ctx() + "[Grade] relevance: FALLBACK — keeping first %d chunks", min(3, len(current_batch)))
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
        logger.info(_ctx() + "[Grade] synthesize: skipped — no retained chunks")
        state.retained_info = "No relevant information found yet."
        state.is_sufficient = False
        state.current_gap_analysis = "No relevant information has been found."
        return

    logger.info(_ctx() + "[Grade] synthesize: %d retained chunks → LLM", len(state.retained_chunks))

    retained_chunks_text = _build_retained_chunks_text(state.retained_chunks)

    prompt = GRADE_PART2_USER.format(
        original_query=state.original_query,
        retained_chunks_text=retained_chunks_text,
    )

    try:
        result = _llm_generate_json(llm, prompt, GRADE_PART2_SYSTEM, temperature=temperature, thinking=True)
    except ValueError as e:
        logger.error(_ctx() + "[Grade] synthesize: JSON parse failed — %s", e)
        _grade_part2_fallback(state)
        return

    if not isinstance(result, dict):
        logger.error(_ctx() + "[Grade] synthesize: expected dict, got %s", type(result))
        _grade_part2_fallback(state)
        return

    state.retained_info = str(result.get("retained_info", state.retained_info))
    state.current_gap_analysis = str(result.get("gap_analysis", ""))
    state.is_sufficient = bool(result.get("is_sufficient", False))

    logger.info(_ctx() + "[Grade] synthesize: sufficient=%s info=%d chars gap=%r",
                state.is_sufficient, len(state.retained_info),
                state.current_gap_analysis[:80] if state.current_gap_analysis else "")


def _grade_part2_fallback(state: AgentState) -> None:
    """Fallback when Part 2 JSON parse fails: keep previous retained_info, mark insufficient."""
    logger.warning(_ctx() + "[Grade] synthesize: FALLBACK — keeping previous info")
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
    task_query: str = "",
    temperature: float | None = None,
) -> None:
    """Add query to history, then either rewrite or transition to decompose.

    Side effects:
    - Appends current_query to history_queries
    - If iteration < max: sets current_query to rewritten query, increments iteration_count
    - If iteration >= max: sets phase to "decompose"
    """
    state.history_queries.append(state.current_query)
    logger.info(_ctx() + "[Requery] history=%d iter=%d/%d",
                len(state.history_queries), state.iteration_count, state.max_iterations)

    if state.iteration_count >= state.max_iterations:
        logger.info(_ctx() + "[Requery] max iter %d reached", state.max_iterations)
        state.phase = "decompose"
        return

    # Generate new query (with task context and retained_info)
    history_text = "\n".join(f"- {q}" for q in state.history_queries)
    prompt = REWRITE_USER.format(
        original_query=state.original_query,
        task_query=task_query or "No additional task context.",
        retained_info=state.retained_info or "No information gathered yet.",
        gap_analysis=state.current_gap_analysis or "No relevant information found in previous searches.",
        history_queries=history_text,
    )

    try:
        result = _llm_generate_json(llm, prompt, REWRITE_SYSTEM, temperature=temperature, thinking=False)
        if isinstance(result, dict) and result.get("new_query"):
            new_query = str(result["new_query"]).strip()
            if new_query and new_query != state.current_query:
                state.current_query = new_query
                state.iteration_count += 1
                logger.info(_ctx() + "[Requery] → %r", new_query[:80])
                return
    except (ValueError, KeyError) as e:
        logger.warning(_ctx() + "[Requery] failed — %s, keeping original", e)

    # Fallback: keep original query but still increment
    state.iteration_count += 1
    logger.info(_ctx() + "[Requery] fallback, iter=%d", state.iteration_count)


# ══════════════════════════════════════════════════════════════════════════
# Node 4: Decompose Query
# ══════════════════════════════════════════════════════════════════════════

# node_decompose_query — REMOVED (moved to decomposer.py)
# node_parallel_sub_queries — REMOVED (replaced by AgenticQueryService fan-out)
