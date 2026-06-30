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
# Meeting v2 — Blueprint Inference (Node 0.3)
# ═══════════════════════════════════════════════════════════════════════

# MEETING_BLUEPRINT_SYSTEM + MEETING_BLUEPRINT_PROMPT
#   Purpose: Generates a full meeting summary (General tab) plus a
#            decomposition blueprint that maps sections to existing
#            RAG collections.  This is Node 0.3 of the v2 pipeline.
#   Role: MEETING_BLUEPRINT_SYSTEM → system (persona)
#         MEETING_BLUEPRINT_PROMPT  → user  (carries transcript, notes,
#                                          collection catalog)
#   Called by: src/meeting/service.py → MeetingService._do_blueprint_summary()
#   Template vars: {transcript}                    — full transcript text
#                  {speakers}                      — speaker list
#                  {notes}                         — user notes content
#                  {hot_words}                     — domain terms (correction aid)
#                  {collection_catalog}            — existing RAG collection list
MEETING_BLUEPRINT_SYSTEM = (
    "You are a professional meeting analyst and information architect. "
    "You extract structured insights and map meeting topics to existing "
    "knowledge-base collections with precision."
)

MEETING_BLUEPRINT_PROMPT = """\
Complete two tasks based on the meeting transcript, user notes, and the
user's existing collection catalog.

--- TRANSCRIPT ---
{transcript}

--- SPEAKERS ---
{speakers}

--- NOTES ---
{notes}

--- HOT WORDS (Correction Aid) ---
{hot_words}

These hot words are domain terms for correcting ASR errors only.
Replace garbled words that phonetically resemble a hot word with the
correct spelling.  Do NOT list hot words in the output.

--- EXISTING COLLECTION CATALOG ---
{collection_catalog}

Each collection has an id (stable identifier), a name (human-readable),
and possibly a description.  Use this catalog to ground your section
mapping — prefer matching meeting topics to existing collections.

---

TASK 1 — General Summary

**Language**: Write in the same language as the transcript. Do not translate.

Produce a comprehensive meeting-level Markdown document with these
sections:

## Summary
A concise 3-5 sentence overview of the entire meeting.
Use [spk:ID] for every meeting speaker reference (see Speaker rules
in Task 2).
Use [stt_XXX] to link key claims to source sentences when appropriate.

Additionally, provide a ``title`` — a single sentence (max 120 characters)
that captures the meeting's core topic, key decision, or outcome.

## Data & Facts
Key data points, figures, metrics, decisions, deadlines mentioned.
Present as bulleted blocks.  For each fact block, append a reference
tag listing the source sentence IDs: [stt_0001,stt_0003].

REF ACCURACY — CRITICAL:
- Before writing a [stt_XXX], verify that sentence stt_XXX ACTUALLY
  contains the data point, figure, or decision stated in the fact.
- Re-read the sentence text in the transcript above to confirm.
- If no single sentence directly supports a fact, do NOT add a ref tag.
  An unsupported fact without a ref is better than a wrong ref.
- Use commas between IDs or ranges: [stt_0001,stt_0003] or [stt_0001-005].
  For consecutive sentence ranges, use dash notation: [stt_0001-005]
  expands to stt_0001,stt_0002,stt_0003,stt_0004,stt_0005.
  Combine ranges and singles with commas: [stt_0001-005,stt_0010,stt_0015-018].
- Use [spk:ID] for all meeting speakers (listed in SPEAKERS).  Use the
  person's name as mentioned for non-speakers.

## Detail
A thorough, structured Markdown account of the entire meeting, organised
by topic flow.  Do NOT be brief — this is the main body of the summary.
Cover every topic discussed, including what each speaker said, the context,
reactions, and any follow-up actions.  Write in flowing prose with
paragraphs; do not reduce the content to a bullet-point list.
Use headings, bullet points, and bold for emphasis as appropriate.
Link claims to source sentences with [stt_XXX] refs whenever possible.
Use [spk:ID] for all meeting speakers.  Use the person's name as
mentioned for non-speakers.

---

TASK 2 — Decomposition Blueprint

Infer the logical sub-topics (sections) present in this meeting, guided
by the collection catalog above.

Rules:
- Map each sub-topic to the most relevant existing collection when a
  clear match exists.  Use the collection's id from the catalog.
- If a sub-topic has no matching collection, leave
  ``associated_collection_id`` as an empty string and use a descriptive
  ``tab_name`` that follows the user's naming conventions.
- Low-value chatter (greetings, tech checks, off-topic small-talk)
  belongs in an "Other" section.
- Provide a ~100-character ``section_description`` summarising what
  this section covers, to help downstream labelling.

---

Output EXACTLY this JSON object (no markdown fences, no extra text):

{{
  "title": "One-sentence meeting title capturing the core topic and outcome",
  "general_md_content": "## Summary\\n...\\n\\n## Data & Facts\\n...\\n\\n## Detail\\n...",
  "blueprint": [
    {{
      "tab_name": "Project Alpha - Budget Review",
      "associated_collection_id": "col_abc123",
      "associated_collection_name": "Project Alpha",
      "section_description": "Budget approval discussion for Project Alpha, including vendor negotiations and staffing plan for Q3. Approximately 100 chars."
    }},
    {{
      "tab_name": "Other",
      "associated_collection_id": "",
      "associated_collection_name": "",
      "section_description": "Greetings, equipment setup checks, and unrelated casual conversation with no business attribution."
    }}
  ]
}}

CRITICAL:
- The ``blueprint`` array MUST include every identified sub-topic.
  Always include an "Other" section as the last entry.
- The ``general_md_content`` string is a single flat Markdown document.
  Escape inner double-quotes and newlines so the JSON is valid.
- Prefer matching to existing collections.  Err toward fewer, broader
  sections rather than many narrow ones.
- When referencing a speaker, use the format [spk:ID] where ID is
	  the speaker identifier from the source sentences (e.g. [spk:0]).
	  NEVER write bare speaker names — always use the marker so the UI
	  can dynamically map names later.  Example: "[spk:0] proposed
	  increasing the budget to $450k."
- Speaker references: throughout the ENTIRE document (Summary, Todo,
  Data & Facts, Detail), use [spk:ID] for every meeting speaker (anyone
  in the SPEAKERS list).  NEVER write a speaker's name directly.
- For non-speakers (people mentioned but NOT in the SPEAKERS list),
  use their name as it appears in conversation.  Do NOT invent [spk:ID].
- Example: "[spk:0] proposed the budget. They suggested asking Finance
  Director Li to approve." — [spk:0] is a speaker; Finance Director Zhang is a non-speaker."""


