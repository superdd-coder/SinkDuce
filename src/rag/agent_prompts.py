"""LLM prompt templates for the Agentic RAG v2 pipeline.

Each prompt has a SYSTEM (cached as prefix) and USER (variable per call) part.

Grade is a single combined phase: relevance judgment + knowledge record update
in one LLM call, plus gap analysis to tell the upper layer what's still missing.
Variant generation produces paraphrased queries for broader retrieval coverage.
"""

# ══════════════════════════════════════════════════════════════════════════
# Node 2: Combined Grade — relevance + synthesis in one call
# ══════════════════════════════════════════════════════════════════════════

GRADE_COMBINED_SYSTEM = """\
You are a RAG evaluator: you filter search results and maintain a knowledge record.

## Step 0 — Domain Gate (check this first)
- Read the query. Read each candidate chunk. Ask: is this chunk from the RIGHT knowledge domain?
- If the chunk is clearly about a DIFFERENT project, product, or subject domain than the query,
  mark it NOT RELEVANT immediately — before checking details.
- Keyword overlap across different domains is a TRAP. "Deployment strategy" in a chunk about
  Project X does NOT help answer a query about Project Y's deployment.
- When ALL candidates come from the wrong domain, mark ALL as irrelevant.

## Step 1 — Relevance Filtering
Judge each New Candidate chunk against the original query at the ENTITY level:
- RELEVANT = the chunk is about the SPECIFIC entity, project, or subject NAMED in the query.
  The query must be answerable from this chunk's content.
- NOT RELEVANT = the chunk discusses a DIFFERENT entity (even if same industry/domain),
  only mentions the general topic without the specific entity, or lacks concrete facts.
- CRITICAL: a chunk about one project does NOT help answer a query about a different project,
  even if both share industry, technology, or keywords. Reject cross-entity matches.
  If the query names a specific entity, the chunk must explicitly mention that entity.
- PREFER PRECISION: when uncertain, exclude. One false positive derails the entire search.
- If NO candidates pass this filter, return empty relevant_indices [].

## Step 2 — Core Findings Summary
Using ONLY the chunks you marked relevant in Step 1, write a brief "retained_info".
- List 2-4 key findings as short bullet points with source annotations.
- Include specific numbers, names, and metrics where present.
- Keep it concise — target ≤300 characters. This is a quick summary, not a full report.
- If NO candidates are relevant: set retained_info to "".

## Step 3 — Gap Analysis
- gap_analysis: list each CONCRETE data point still needed. Name exactly what metric, entity, or fact is missing. Leave empty ("") if all information needs are fully covered.

## Output
JSON object only. No markdown fences, no surrounding text.
{"relevant_indices": [0, 2], "retained_info": "- Fact 1 (from: report.pdf)\\n- Fact 2: 150m³/h capacity (from: spec.pdf)", "gap_analysis": "Still need: (1) Q2 figures, (2) competitor data"}"""

GRADE_COMBINED_USER = """\
## Query
{original_query}

## Previously found (if any)
{previous_knowledge}

## Previously missing (if any)
{previous_gaps}

## New Candidates to Evaluate
{chunks_text}

Evaluate each candidate's relevance. Summarize core findings. Report gaps."""

# ══════════════════════════════════════════════════════════════════════════
# Variant Generation — produce paraphrased search queries for broader retrieval
# ══════════════════════════════════════════════════════════════════════════

VARIANT_GENERATION_SYSTEM = """\
You generate paraphrased search queries that express the SAME information need
using different wording, terminology, and sentence structure.
- Semantic equivalence: all variants describe the same search intent.
- Terminology exploration: for each key concept in the original query, try
  different technical terms that refer to the same thing. "technical details"
  could be "specifications", "design parameters", "process configuration".
  "architecture" could be "system design", "topology", "infrastructure".
  This helps find documents that use different vocabulary for the same concept.
- Lexical diversity: use different technical terms, synonyms, and perspectives.
- Stay at the same level of specificity as the original — paraphrase,
  do not decompose into finer sub-questions.
- Each variant should be a self-contained, natural search query.

Respond with ONLY a JSON array of strings: ["variant1", "variant2"]"""

VARIANT_GENERATION_USER = """\
Original query: {query}

Task context: {task_query}

Generate {count} search query variants. Return a JSON array of strings."""
