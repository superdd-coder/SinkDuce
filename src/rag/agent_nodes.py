"""Agentic RAG v2 node functions — pure functions for each pipeline stage.

Each node mutates AgentState in-place. Retrieval/grading helpers are module-private.

Grade is a single combined phase: relevance judgment + knowledge record update
in one LLM call, using retained_info summary as context across iterations.
"""

from __future__ import annotations

import json
import logging

from src.rag.agent_state import AgentState
from src.rag.agent_prompts import (
    GRADE_COMBINED_SYSTEM,
    GRADE_COMBINED_USER,
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
    """Parse JSON from LLM output, handling markdown fences and common repair."""
    import re

    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Repair: try escaping bare newlines inside JSON strings
    # A newline that appears between an opening quote and closing quote
    # inside a JSON value is likely an unescaped newline
    repaired = re.sub(
        r'(?<=[^\\])\\n', r'\\\\n',
        text,
    )
    # Also handle literal newlines that break JSON string values:
    # find "key": "value\nwith\nnewlines" → escape them
    # Simpler approach: escape ALL bare newlines that aren't structural
    repaired = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            repaired.append(ch)
            escape_next = False
            continue
        if ch == '\\':
            repaired.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            repaired.append(ch)
            continue
        if in_string and ch == '\n':
            repaired.append('\\n')
        else:
            repaired.append(ch)
    repaired_text = ''.join(repaired)

    return json.loads(repaired_text)


def _llm_generate_json(
    llm: LLMProvider,
    prompt: str,
    system: str,
    temperature: float | None = None,
    max_retries: int = 2,
    thinking: bool | None = None,
    max_tokens: int = 1024,
) -> dict | list:
    """Call LLM and parse JSON response. Retry with correction hint on failure."""
    last_error = None
    augmented_prompt = prompt
    for attempt in range(max_retries + 1):
        raw = llm.generate(augmented_prompt, system=system, temperature=temperature, max_tokens=max_tokens, thinking=thinking).strip()
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
# Node 2: Combined Grade — relevance + knowledge record in one LLM call
# ══════════════════════════════════════════════════════════════════════════

def node_combined_grade(
    state: AgentState,
    current_batch: list[RetrievedChunk],
    *,
    llm: LLMProvider,
    temperature: float | None = None,
) -> None:
    """Combined grade: relevance judgment + knowledge record update in one LLM call.

    Uses retained_info summary (not full chunk text) for previous knowledge,
    keeping prompt size bounded across iterations.

    Side effects:
    - Promotes relevant chunks from current_batch to state.retained_chunks
    - Updates state.retained_info, state.current_gap_analysis, state.is_sufficient
    """
    if not current_batch:
        logger.info(_ctx() + "[Grade] skipped: empty batch")
        state.is_sufficient = False
        state.current_gap_analysis = "No new results found for the current query."
        return

    logger.info(_ctx() + "[Grade] combined: judging %d candidates (%d retained so far)",
                len(current_batch), len(state.retained_chunks))

    # Build candidate chunks text (full text, same format as before)
    chunks_text_parts = []
    for i, c in enumerate(current_batch):
        src = c.metadata.get("source", "unknown")
        col = c.metadata.get("collection", "unknown")
        chunks_text_parts.append(f"[{i}] (database: {col}, source: {src}) {c.text}")
    chunks_text = "\n---\n".join(chunks_text_parts)

    # Previous knowledge / gaps (retained_info summary, not full chunk text)
    previous_knowledge = state.retained_info if state.retained_info else "(none — this is the first search round)"
    previous_gaps = state.current_gap_analysis if state.current_gap_analysis else "(none)"

    prompt = GRADE_COMBINED_USER.format(
        original_query=state.original_query,
        previous_knowledge=previous_knowledge,
        previous_gaps=previous_gaps,
        chunks_text=chunks_text,
    )

    try:
        result = _llm_generate_json(
            llm, prompt, GRADE_COMBINED_SYSTEM,
            temperature=temperature, thinking=False, max_tokens=8192,
        )
    except ValueError as e:
        logger.error(_ctx() + "[Grade] combined: JSON parse failed — %s", e)
        _combined_grade_fallback(state, current_batch)
        return

    if not isinstance(result, dict):
        logger.error(_ctx() + "[Grade] combined: expected dict, got %s", type(result))
        _combined_grade_fallback(state, current_batch)
        return

    # ── Extract relevant indices & promote chunks ───────────────────
    relevant_indices: list[int] = []
    raw_indices = result.get("relevant_indices", [])
    if isinstance(raw_indices, list):
        n = len(current_batch)
        relevant_indices = [int(i) for i in raw_indices if isinstance(i, (int, float)) and 0 <= int(i) < n]

    promoted = 0
    for idx in relevant_indices:
        chunk = current_batch[idx]
        if not _chunk_in_list(chunk, state.retained_chunks):
            state.retained_chunks.append(chunk)
            promoted += 1

    logger.info(_ctx() + "[Grade] combined: %d/%d relevant → %d promoted",
                len(relevant_indices), len(current_batch), promoted)

    # ── Update knowledge record fields ──────────────────────────────
    state.retained_info = str(result.get("retained_info", state.retained_info))
    state.current_gap_analysis = str(result.get("gap_analysis", ""))
    state.is_sufficient = bool(result.get("is_sufficient", False))

    logger.info(_ctx() + "[Grade] combined: sufficient=%s info=%d chars gap=%r",
                state.is_sufficient, len(state.retained_info),
                state.current_gap_analysis[:80] if state.current_gap_analysis else "")


def _combined_grade_fallback(state: AgentState, current_batch: list[RetrievedChunk]) -> None:
    """Fallback: promote first 3 chunks, mark insufficient."""
    logger.warning(_ctx() + "[Grade] combined: FALLBACK — keeping first %d chunks", min(3, len(current_batch)))
    for c in current_batch[:3]:
        if not _chunk_in_list(c, state.retained_chunks):
            state.retained_chunks.append(c)
    if not state.retained_info:
        state.retained_info = "Unable to evaluate relevance — fallback mode."
    state.is_sufficient = False
    state.current_gap_analysis = "Unable to evaluate — the grader did not produce valid output."


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
