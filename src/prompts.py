"""Centralized LLM prompt registry.

All prompts used across the project live here for unified management.
Import from this module instead of defining prompts inline or in scattered constants.

Template variables (e.g. {source_content}, {transcript}) are filled at call sites
via .format() or f-strings.
"""

# ═══════════════════════════════════════════════════════════════════════
# Visual / Image Description
# ═══════════════════════════════════════════════════════════════════════

# VISUAL_PROMPT
#   Purpose: Generates a natural-language description of an image via a Vision LLM.
#            Used by the "Visual Translate" feature in the Tiptap notes editor.
#            When the user clicks the AI button on an image, the Vision LLM receives
#            this prompt along with the base64-encoded image.
#   Role: user (single message, with image base64 attached)
#   Called by: src/api/routes/visual.py → llm.describe_image(prompt=VISUAL_PROMPT)
#   Fallback: src/providers/llm/openai_compat.py has an identical _DEFAULT_VISUAL_PROMPT
#   Template vars: none
VISUAL_PROMPT = (
    "Analyze this image and describe it concisely in 2-5 sentences of plain text "
    "— no markdown, no bullet points, no headings. "
    "Cover what is shown (photo, chart, diagram, etc.), key elements and their "
    "relationships, any visible text transcribed exactly, and notable data like "
    "numbers, labels, or axes. Be objective and factual, no speculation. "
    "Match the language of visible text, or use English if none. "
    "Omit purely decorative or background elements."
)


# ═══════════════════════════════════════════════════════════════════════
# Notes Distillation
# ═══════════════════════════════════════════════════════════════════════

# DISTILL_SYSTEM_PROMPT + DISTILL_USER_PROMPT
#   Purpose: Compresses a note's content into high-density structured notes.
#            In the note editor, users distill Note A and inject the result
#            into Note B via the "Distill" feature. Results are cached by
#            source_note_id — re-distilling only happens when source content changes.
#   Role: DISTILL_SYSTEM_PROMPT → system (behavior rules)
#         DISTILL_USER_PROMPT   → user  (carries the source note body)
#   Called by: src/notes/service.py → get_distillation_prompt() → llm.generate()
#   Template vars: {source_content} — full Markdown of the source note
DISTILL_SYSTEM_PROMPT = """You are a precise information extractor. Distill the source content into concise, information-dense notes.

Rules:
- Skip noise: timestamps, UI labels, navigation text, metadata headers, empty bullet points, and purely structural markup
- Capture ALL significant facts, data, and conclusions — prioritize completeness over brevity
- Preserve specific numbers, dates, names, technical terms, and parameters exactly as written
- Use a mix of paragraphs and `-` bullet points — whichever fits the information best
- `**bold**` for key terms, proper nouns, and critical numbers only — no other formatting
- Preserve original section structure (## headings) if the source has clear sections
- For code blocks: summarize purpose in one line, keep short snippets in backticks
- For tables: preserve as markdown tables if the data is important
- If the source is empty or has no extractable content, output exactly: *No extractable content*
- No preamble, no commentary, no meta-remarks"""

DISTILL_USER_PROMPT = """Distill the following content. Capture all important information — be thorough and information-dense. Preserve every specific data point, number, name, and technical detail.

---
{source_content}
---"""


# ═══════════════════════════════════════════════════════════════════════
# Collection Consolidation
# ═══════════════════════════════════════════════════════════════════════

# CONSOLIDATION_PROMPT
#   Purpose: Merges per-document summaries into a project-level overview,
#            and detects factual contradictions across documents.
#            Triggered by the "Consolidate" button on the INFO page.
#            Produces a Project Summary + Conflicts list.
#   Role: user (single message)
#   Called by: src/tasks/handlers.py → enriching_llm.generate(CONSOLIDATION_PROMPT.format(...))
#   Template vars: {summaries} — concatenated text of all per-document summaries
CONSOLIDATION_PROMPT = """You are analyzing multiple document summaries from a single project. Synthesize them into:

1. A CONCISE PROJECT SUMMARY (300 words max): Write a high-level overview of the project, NOT a per-document re-summary. Synthesize across all documents to answer:
   - What is this project? (type, scope, scale)
   - Who is involved? (client, vendor, key parties)
   - Key technical parameters (capacity, process, specs)
   - Key commercial terms (contract value, rate, duration)
   - Timeline and status
   Write in concise paragraphs without ## sub-headings. Use **bold** for key numbers and names.

2. CONFLICTS: Identify ONLY genuine contradictions where two documents make different claims about the SAME fact.

Document summaries:
{summaries}

===OUTPUT FORMAT===

Output a single JSON object with this EXACT schema (no markdown, no extra text):

{{
  "summary": "(Concise project overview, max 300 words, plain paragraphs with **bold** highlights)",
  "conflicts": [
    {{"content1": "claim from doc 1", "source1": "filename1", "content2": "claim from doc 2", "source2": "filename2"}}
  ]
}}

If no conflicts, use an empty array: "conflicts": []"""