# ═══════════════════════════════════════════════════════════════════════
# Meeting v2 — Context-Aware Sentence Tagging (Node 1.1)
# ═══════════════════════════════════════════════════════════════════════

# MEETING_TAGGING_SYSTEM + MEETING_TAGGING_PROMPT
#   Purpose: Classifies every sentence in the target chunk into the
#            blueprint sections defined during Node 0.3.  Uses a rolling
#            context window (2 prior chunks + current chunk) for
#            co-reference resolution while strictly tagging only the
#            target chunk's sentences.
#   Role: MEETING_TAGGING_SYSTEM → system (persona)
#         MEETING_TAGGING_PROMPT  → user  (carries blueprint, background
#                                         context, and target sentences)
#   Called by: src/meeting/service.py → MeetingService._tag_chunk()
#   Template vars: {blueprint_json}   — section definitions from Node 0.3
#                  {context_json}     — Chunk -2 & -1 sentences as background
#                  {target_json}      — Chunk 0 sentences to classify
MEETING_TAGGING_SYSTEM = (
    "You are a precise meeting section classifier. "
    "You assign each sentence in a target window to one or more "
    "predefined topic sections.  Be conservative — a sentence that is "
    "ambiguous or transitional should be placed in the single best-fit "
    "section rather than multiple sections."
)

MEETING_TAGGING_PROMPT = """\
Map every sentence in <target> to the most appropriate section(s) from
the blueprint below.  Sentences in <context> are provided ONLY for
co-reference resolution (e.g. to understand what "that project" refers
to).  Do NOT tag context sentences.

--- BLUEPRINT ---
{blueprint_json}

CRITICAL RULES:
1. Tag ONLY sentences inside <target>.  Context sentences are read-only.
2. A sentence may belong to multiple sections when it genuinely spans
   two topics (e.g. "the budget increase affects both Project A and
   Project C").
3. The "Other" section is a catch-all for greetings, tech checks, and
   small-talk with no business attribution.  Default to "Other" only
   when no better section fits.
4. Pay attention to speaker changes and explicit topic transitions
   (e.g. "Let's move on to...").

Output EXACTLY this JSON (no markdown, no extra text):

{{
  "mapping": {{
    "tab_sec_01": ["stt_0012", "stt_0013"],
    "tab_sec_02": ["stt_0014", "stt_0015"],
    "other": ["stt_0016"]
  }}
}}

The mapping keys are blueprint tab_ids.  Each value is a list of
sentence IDs from <target> assigned to that section.

---

<context>
{context_json}
</context>

<target>
{target_json}
</target>"""


