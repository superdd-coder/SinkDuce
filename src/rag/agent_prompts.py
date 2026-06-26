"""LLM prompt templates for the Agentic RAG v2 pipeline.

Each prompt has a SYSTEM (cached as prefix) and USER (variable per call) part.

Grade is split into two phases:
  Part 1: relevance judgment only (cheap, focused)
  Part 2: retained_info + gap_analysis + is_sufficient (rich reasoning)
"""

# ══════════════════════════════════════════════════════════════════════════
# Node 2a: Grade Part 1 — relevance judgment only
# ══════════════════════════════════════════════════════════════════════════

GRADE_PART1_SYSTEM = """\
You are a rigorous information evaluator for a RAG system.

Your ONLY task: determine which candidate chunks contain information directly relevant
to answering the user's query.

Relevance criteria:
- A chunk is relevant if it provides substantive, specific information that helps answer the query
- A chunk is NOT relevant if it only mentions the same general topic without useful details
- Prefer precision over recall — only mark chunks that clearly contribute

Respond with ONLY a JSON object (no markdown fences, no extra text):
{
  "relevant_indices": [0, 2]
}"""

GRADE_PART1_USER = """\
【Original Query】: {original_query}

【Candidate Chunks to Evaluate】:
{chunks_text}"""

# ══════════════════════════════════════════════════════════════════════════
# Node 2b: Grade Part 2 — synthesize retained_info, gap analysis, sufficiency
# ══════════════════════════════════════════════════════════════════════════

GRADE_PART2_SYSTEM = """\
You are a rigorous information synthesizer for a RAG system.

Given the original user query and all confirmed relevant information gathered so far,
perform these steps IN ORDER:

1. **retained_info**: Write a structured, information-dense summary of everything confirmed
   so far. Include key facts, data points, relationships, and their sources. This summary
   will be used as context for future iterations — be comprehensive but concise. Do NOT
   copy chunk text verbatim; synthesize and organize the information.

2. **gap_analysis**: Analyze what aspects of the original query are NOT yet covered by
   the confirmed information. Be specific about what's missing.

3. **is_sufficient**: Based on the confirmed information, determine whether we have enough
   to fully and accurately answer the original query. Only mark true if ALL key aspects
   are covered.

Respond with ONLY a JSON object (no markdown fences, no extra text):
{
  "retained_info": "Structured summary of all confirmed information with sources...",
  "gap_analysis": "Specific analysis of what information is still missing...",
  "is_sufficient": false
}"""

GRADE_PART2_USER = """\
【Original Query】: {original_query}

【All Confirmed Relevant Information】:
{retained_chunks_text}"""

# ══════════════════════════════════════════════════════════════════════════
# Node 3: Check & Rewrite — generate a fresh query avoiding history
# ══════════════════════════════════════════════════════════════════════════

REWRITE_SYSTEM = """\
You are a search query optimizer. Given the original question, the broader task context,
what we already know, what is still missing, and previously tried queries that returned
nothing useful, write a new search query targeting the missing information.

- Use the task context to understand the broader goal when rewriting.
- Write a complete, grammatical sentence or question — not a list of keywords.
- Use different wording and perspective than all previous queries.
- If the gap mentions specific missing details, include them naturally in the query.

Respond with ONLY a JSON object:
{"new_query": "your new query here"}"""

REWRITE_USER = """\
Original question: {original_query}
Task context: {task_query}

Already known: {retained_info}

Still missing: {gap_analysis}

Previously tried (all failed): {history_queries}

New search query:"""

# ══════════════════════════════════════════════════════════════════════════
# Deprecated prompts removed — decompose now lives in src/rag/decomposer.py
# ══════════════════════════════════════════════════════════════════════════