# ═══════════════════════════════════════════════════════════════════════
# Contextual Enrichment (document indexing pipeline)
# ═══════════════════════════════════════════════════════════════════════

# SUMMARY_PROMPT
#   Purpose: Step 1 of contextual enrichment — generates BOTH a structured summary
#            (data/facts/insights) AND a short 1-2 sentence summary in one LLM call.
#            The structured part uses the same format as STRUCTURED_SUMMARY_PROMPT.
#   Template vars: {document} — full document text
SUMMARY_PROMPT = """Analyze the following document and produce two outputs:

Document:
{document}

---

## Output 1 — Structured Summary
Analyze the following document and extract key information. Be extremely conservative — only extract facts that are EXPLICITLY stated in the document. Do NOT infer, assume, or generalize.

Output in this exact format:

===DATA===
(Numerical data that is EXPLICITLY stated in the document with clear context)
- Example: The contract value for Project Alpha is 5 million USD
- Example: The system design capacity is 3,000 m3/day

===FACTS===
(Factual statements that are EXPLICITLY stated — not inferred)
- Example: Company X is the contractor for Project Alpha
- Example: The project uses Dow BW30-400 RO membranes

===INSIGHTS===
(Only include if there is STRONG direct evidence in the document. If uncertain, write "- None identified")
- Example: Based on the 3-month delay mentioned by the project manager, the Q3 deadline appears at risk

Rules:
- MAX 10 items per category. Quality over quantity.
- ONLY extract what is explicitly written. Do NOT generalize from examples or discussions.
- If a number or fact is mentioned in a hypothetical, example, or "what-if" scenario, do NOT treat it as a real data point.
- If you are not sure whether something is a fact or an assumption, do NOT include it.
- Each item MUST clearly state what it refers to. Do not use vague references like "the project" — name the specific project/entity.
- If a category has nothing that meets these criteria, write "- None identified"
- Do NOT use square brackets [] around words. Write plain sentences.
- Pay attention to context: if someone says "let's model a 1000 m3/day project", that is a discussion about modeling, NOT a statement about an actual project's capacity.

## Output 2 — Short Summary
Write a brief 1-2 sentence summary of this document. Focus on: what is this document about, who is it for, and what is its purpose. Keep it concise and readable.

## Output Format
Respond with ONLY a JSON object (no markdown fences, no extra text):
{{"structured_summary": "===DATA===\\n- ...\\n===FACTS===\\n- ...\\n===INSIGHTS===\\n- ...", "short_summary": "1-2 sentence summary"}}"""

# CONTEXT_PROMPT
#   Purpose: For each chunk, generates background context that a reader cannot
#            infer from the chunk text alone, using surrounding chunks.
#            The document summary is generated in parallel and stored separately.
#   Role: user (single message)
#   Called by: src/rag/contextual.py → ContextualRetrieval._generate_context()
#   Template vars: {chunk}              — current chunk text
#                  {surrounding_section} — neighboring chunk text (may be empty)
CONTEXT_PROMPT = """You are helping build a search index. Given a chunk from a document and its surrounding chunks, write 1-2 sentences of background context that a reader would need to understand this chunk but CANNOT figure out from the chunk text alone.

{surrounding_section}Chunk text: {chunk}

Rules:
- Only include information NOT present in the chunk itself
- Write in natural, readable sentences (not key=value format)
- Focus on: what section of the document this is from, what was discussed before this chunk, who/what entities are referenced
- Use surrounding chunks to understand what comes before/after this chunk
- If the chunk is self-contained and understandable on its own, output nothing
- Keep it brief — max 2 short sentences

Output only the context text, nothing else."""