# ═══════════════════════════════════════════════════════════════════════
# Meeting v2 — Section Summarization (Node 1.3)
# ═══════════════════════════════════════════════════════════════════════

# MEETING_SECTION_SUMMARY_SYSTEM + MEETING_SECTION_SUMMARY_PROMPT
#   Purpose: Generates a focused markdown summary for one section of a
#            meeting, using the section's tagged sentences and context.
#            Includes LLM-generated reference annotations for fact
#            traceability.
#   Role: MEETING_SECTION_SUMMARY_SYSTEM → system (persona)
#         MEETING_SECTION_SUMMARY_PROMPT  → user
#   Called by: src/meeting/service.py → MeetingService._summarize_section()
#   Template vars: {section_name}       — e.g. "Project Alpha - Budget Review"
#                  {section_description} — 100-char description from blueprint
#                  {sentences_json}      — tagged sentences with surrounding context
#                  {other_sections_summary} — brief descriptions of other sections
#                                            for cross-reference resolution
MEETING_SECTION_SUMMARY_SYSTEM = (
    "You are a professional meeting minute writer. "
    "You write concise, well-structured meeting notes for a single "
    "specific topic section, using only the provided source sentences."
)

MEETING_SECTION_SUMMARY_PROMPT = """\
Write a focused meeting summary for the following section.

Section: {section_name}
Context: {section_description}

Other sections discussed in this meeting (for cross-reference only):
{other_sections_summary}

---

Source sentences (each has an id, speaker, and text):

{sentences_json}

---

**Language**: Write in the same language as the source sentences. Do not translate.

Produce a Markdown document with these sections:

## Summary
2-4 sentence overview of the discussion and outcomes for this section.
Use [spk:ID] for every meeting speaker reference (same rules as Todo).
Use [stt_XXX] to link claims to source sentences when appropriate.

## Todo
Action items specific to this section.  Each as a bullet with assignee
and priority.

Assignee rules:
- If the assignee IS a meeting speaker (appears in any source sentence's
  "speaker" field), use the [spk:ID] marker (e.g. [spk:0]).
- If the assignee is a non-speaker (mentioned in conversation but does
  NOT appear as a "speaker" in the source sentences), use their name
  exactly as it appears in the conversation.

Attribution rule — CRITICAL:
Attribute each task to the person who is expected to DO it, NOT the
person who merely mentioned or suggested it.  Carefully determine this
from the conversation context.  For example: if [spk:0] says "Finance Director Zhang should update the dashboard", the task
belongs to Finance Director Zhang, not [spk:0].

Priority: append [priority: high], [priority: medium], or [priority: low]
at the end of each bullet when the conversation indicates urgency.

Examples:
- [spk:0] to prepare the Q3 budget report [priority: high]
- Finance Director Zhang to update the team dashboard [priority: medium]

## Data & Facts
Key data points, figures, metrics, decisions, and deadlines mentioned
in this section.  Present each as a standalone bullet block followed by
a source reference tag on the same line in the format [stt_0001].

REF ACCURACY — CRITICAL:
- Before writing a [stt_XXX], verify that sentence stt_XXX ACTUALLY
  contains the data point, figure, or decision stated in the fact.
- Re-read the sentence text to confirm.  Do NOT guess or match by topic
  alone.  The sentence must literally contain the stated number or claim.
- If no single sentence directly supports a fact, do NOT add a ref tag.
  An unsupported fact without a ref is FAR better than a wrong ref.
- Combine multiple IDs in one tag when a fact draws from several sentences.
- Use commas between IDs or ranges: [stt_0001,stt_0003] or [stt_0001-005].
  For consecutive sentences, use dash ranges: [stt_0001-005] expands to
  stt_0001,stt_0002,stt_0003,stt_0004,stt_0005.  Mix ranges and singles with
  commas: [stt_0001-005,stt_0010,stt_0015-018].
  NEVER concatenate IDs without commas or dash, e.g. [stt_0036038] —
  every stt_XXXX must be a 4-digit ID.
- ALWAYS include the "stt_" prefix.  NEVER write bare numbers like
  [0278] or [278-0281] — these are invalid and will be silently removed.
- Only reference sentence IDs that actually appear in the source sentences
  above AND whose text confirms the fact.

Use [spk:ID] for ALL speaker references within facts.  Example:

- [spk:0] reported Q3 revenue at $2.1M, a 15% increase YoY. [stt_0012,stt_0015]
- Vendor X was selected for the supply chain re-design, contract starts July 1. [stt_0023]

## Detail
A structured, thorough Markdown account of this section's discussion.
Use headings (###), bullet points, and bold.  Organise by sub-topic
flow within the section.  Use [spk:ID] for every meeting speaker
reference (same rules as Todo).  Use [stt_XXX] to link factual
claims to source sentences when appropriate.

CRITICAL:
- When referencing a MEETING SPEAKER (someone who appears in the source
  sentences' "speaker" field), use the [spk:ID] marker (e.g. [spk:0]).
  NEVER write the speaker's name directly — always use the marker so the
  UI can dynamically map names later.
- When referencing a NON-SPEAKER (a person mentioned in conversation
  who does NOT appear as a speaker in the source sentences), use their
  name exactly as mentioned.  Do NOT invent a [spk:ID] for them.
  NEVER write [spk:?] — it is an invalid marker.  Just write the person's
  name directly (e.g. "Allen" not "[spk:?] (likely Allen)").
- Example: "[spk:0] proposed increasing the budget.  They suggested
  asking Finance Director Zhang to approve it." — [spk:0] is a speaker;
  "Finance Director Zhang" is a non-speaker rendered as plain text.
- Restrict content to this section's topic.  Do not include information
  from the other sections listed above — they are provided only to help
  you resolve cross-references like "as discussed in the budget review".
- Sentence references [stt_XXX] may be used in ANY section (Summary,
  Todo, Data & Facts, Detail) to link claims to source sentences.
  Each [stt_XXX] MUST point to a sentence whose text literally
  contains the claim being stated.  Verify by re-reading the sentence.
  No ref is better than a wrong ref."""


