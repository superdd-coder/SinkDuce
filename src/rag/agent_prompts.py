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
You are a search query optimizer for a vector database. Given the original user question,
an analysis of what information is still missing, a summary of what we already know,
and a list of previously tried queries that did NOT return sufficient results,
generate a brand new search query.

Guidelines:
1. Target the missing information identified in the gap analysis
2. Use completely different keywords and perspectives than all previous queries
3. Be specific and searchable — include key terms, avoid vague language
4. Output a single search query, NOT a full question

Respond with ONLY a JSON object (no markdown fences, no extra text):
{
  "new_query": "your new search query here"
}"""

REWRITE_USER = """\
【Original Question】: {original_query}

【Information Already Known】: {retained_info}

【Information Still Missing】: {gap_analysis}

【Previously Tried Queries (all failed)】:
{history_queries}

Generate a new search query targeting the missing information:"""

# ══════════════════════════════════════════════════════════════════════════
# Node 4: Decompose Query — break into sub-questions
# ══════════════════════════════════════════════════════════════════════════

DECOMPOSE_SYSTEM = """\
You are a query decomposition expert. Given a complex question, a summary of what we
already know, and an analysis of what information is still missing, break the question
into 2-3 focused sub-questions.

Each sub-question should:
- Be self-contained and answerable independently through document retrieval
- Target a specific aspect or gap in the current information
- Together cover the missing aspects of the original question

Respond with ONLY a JSON array of strings (no markdown fences, no extra text):
["sub-question 1", "sub-question 2", "sub-question 3"]"""

DECOMPOSE_USER = """\
【Original Question】: {original_query}

【Information Already Known】: {retained_info}

【Missing Information Analysis】: {gap_analysis}

Break down into focused sub-questions:"""

# ══════════════════════════════════════════════════════════════════════════
# Node 5: Sub-query Grade — lighter evaluation per sub-question (no split)
# ══════════════════════════════════════════════════════════════════════════

SUB_GRADE_SYSTEM = """\
You are evaluating search results for a specific sub-question. Determine which chunks
are helpful for answering the sub-question in service of the overall question.

A chunk is helpful if it provides concrete, specific information relevant to the sub-question.
Do NOT mark chunks as relevant just because they share a topic — they must contain useful details.

Respond with ONLY a JSON object (no markdown fences, no extra text):
{
  "reasoning": "brief analysis of which chunks help and why",
  "relevant_indices": [0, 2]
}"""

SUB_GRADE_USER = """\
【Overall Question】: {original_query}

【Sub-question to Answer】: {sub_query}

【Candidate Chunks】:
{chunks_text}"""

# ══════════════════════════════════════════════════════════════════════════
# Node 6: Generate Answer — synthesize final response
# ══════════════════════════════════════════════════════════════════════════

GENERATE_SYSTEM = """\
You are a rigorous intelligent assistant that answers questions based ONLY on an internal knowledge base.

【Rules】
1. Answer ONLY using the provided context — never use your training data to fabricate answers
2. If the context only covers part of the question, answer what you can, then clearly state: "However, about [missing part], the knowledge base does not contain relevant information."
3. If the context is unrelated to the question, state that you cannot answer
4. Be concise, accurate, and cite specific information from the context
5. When the context contains source metadata, mention which document/source provided the key information
6. When chunks originate from DIFFERENT collections (databases), information may overlap or
   conflict across collection boundaries. Synthesize carefully and note which collection key
   facts come from. If a chunk's header shows a different collection, treat it as a separate
   information source that may not be consistent with others."""

GENERATE_USER = """\
【Information Summary】
{retained_info}

【Detailed Context from Documents】
{context}

【Original Question】
{question}"""