# STRUCTURED_SUMMARY_PROMPT
#   Purpose: Extracts structured information (data / facts / insights) from a
#            single document. Triggered by the "Generate Summary" button next to
#            a document on the INFO page. Output is categorized into DATA, FACTS,
#            and INSIGHTS sections for building per-document Collection summaries.
#   Role: user (single message)
#   Called by: src/rag/contextual.py → summary/doc-summary generation pipeline
#   Template vars: {document} — full document text
STRUCTURED_SUMMARY_PROMPT = """Analyze the following document and extract key information. Be extremely conservative — only extract facts that are EXPLICITLY stated in the document. Do NOT infer, assume, or generalize.

Document:
{document}

Output in this exact format:

===DATA===
(Numerical data that is EXPLICITLY stated in the document with clear context)
- Example: The contract value for Project Alpha is 5 million USD
- Example: The system design capacity is 3,000 m3/day

===FACTS===
(Factual statements that are EXPLICITLY stated — not inferred)
- Example: Company X is the contractor for Project Alpha
- Example: The project uses Dow BW30-400 RO membranes

===INSIGHTS===
(Only include if there is STRONG direct evidence in the document. If uncertain, write "- None identified")
- Example: Based on the 3-month delay mentioned by the project manager, the Q3 deadline appears at risk

Rules:
- MAX 10 items per category. Quality over quantity.
- ONLY extract what is explicitly written. Do NOT generalize from examples or discussions.
- If a number or fact is mentioned in a hypothetical, example, or "what-if" scenario, do NOT treat it as a real data point.
- If you are not sure whether something is a fact or an assumption, do NOT include it.
- Each item MUST clearly state what it refers to. Do not use vague references like "the project" — name the specific project/entity.
- If a category has nothing that meets these criteria, write "- None identified"
- Do NOT use square brackets [] around words. Write plain sentences.
- Pay attention to context: if someone says "let's model a 1000 m3/day project", that is a discussion about modeling, NOT a statement about an actual project's capacity."""


# ═══════════════════════════════════════════════════════════════════════
# Meeting Summary
# ═══════════════════════════════════════════════════════════════════════

# MEETING_SUMMARY_SYSTEM + MEETING_SUMMARY_PROMPT
#   Purpose: Generates a structured meeting summary (title, per-project sections,
#            TODO items) from the meeting transcript + user notes.
#            Triggered by the "Summarize" button on the Recording page.
#   Role: MEETING_SUMMARY_SYSTEM → system (persona)
#         MEETING_SUMMARY_PROMPT  → user  (carries transcript, notes, hot words)
#   Called by: src/meeting/service.py → MeetingService.summarize() → llm.generate()
#   Template vars: {transcript}                     — full meeting transcript text
#                  {speakers}                       — speaker list
#                  {notes}                          — user notes content
#                  {hot_words}                      — domain terms (for ASR correction, not output)
#                  {database_grouping_instruction}  — Collection grouping hint (dynamic, may be empty)
MEETING_SUMMARY_SYSTEM = "You are a professional meeting assistant. You extract structured information from meeting transcripts and notes."

MEETING_SUMMARY_PROMPT = """\
Based on the following meeting transcript and notes, generate a structured summary.

---TRANSCRIPT---
{transcript}

---SPEAKERS---
{speakers}

---NOTES---
{notes}

---HOT WORDS (Domain Terminology)---
{hot_words}

These hot words are domain-specific terms (names, acronyms, jargon) for YOUR REFERENCE ONLY. Use them ONLY to correct potential ASR errors — if you see garbled or out-of-place words that phonetically resemble a hot word, replace them with the correct spelling. Do NOT list, mention, or regurgitate hot words anywhere in the output. They are a correction aid, not content.

Requirements:
- **Title**: A short, descriptive meeting title (max 50 characters). Format: "[Main Topic] - [Key Context]". Do NOT include a date prefix.
- **Sections**: Split the meeting into logical sections by project/topic. Each section has:
  • heading: The project/topic name (e.g. "Project WD", "Other Topics")
  • detail: Comprehensive account of everything discussed under this topic. Include key points, data, arguments, decisions, context from notes. Use Markdown with bullet points, bold, and real speaker names.
  • summary: Concise 2-4 sentence summary of this topic's discussion and outcomes. Use Markdown.
  • todos: JSON array of action items for this topic. Each item has "text" (required), optional "assignee" (real speaker name), optional "priority" (high/medium/low). CRITICAL: Every section MUST have a todos array, even if empty ([]).

{database_grouping_instruction}

IMPORTANT: Always incorporate information from the NOTES section. Notes may contain important context, decisions, or details not present in the transcript.

Output a single JSON object with this EXACT schema (no markdown fences, no extra text):

{{
  "title": "Meeting Title",
  "sections": [
    {{
      "heading": "Project/Topic Name",
      "detail": "Markdown detail text...",
      "summary": "Markdown summary text...",
      "todos": [{{"text": "action item", "assignee": "Name", "priority": "high"}}]
    }}
  ]
}}

RULES for sections:
- Each section represents a distinct project or major topic group
- Sub-points and numbered items stay as bullets within their section's detail
- If the meeting covers a single topic, use one section
- Always include at least one section
- NEVER mention hot words, project matching results, or reference-only metadata in the output. Hot words are a correction aid; project names are section grouping hints. Neither should appear as content."""