# ═══════════════════════════════════════════════════════════════════════
# Meeting v2 — Magic Extract Rescan (Node 2.2)
# ═══════════════════════════════════════════════════════════════════════

# MEETING_EXTRACT_SYSTEM + MEETING_EXTRACT_PROMPT
#   Purpose: Re-scans all meeting chunks for sentences matching a
#            user-defined custom topic.  Existing section tags act as
#            guardrails — sentences already belonging to another section
#            are preferentially respected unless the sentence has strong
#            dual relevance to the new topic.
#   Role: MEETING_EXTRACT_SYSTEM → system (persona)
#         MEETING_EXTRACT_PROMPT  → user
#   Called by: src/meeting/service.py → MeetingService._rescan_chunk()
#   Template vars: {target_topic_name}        — user-defined topic name
#                  {target_topic_description}  — user-defined description
#                  {existing_sections_json}    — existing sections summary
#                  {context_json}              — background chunk sentences
#                  {target_json}               — current chunk sentences
MEETING_EXTRACT_SYSTEM = (
    "You are a precise meeting content extractor. "
    "You find sentences matching a specific topic while respecting "
    "pre-existing classifications."
)

MEETING_EXTRACT_PROMPT = """\
Find every sentence in <target> that relates to the following topic:

Topic: {target_topic_name}
Description: {target_topic_description}

Existing section assignments for context:
{existing_sections_json}

CRITICAL RULES:
1. Tag ONLY sentences inside <target>.
2. A sentence tagged with an existing section tag should be included
   ONLY when it genuinely has strong dual relevance to the target topic.
   Avoid "tag grabbing" — respect the prior classification by default.
3. If no sentences match, return an empty mapping object.
4. Treat the target topic as a brand-new section — use the key
   "extract_target" consistently in the mapping output.

Output EXACTLY this JSON (no markdown, no extra text):

{{
  "mapping": {{
    "extract_target": ["stt_0012", "stt_0025"]
  }}
}}

---

<context>
{context_json}
</context>

<target>
{target_json}
</target>"""
