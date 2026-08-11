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
- Preserve every [spk:ID] token exactly as written (e.g. [spk:0], [spk:2]) — do not replace them with person names
- If the source is empty or has no extractable content, output exactly: *No extractable content*
- No preamble, no commentary, no meta-remarks"""

DISTILL_USER_PROMPT = """Distill the following content. Capture all important information — be thorough and information-dense. Preserve every specific data point, number, name, and technical detail. Keep any [spk:ID] markers unchanged.

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

2. CONFLICTS: A conflict is a pair of claims that are mutually exclusive — both cannot be true at the same time.

   Two claims are the SAME fact (not a conflict) when one is a more general, more specific, summarized, or broken-down version of the other, or when they describe different aspects of the same entity without disagreeing.

   Two claims are CONFLICTING when one asserts something that directly contradicts the other — different numbers for the same measurable quantity, or logically incompatible statements about the same subject.

   Compare these examples to calibrate. The hard cases are where the numbers look compatible at first glance:

   Example A (IS a conflict — easy to miss because numbers look similar):
     - "Total project cost is $750M broken into solar EPC ($620M), BESS EPC ($90M), interconnection ($22M), and development fees ($18M)."
     - "The EPC budget for the project is $750M."
     These both say $750M, but the first breaks $750M into multiple components (only ~$710M of which is EPC); the second claims EPC alone is $750M. The numbers cannot both be true.

   Example B (NOT a conflict — different facets of the same project):
     - "Project completion is scheduled for Q3 2026."
     - "Construction begins in Q1 2025."
     Both can be true; one is about construction start, the other about final completion. No contradiction.

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
# Meeting v4 — Two-Pass Blueprint (General Summary → Decomposition)
# ═══════════════════════════════════════════════════════════════════════
# Split from a single combined call into two separate calls:
#
#   Call 1 — General Summary (MEETING_GENERAL_SUMMARY_PROMPT)
#     Input:  transcript + notes + hot_words   (NO collection catalog)
#     Output: title + general_md_content
#     Why:    Catalog must not influence the Summary — the Summary
#             describes what was discussed, not what collections exist.
#
#   Call 2 — Blueprint Decomposition (MEETING_BLUEPRINT_PROMPT)
#     Input:  transcript + notes + hot_words + collection_catalog
#     Output: taxonomy + blueprint (topics + section_descriptions)
#     Why:    Catalog is needed for STEP 1 (infer dimension) and
#             STEP 2b (match topics to collections), but the LLM
#             focuses entirely on classification — no Summary task
#             competing for attention.
#
# Both calls share the same system prompt (MEETING_BLUEPRINT_SYSTEM)
# and the same transcript prefix for prefix-cache reuse.

# ═══════════════════════════════════════════════════════════════════════
# Call 1 — General Summary (no collection catalog)
# ═══════════════════════════════════════════════════════════════════════

# MEETING_GENERAL_SUMMARY_PROMPT
#   Purpose: Generates a comprehensive meeting summary (General tab)
#            from the transcript alone — no collection catalog, no
#            taxonomy inference.  Isolated so that collection descriptions
#            cannot bias the summary wording.
#   Role: user
#   Called by: src/meeting/service.py → MeetingService._do_blueprint_summary()
#   Template vars: {transcript}    — full transcript [N] [spk:ID] {text}
#                  {notes}         — user meeting notes
#                  {hot_words}     — domain terms (correction aid)
MEETING_GENERAL_SUMMARY_PROMPT = """\
<transcript>
{transcript}
</transcript>

<hot-words>
{hot_words}
</hot-words>

<user-meeting-note>
{notes}
</user-meeting-note>

<task>
Produce a comprehensive meeting-level Markdown document.

Language: Output MUST be in the same language as the transcript.
If the transcript is English, write in English.  NEVER switch
languages — this is a hard failure.

## Summary
A concise 3-5 sentence overview of the entire meeting.
Use [spk:ID] and [N] to cite speakers and source sentences.
Follow SPEAKER IDENTITY rules below (never write [spk:ID] next to
the same person's bare name).

## Data & Facts
Key data points, figures, metrics, decisions, deadlines mentioned.
Each as a standalone bullet with [N] reference.

REF ACCURACY — CRITICAL:
- Before writing a [N] ref, verify that the sentence text ACTUALLY
  contains the data point or claim.
- If no single sentence directly supports a fact, do NOT add a ref tag.
- Combine IDs: [67,70] or ranges [67-70].

SPEAKER IDENTITY — CRITICAL (applies to Summary, Todo, Data & Facts,
and Detail):

The transcript labels speakers only as [spk:ID] (e.g. [spk:0], [spk:1]).
Display names are applied later by the product UI — you must NOT invent
or paste display names next to a speaker tag.

Two distinct cases:

1) Meeting speaker / assignee among transcript speakers
   → Use ONLY [spk:ID]. Never also write that person's name.
   Prefer ID whenever the person can be tied to a [spk:…] in the
   transcript (who spoke, who owns a task, who decided).

2) Person mentioned in dialogue who is NOT used as a speaker slot
   → Write their name as spoken in the transcript (plain text only).
   Use a bare name ONLY when it is clearly a third party or someone
   referred to by name and you cannot map them to a [spk:ID].
   If they can be mapped to a speaker ID, use [spk:ID] instead (prefer ID).

HARD RULES:
- NEVER combine both forms for the same person in one clause
  (illegal: "[spk:0] Alex …", "Alex [spk:0] …", "[spk:0] Alex to …").
- NEVER write a speaker's name in place of [spk:ID] for attribution.
- Prefer [spk:ID] over a bare name whenever either would work.
- Bare names are only for people clearly named in dialogue who are
  not identified as a transcript speaker for that attribution.

GOOD:
- [spk:0] to prepare the Q3 budget report [priority: high]
- [spk:1] recommended Option B; the team agreed. [42]
- External vendor Northline to send revised quotes by Friday [88]
  (third party named in dialogue, not a [spk:] in this meeting)

BAD (double labeling — forbidden):
- [spk:0] Alex to prepare the Q3 budget report
- Alex [spk:0] to prepare the Q3 budget report
- [spk:1] Jordan recommended Option B

BAD (used bare name when a speaker ID applies):
- Alex to prepare the Q3 budget report
  (when Alex is [spk:0] in the transcript)

## Todo
Every action item, commitment, or deadline found in the ENTIRE meeting.
One per bullet.

Format when the doer is a meeting speaker:
  "- [spk:ID] task description [priority: high|medium|low]"
Format when the doer is only a named third party (not a [spk:ID]):
  "- <Name as in transcript> task description [priority: …]"

Attribution rule — CRITICAL:
Attribute each task to the person expected to DO it, NOT the person
who merely mentioned it.
Example: if [spk:0] says "Pat should update the dashboard", and Pat
is not a transcript speaker, the task is Pat's (bare name). If Pat
is [spk:2], write [spk:2], not "Pat", and never "[spk:2] Pat …".

Priority: append [priority: high], [priority: medium], or
[priority: low] at the end of each bullet when urgency is indicated.

Examples:
- [spk:0] to prepare the Q3 budget report [priority: high]
- [spk:1] to circulate the meeting notes [priority: medium]
- Vendor Northline to send revised quotes by Friday [priority: medium]

## Detail
A condensed narrative of the entire meeting, preserving ALL
substantive content.  Write as a human note-taker would — synthesize
discussion threads, do NOT reproduce the transcript turn by turn.

CONTENT FILTER — what to INCLUDE vs EXCLUDE:

INCLUDE (substance):
  - Technical data: numbers, specs, parameters, test results
  - Decisions, conclusions, and agreements reached
  - Disagreements, open questions, and concerns raised
  - Action items and commitments (who does what by when)
  - Key context that explains why a decision was made

EXCLUDE (procedure):
  - Introductions, greetings, attendance, role descriptions
  - Meeting logistics: scheduling, screen sharing, agenda order
  - Meta-discussion: "let's move to the next topic", "I have a hard stop"
  - Polite filler: thanking, praising, acknowledging without substance
  - Repeated confirmations ("got it", "understood", "noted") with no new info
  - Tangents that were explicitly dropped or deferred

Err on the side of KEEPING content — when a statement contains any
data point, opinion, or implication, keep it even if minor.  But
pure procedural housekeeping (introductions, logistics, agenda
navigation) should be removed or compressed to a single line.

PARAGRAPH STRUCTURE — topic-based, NOT turn-based:

Group related discussion into topic paragraphs, even if that means
merging multiple speaker turns into one paragraph.  A paragraph
should cover ONE topic thread from start to resolution (or deferral).

WRITING STYLE — final answers only, no discussion journey:

Your job is to state WHAT was decided, concluded, or found — NOT to
recount WHO said what or HOW the discussion unfolded.  Strip out the
Q&A process entirely.  If [spk:A] asked a question and [spk:B]
answered, write only the answer.

BAD (narrates the discussion journey):
  [spk:A] asked about Topic X, noting Fact 1. [N]
  [spk:B] explained that the reason is Condition C. [N]
  [spk:A] confirmed that this means Outcome O. [N]

GOOD (states the final answer directly):
  Topic X operates under Condition C, resulting in Outcome O [N-N].

BAD (attributes every fact to a speaker):
  [spk:B] stated the capacity is N units. [spk:C] noted the cost is
  $M. [spk:B] added that the timeline is D months.

GOOD (states facts directly, speaker only for opinions/decisions):
  Capacity is N units at a cost of $M with a D-month timeline [N-N].
  [spk:B] recommended proceeding with Option A.

BAD (every sentence gets a ref — noisy):
  The system uses N units, each V m³ [ref].  The loading rate is R
  kg/m³ with a D-day retention time [ref].

GOOD (refs only on key data, combined):
  The system uses N units of V m³ each at R kg/m³ loading with a
  D-day retention time [ref-ref].

INFERENCE RULES — what you MAY vs MAY NOT infer:

MAY (simple, single-step, directly from stated numbers):
  - "tripling X" → "saves roughly two-thirds"
  - "A is 30% higher than B" → "B is roughly 23% lower than A"
  - "raised from $5M to $8M" → "a $3M / 60% increase"

MAY NOT (multi-step, domain-specific, or requires outside knowledge):
  - Financial projections (NPV, IRR, payback period)
  - Comparing options that were not directly compared in the meeting
  - Drawing conclusions that require technical domain expertise
    beyond what is stated in the transcript
  - ANY inference where you cannot point to the exact source
    sentences that contain the input numbers

When in doubt, state the raw numbers and let the reader draw
their own conclusions.

SENTENCE REFERENCES:
- Use [N] refs for key data points, numbers, decisions, and direct
  quotes.  Do NOT add refs to every sentence — narrative context
  and transitional prose do not need refs.
- Place [N] at the end of the clause it supports.
- Combine IDs: [67,70] or ranges [67-70].
- NEVER invent or concatenate IDs.

Output the Markdown document directly — no JSON wrapper, no markdown
fences, no preamble.  Start immediately with ``## Summary``.
</task>"""


# ═══════════════════════════════════════════════════════════════════════
# Call 2 — Blueprint Decomposition (with collection catalog)
# ═══════════════════════════════════════════════════════════════════════

# MEETING_BLUEPRINT_PROMPT
#   Purpose: Infers the user's categorization taxonomy from existing
#            collections, then decomposes the transcript into sections
#            matching that taxonomy.  Receives the collection catalog
#            for dimension inference and topic-to-collection matching.
#
#            v2 (2026-07): Catalog moved inside STEP 1 to reduce
#            anchoring.  Dimension is free-form (not a preset enum)
#            so it generalizes to any organizing principle.  STEP 2
#            includes a self-verification check to prevent ghost
#            sections from catalog-only entities.
#   Role: user
#   Called by: src/meeting/service.py → MeetingService._do_blueprint_summary()
#              and generate_blueprint_stream()
#   Template vars: {transcript}         — full transcript [N] [spk:ID] {text}
#                  {notes}              — user meeting notes
#                  {hot_words}          — domain terms (correction aid)
#                  {collection_catalog} — existing RAG collection list
MEETING_BLUEPRINT_PROMPT = """\
<transcript>
{transcript}
</transcript>

<hot-words>
{hot_words}
</hot-words>

<user-meeting-note>
{notes}
</user-meeting-note>

<task>

STEP 1 — Observe the user's organizing principle

<collection-catalog>
{collection_catalog}
</collection-catalog>

Look at the collection names and descriptions above.  What organizing
principle do they follow?  Observe the actual pattern — do NOT force
it into a preset category.  The organizing principle could be by
project, by function, by department, by region, by quarter, by vendor,
by technology, or anything else.  Describe what you actually see.

Name the principle with a short ``dimension`` label and write a one-
sentence ``explanation``.  For example:

  If each collection = one project:    dimension: "project"
  If each collection = one function:   dimension: "function"
  If each collection = one department: dimension: "department"
  If each collection = one region:     dimension: "region"
  If the pattern is something else:    choose a short descriptive label

If the catalog is empty ("No existing collections"):
  dimension: "other"
  explanation: "No existing collections — meeting content will be
  organized by the topics discussed in the transcript."

IMPORTANT — FALLBACK: If the meeting's content does not naturally fit
the observed organizing principle (e.g. collections are organized by
project but this meeting discusses cross-cutting policies that don't
belong to any single project), apply the principle that BEST organizes
THIS meeting's actual topics — even if it differs from the catalog's
pattern.  Note this in the taxonomy.

────────────────────────────────────────────────────────────────

STEP 2 — Extract the entities discussed in this meeting

Now work exclusively from <transcript>.  Using the organizing
principle from STEP 1, scan <transcript> for every distinct entity
that fits that principle and was discussed.

  For example, if the principle is "by project":
    Scan for every project / client / case name that was discussed.
    Transcript discusses Project A audit + Project B legal
    + Project C HR → entities: Project A, Project B, Project C.

  For example, if the principle is "by function":
    Scan for every business function / work type that was discussed.
    Transcript discusses legal issues + HR issues → entities: Legal, HR.

  For example, if the principle is "by department":
    Scan for every department / team that was discussed.
    Transcript discusses R&D headcount + Sales Q3 targets
    + Operations workflow → entities: R&D, Sales, Operations.

  Apply the SAME logic to whatever principle you observed in STEP 1.

Skip: greetings, tech checks, pure social small talk with zero
business relevance.

Before finalizing your entity list, verify each one against
<transcript>:
  - Can you recall a moment in the transcript where the discussion
    explicitly shifts to or focuses on this entity?
  - Is there more than a passing mention — a speaker naming it,
    asking about it, or reporting on it?
  If you cannot name a specific moment for an entity, it was not
  meaningfully discussed.  Remove it.

Do NOT create a section just because you saw a matching collection
name in STEP 1.  The collection catalog tells you which collections
exist — it does NOT describe what was discussed in this meeting.

STEP 3 — Match entities to collections

For each entity from STEP 2, check the <collection-catalog> in STEP 1:
  - If a collection represents the SAME entity → use its id and name.
  - If no match → leave ``associated_collection_id`` and
    ``associated_collection_name`` as empty strings "".

CRITICAL:
- Entities come ONLY from STEP 2.  Do NOT add new entities based on
  collection names you saw in STEP 1.
- If a collection exists in the catalog but its entity was NOT found
  in STEP 2, it gets NO blueprint entry.  That collection's content
  comes from other meetings, not this one.
- Do NOT merge two distinct entities into one section.

STEP 4 — Write section descriptions

For each entity, write a ``section_description`` (max 400 chars)
that describes ONLY what <transcript> says about this entity.

Derive the description EXCLUSIVELY from <transcript>.  The catalog
descriptions in STEP 1 may reflect content from OTHER meetings —
they do NOT describe what was discussed in THIS meeting.

The downstream sentence classifier uses this description to identify
conversation segments.  It must reflect THIS meeting's actual
discussion, not general knowledge or catalog content.

CRITICAL — CROSS-CUTTING CONCEPTS: When a general method, model, or
approach was discussed, describe ONLY how it applies to this
specific entity.  Do NOT list the general concept as a standalone
signal — the classifier will tag every sentence about that concept
regardless of which entity it relates to.

STEP 5 — Output JSON

Output EXACTLY this JSON (no markdown fences, no extra text):

{{
  "title": "Short title, max 8 words, capturing the core topic or outcome",
  "taxonomy": {{
    "dimension": "project",
    "explanation": "The user organizes collections by individual project. Each collection name is a distinct project identifier."
  }},
  "blueprint": [
    {{
      "tab_name": "Project Alpha",
      "section_description": "Audit progress review including Q2 financial model updates and budget approval discussion...",
      "associated_collection_id": "col_1",
      "associated_collection_name": "Project Alpha"
    }},
    {{
      "tab_name": "Project Gamma",
      "section_description": "Initial discussion of litigation strategy and staffing plan for the upcoming case...",
      "associated_collection_id": "",
      "associated_collection_name": ""
    }}
  ]
}}

MANDATORY:
- Every distinct entity from STEP 2 that passes verification
  MUST appear as a separate entry in ``blueprint``.
- ``tab_name`` follows the same naming convention as existing
  collections.
- ``taxonomy.dimension`` is a short label describing the organizing
  principle — name what you observed, not from a preset list.
- When unmatched, ``associated_collection_id`` and
  ``associated_collection_name`` MUST be "".
</task>"""


# ═══════════════════════════════════════════════════════════════════════
# Meeting v3 — Shared System Prompt (maximizes prefix-cache hits)
# ═══════════════════════════════════════════════════════════════════════
# All three stages (Blueprint, Tagger, Summarizer) use the SAME system
# prompt so that LLM provider prefix caches are shared across phases:
#
# All three phases (Blueprint, Tagger, Summarizer) share the same system
# prompt for prefix-cache reuse.  Role-specific instructions live in
# each prompt's <task> block.
#

_MEETING_V3_SHARED_SYSTEM = (
    "You work exclusively within <task> blocks.  Read the instructions "
    "inside <task> carefully and follow them exactly.  Output ONLY what "
    "is requested — no preamble, no commentary, no markdown fences unless "
    "the <task> explicitly asks for them."
    "\n\n"
    "CRITICAL — LANGUAGE: Always output in the SAME language as the "
    "transcript.  NEVER switch to a different language — if the "
    "transcript is in English, output MUST be in English; if in "
    "Chinese, output MUST be in Chinese.  Outputting in a language "
    "different from the transcript is a hard failure."
    "\n\n"
    "TRANSCRIPT FORMAT: Each line is [N] [spk:ID] {text} where [N] is "
    "a bare integer sentence number and [spk:ID] is a speaker identifier.  "
    "Cite sentences as [67] (bare integer, no prefix)."
    "\n\n"
    "SPEAKER IDENTITY:\n"
    "- Transcript speakers are labeled only as [spk:ID] (e.g. [spk:0]). "
    "Display names are applied later by the UI — never invent or paste "
    "a display name next to a speaker tag.\n"
    "- Meeting speaker / assignee among transcript speakers → use ONLY "
    "[spk:ID]. Prefer ID whenever the person can be tied to a [spk:…] "
    "(who spoke, who owns a task, who decided).\n"
    "- Person named in dialogue who is NOT that speaker slot → plain "
    "name as spoken, only when you cannot map them to a [spk:ID].\n"
    "- NEVER combine both for the same person in one clause "
    "(illegal: \"[spk:0] Alex …\", \"Alex [spk:0] …\").\n"
    "- Prefer [spk:ID] over a bare name whenever either would work.\n"
    "- Use [spk:ID] only when attributing a claim, decision, or action "
    "to a person.  Do NOT prefix every sentence with the speaker tag."
    "\n\n"
    "HARD RULES:\n"
    "- NEVER invent or guess sentence numbers or speaker IDs.  Only use "
    "IDs that appear in the transcript.\n"
    "- If no single sentence directly supports a claim, do NOT attach a "
    "ref.  An unsubstantiated claim without a ref is better than a "
    "wrong ref."
)

# ── Aliases: all three prompts point to the same string object ──
MEETING_BLUEPRINT_SYSTEM = _MEETING_V3_SHARED_SYSTEM
MEETING_TAGGER_V3_SYSTEM = _MEETING_V3_SHARED_SYSTEM
MEETING_SUMMARIZER_V3_SYSTEM = _MEETING_V3_SHARED_SYSTEM


# ═══════════════════════════════════════════════════════════════════════
# Meeting — Summary Translation
# ═══════════════════════════════════════════════════════════════════════

# Supported target languages for summary translation.  Maps the short
# upper-case code (used in the translation file name `{tab_id}_{LANG}.md`
# and in the API) to the full language name injected into the prompt.
TRANSLATION_LANG_NAMES: dict[str, str] = {
    "CN": "Chinese (Simplified)",
    "EN": "English",
    "JA": "Japanese",
    "KO": "Korean",
    "FR": "French",
    "DE": "German",
    "ES": "Spanish",
}

# MEETING_TRANSLATION_SYSTEM
#   Purpose: System prompt for translating a generated meeting summary into
#     a target language.  Unlike _MEETING_V3_SHARED_SYSTEM (which hard-forces
#     same-language output), this prompt explicitly authorizes the language
#     switch while preserving markdown structure and citation markers.
#   Role: system
#   Called by: src/meeting/service.py → MeetingService.translate_summary
#   Template vars: (none)
MEETING_TRANSLATION_SYSTEM = (
    "You are a professional translator.  Translate the given document into "
    "the target language specified by the user.  Output ONLY the translated "
    "document — no preamble, no commentary, no markdown fences."
    "\n\n"
    "RULES:\n"
    "- Preserve the markdown structure exactly: headings, lists, bold, "
    "italic, and line breaks carry over unchanged.\n"
    "- Keep citation markers such as [67], speaker tags such as "
    "[spk:ID], and priority tags such as [priority: high] verbatim — never translate, reorder, or drop them.\n"
    "- Translate naturally and fluently into the target language; prefer "
    "idiomatic phrasing over word-for-word literalness.\n"
    "- Render proper nouns (people, products, organizations) according to "
    "the conventions of the target language.\n"
    "- Keep the same level of detail — translate everything, add nothing."
)

# MEETING_TRANSLATION_PROMPT
#   Purpose: User prompt that supplies the source summary markdown followed
#     by the target language.  The target language is placed AFTER the source
#     document so the model reads the full content before the instruction.
#   Role: user
#   Called by: src/meeting/service.py → MeetingService.translate_summary
#   Template vars: {source_md} — the original summary markdown to translate
#                  {target_language} — full target language name
#                    (e.g. "Chinese (Simplified)")
MEETING_TRANSLATION_PROMPT = """\
<document>
{source_md}
</document>

<task>
Translate the document above into {target_language}.  Follow every rule \
from your instructions: keep the markdown structure, keep citation markers \
like [67], speaker tags like [spk:ID], and priority tags like [priority: high] verbatim, and output only the \
translated document.
</task>
"""


# ═══════════════════════════════════════════════════════════════════════
# Meeting v3 — Full-Transcript Tagger (one-shot, replaces per-chunk loop)
# ═══════════════════════════════════════════════════════════════════════

# MEETING_TAGGER_V3_PROMPT
#   Purpose: Classifies every sentence in the full transcript for a single
#            section in one LLM call.  Outputs sentence_ids array.
#
#   KV-cache layout:
#     [system] + <transcript> + </transcript> + <Other-Section-in-the-Meeting>
#     form the largest stable prefix across per-section calls in the same meeting.
#     transcript is byte-identical; existing differs only in the excluded
#     section name.  Cache hit covers ≈ system(200) + transcript(5K) +
#     existing(1K) ≈ 6K tokens per call.  <task-rules>, <examples>, and
#     <task> live AFTER this stable prefix; rules and examples are fixed
#     but break cache once the prefix ends, so they cost ~2.5K tokens
#     per call to re-tokenize.  Keeping transcript / existing at the
#     front preserves the cache hit on the largest stable block.
#
#   Role: user
#   Called by: src/meeting/service.py → MeetingService.extract_sections()
#              and MeetingService._extract_section_stream()
#   Template vars: {transcript}              — full transcript [N] [spk:ID] {text}
#                  {other_sections}           — other tabs' name+description
#                  {section_name}             — target section name
#                  {section_description}      — target section description
MEETING_TAGGER_V3_PROMPT = """\
<transcript>
{transcript}
</transcript>

<hot-words>
{hot_words}
</hot-words>

<Other-Section-in-the-Meeting>
{other_sections}
</Other-Section-in-the-Meeting>

<task-rules>
The meeting covers multiple sections (listed in
<Other-Section-in-the-Meeting> above).  Prefer a focused set over a
noisy one, but capture every contiguous discussion of the target
section in full — including continuations, callbacks, and short
affirmations within the same region.

Region-by-region reasoning only.  Scanning sentence-by-sentence
will miss the discourse structure that makes tagging decidable —
you cannot reliably tell what a sentence is about from the
sentence alone (explicit naming is rare, most sentences rely on
context from the surrounding region).  Tag the way a human would:
first build a mental map of what each part of the meeting is
about, THEN assign tags region by region.

────────────────────────────────────────
PHASE 1 — Build a region map (in your reasoning, do not output)

Walk through the entire transcript and group sentences into
contiguous regions.  A region is a run of sentences on the same
topic/entity, ending when the speaker switches to something else.

For each region, note in your reasoning:
  - the sentence ID range (e.g. [10–14])
  - the entity/topic it discusses (e.g. "Project X Q3 budget")
  - whether it switches entity from the previous region

A region boundary happens when:
  - the speaker explicitly names a different entity
  - the speaker responds to a question about a different entity
  - the topic visibly shifts (general policy, greeting, tech check)

A single-sentence switch creates a new region even when surrounded
by another entity — when the speaker returns ("OK, back to X"),
that return point starts a fresh region.  A back-reference inside
another region ("and apply the same thing to X too") stays in its
current region but is a deliberate callback — handle in Phase 2.

Keep short regions (1–2 sentences) as standalone regions.  Rapid
back-and-forth between entities is normal — fragmented regions are
a feature, not a defect to clean up.

Pronouns and short forms ("this case", "it", "they", "that thing",
"the project we just talked about") resolve against the most recent
named entity in the same region.  When the current region is too
short to contain one (a 1-sentence region after a switch), fall
back to the named entity of the immediately preceding region.

────────────────────────────────────────
PHASE 2 — Assign tags region by region

For each region in your map, look up its entity/topic:

  - Region is about the target section         → tag every sentence.
  - Region is about a DIFFERENT section        → tag none of it.
  - Region is general policy / greeting        → tag none of it.

Callbacks: a sentence in a non-target region that explicitly refers
BACK to an earlier target region IS part of the target — tag it,
even though its surrounding region is not.

Short affirmations ("ok", "yeah", "right", "got it", "sure")
following a tagged sentence in the same region → tag.  The same
word in a non-tagged region → skip.
</task-rules>

<examples>
The four examples below show the same tagging task with different
target sections.  Read them as patterns, not templates — the goal
is to internalize the region-by-region reasoning, then apply it
to the real transcript above.

────────────────────────────────────────────────────────
EXAMPLE A — Target: "Project X" (Q3 budget and staffing review)

[10] [spk:0] OK, let's start with Project X.
[11] [spk:1] X's Q3 budget is about 12% over.
[12] [spk:1] Mainly equipment procurement and outsourcing.
[13] [spk:0] What about staffing?
[14] [spk:1] Still hiring, should be decided next week.
[15] [spk:2] By the way, what's the status on Project Y's contract?
[16] [spk:0] Y's legal review is still pending, conclusion next week.
[17] [spk:1] OK, back to X's headcount planning.
[18] [spk:1] I'd suggest pulling the two senior positions from Q4 into Q3.

Expected: [10, 11, 12, 13, 14, 17, 18]
Why:
- 10 explicit naming → tag
- 11–14 same contiguous block, subject elided across turns → tag
- 15 explicit switch to Y → skip
- 16 about Y → skip
- 17 explicit "back to X", resumes 14's staffing thread → tag
- 18 continues 17, still X staffing → tag

Pattern: long block + mid-block switch + return.  Continuity IS
the rule — do not split-tag only on explicit naming.

────────────────────────────────────────────────────────
EXAMPLE B — Target: "Case #2024-001" (data compliance review for Client A)

[44] [spk:0] Next, let's review case 001's compliance issues.
[45] [spk:1] Client A's data export plan is still waiting on legal.
[46] [spk:1] Last time we said we needed an impact assessment.
[47] [spk:0] Right, their IT team submitted a draft last week.
[48] [spk:0] Still has a lot of gaps.
[49] [spk:2] How do we usually handle this kind of situation?
[50] [spk:1] Usually we run DPIA first, then legal review.
[51] [spk:1] For this case I estimate another two weeks.
[52] [spk:3] OK, I'll follow up.

Expected: [44, 45, 46, 47, 48, 49, 51]
Why:
- 44 explicit naming → tag
- 45 "Client A" explicit naming → tag
- 46 continues 45's plan topic, no entity switch → tag
- 47 "their" refers to 45's "Client A", continuity → tag
- 48 continues 47's IT draft → tag
- 49 question about 001's compliance process, still this section → tag
- 50 general-process answer ("Usually we run DPIA"), not anchored
  to 001 → skip
- 51 "this case" far-range reference back to 44 → tag
- 52 short closing affirmation of 51 → tag

Pattern: pronoun chain across gaps + the subtle line between
"general process" (skip) and "question about this case's process"
(tag).

────────────────────────────────────────────────────────
EXAMPLE C — Target: "Calculus" (derivatives, integrals, limits, taught in Week 1–3)

[80] [spk:0] OK moving on, today we start derivatives.
[81] [spk:1] Right, so dy/dx is the rate of change.
[82] [spk:2] By the way, are derivatives also covered in Statistics?
[83] [spk:1] Briefly, but Statistics focuses on distributions.
[84] [spk:0] OK back to Calculus — what about integration by parts?

Expected: [80, 81, 82, 84]
Why:
- 80 explicit "derivatives" → tag
- 81 about derivatives (matches Calculus scope) → tag
- 82 mentions Statistics, but the question is anchored in the
  Calculus perspective — "are derivatives also covered there?"
  is itself a Calculus-side question → tag
- 83 explicitly switches to Statistics ("Statistics focuses on
  distributions") → skip
- 84 explicit "back to Calculus" + integration by parts → tag

Pattern: this is the SAME region-and-switch reasoning as Examples
A and D, just in a classroom domain instead of project tracking.
The rule is domain-independent — apply the section description
literally, decide per-region, treat pronouns and short forms
against the region map, capture callbacks.  When a sentence
spans two sections, tag it under whichever one is the CURRENT
FOCUS of the discussion at that point — not whichever name
happens to appear first.

────────────────────────────────────────────────────────
EXAMPLE D — Target: "Marketing" (campaigns, brand, paid acquisition)

[10] [spk:0] Let me check on the Q4 marketing campaign status.
[11] [spk:1] The launch date is pushed to November.
[12] [spk:1] We're still finalizing the creative assets.
[13] [spk:2] Quick question — has the Engineering team finished the new landing page?
[14] [spk:1] Almost, they're debugging the form submission issue.
[15] [spk:0] OK back to Marketing. What's the budget for paid ads?
[16] [spk:1] Around 200k, pending Finance approval.

Expected: [10, 11, 12, 15, 16]
Why:
- Region map (in your head):
    [10–12]   Marketing — Q4 campaign
    [13–14]   Engineering — landing page
    [15–16]   Marketing — paid ads budget
- Each entity switch creates a new region, even when regions are
  only 1–2 sentences long.  Keep short regions as standalone —
  rapid back-and-forth between entities is normal.
- Tag every sentence whose region is about Marketing; skip every
  sentence whose region is about Engineering or a different scope.
- 16 mentions "Finance approval" but the discussion is operating
  from the Marketing side (it's about Marketing's budget
  allocation) → tag.  Mentioning another entity is not a skip
  signal.

Pattern: this is the SAME region-and-switch reasoning as Examples
A, B, and C, just in a function/department-style domain
(Marketing vs Engineering) instead of project tracking, case
work, or classroom topics.  The rule is dimension-agnostic —
apply the section description literally, decide per-region,
capture short affirmations and callbacks, regardless of whether
the user organizes their world by project, by function, by
case, by subject, or by something else entirely.
</examples>

<task>
This section is about: {section_name}
{section_description}

OUTPUT (JSON):
{{"sentence_ids":[<the IDs, in chronological order>]}}

Use the bare integer IDs as they appear in each transcript line header.
</task>"""


# ═══════════════════════════════════════════════════════════════════════
# Meeting v3 — Section Summarizer (FOCUS + NEARBY, full-transcript context)
# ═══════════════════════════════════════════════════════════════════════

# MEETING_SUMMARIZER_V3_PROMPT
#   Purpose: Generates a focused markdown summary for one section using
#            tagged FOCUS sentences + NEARBY context + full transcript.
#   Role: user
#   Called by: src/meeting/service.py → MeetingService.extract_sections()
#   Template vars: {transcript}              — full transcript [stt_XXXX] [spk:ID] {text}
#                  {other_sections}           — other tabs' name+description
#                  {section_name}             — target section name
#                  {section_description}      — target section description
#                  {focus_sentences}          — FOCUS sentences (tagged by Tagger)
#                  {neighbor_sentences}       — NEARBY sentences (context only)
MEETING_SUMMARIZER_V3_PROMPT = """\
<transcript>
{transcript}
</transcript>

<hot-words>
{hot_words}
</hot-words>

<Other-Section-in-the-Meeting>
{other_sections}
</Other-Section-in-the-Meeting>

<task>
Write a focused meeting summary for the section specified in
<target-section>.  Be thorough on substance but concise in
expression — capture every distinct discussion thread, decision,
and data point, but present each one efficiently without
repetition or filler.

This meeting covers multiple sections (listed in
<Other-Section-in-the-Meeting>).  Use them for context only — do
NOT include their content in your output.  Focus exclusively on
<target-section>.

Language: Output MUST be in the same language as the transcript.
If the transcript is English, write in English.  If Chinese, write
in Chinese.  NEVER switch languages — this is a hard failure.

Produce a Markdown document with these sections:

## Summary
A 3-5 paragraph overview covering all distinct discussion threads,
decisions, and outcomes found in FOCUS sentences.  Be information-
dense — prefer one well-crafted paragraph over three vague ones.
Use [spk:ID] and [N] references (copy the number from the header).
Follow SPEAKER IDENTITY rules below (never write [spk:ID] next to
the same person's bare name).

Use [N] to cite source sentences.

SPEAKER IDENTITY — CRITICAL (applies to Summary, Todo, Data & Facts,
and Detail):

The transcript labels speakers only as [spk:ID] (e.g. [spk:0], [spk:1]).
Display names are applied later by the product UI — you must NOT invent
or paste display names next to a speaker tag.

Two distinct cases:

1) Meeting speaker / assignee among transcript speakers
   → Use ONLY [spk:ID]. Never also write that person's name.
   Prefer ID whenever the person can be tied to a [spk:…] in the
   transcript (who spoke, who owns a task, who decided).

2) Person mentioned in dialogue who is NOT used as a speaker slot
   → Write their name as spoken in the transcript (plain text only).
   Use a bare name ONLY when it is clearly a third party or someone
   referred to by name and you cannot map them to a [spk:ID].
   If they can be mapped to a speaker ID, use [spk:ID] instead (prefer ID).

HARD RULES:
- NEVER combine both forms for the same person in one clause
  (illegal: "[spk:0] Alex …", "Alex [spk:0] …", "[spk:0] Alex to …").
- NEVER write a speaker's name in place of [spk:ID] for attribution.
- Prefer [spk:ID] over a bare name whenever either would work.
- Bare names are only for people clearly named in dialogue who are
  not identified as a transcript speaker for that attribution.

GOOD:
- [spk:0] to prepare the Q3 budget report [priority: high]
- [spk:1] recommended Option B; the team agreed. [42]
- External vendor Northline to send revised quotes by Friday [88]
  (third party named in dialogue, not a [spk:] in this meeting)

BAD (double labeling — forbidden):
- [spk:0] Alex to prepare the Q3 budget report
- Alex [spk:0] to prepare the Q3 budget report
- [spk:1] Jordan recommended Option B

BAD (used bare name when a speaker ID applies):
- Alex to prepare the Q3 budget report
  (when Alex is [spk:0] in the transcript)

## Todo
Every action item, commitment, or deadline found in FOCUS sentences.

Format when the doer is a meeting speaker:
  "- [spk:ID] task description [priority: high|medium|low]"
Format when the doer is only a named third party (not a [spk:ID]):
  "- <Name as in transcript> task description [priority: …]"

Attribution rule — CRITICAL:
Attribute each task to the person expected to DO it, NOT the person
who merely mentioned it.
Example: if [spk:0] says "Pat should update the dashboard", and Pat
is not a transcript speaker, the task is Pat's (bare name). If Pat
is [spk:2], write [spk:2], not "Pat", and never "[spk:2] Pat …".

Priority: append [priority: high], [priority: medium], or
[priority: low] at the end of each bullet when urgency is indicated.

Examples:
- [spk:0] to prepare the Q3 budget report [priority: high]
- [spk:1] to circulate the meeting notes [priority: medium]
- Vendor Northline to send revised quotes by Friday [priority: medium]

## Data & Facts
Every data point, figure, metric, decision, and deadline found in
FOCUS sentences.  Present each as a standalone bullet.

REF ACCURACY — CRITICAL:
- Before writing a [N] ref, verify that the sentence text ACTUALLY
  contains the data point or claim being cited.
- If no single sentence directly supports a fact, do NOT add a ref
  tag.  An unsupported fact without a ref is better than a wrong ref.
- Combine multiple IDs with commas: [67,70] or ranges
  with a dash: [67-70].  NEVER concatenate IDs without a
  comma or dash separator.

Example:
- [spk:0] reported Q3 revenue at $2.1M, a 15% increase YoY. [12,15]

## Detail
A condensed narrative of the discussion about this section.
Write as a human note-taker would — synthesize discussion threads,
do NOT reproduce the transcript turn by turn.

CONTENT FILTER — what to INCLUDE vs EXCLUDE:

INCLUDE (substance):
  - Technical data: numbers, specs, parameters, test results
  - Decisions, conclusions, and agreements reached
  - Disagreements, open questions, and concerns raised
  - Action items and commitments (who does what by when)
  - Key context that explains why a decision was made

EXCLUDE (procedure):
  - Introductions, greetings, attendance, role descriptions
  - Meeting logistics: scheduling, screen sharing, agenda order
  - Meta-discussion: "let's move to the next topic", "I have a hard stop"
  - Polite filler: thanking, praising, acknowledging without substance
  - Repeated confirmations ("got it", "understood", "noted") with no new info
  - Tangents that were explicitly dropped or deferred

PARAGRAPH STRUCTURE — topic-based, NOT turn-based:

Group related discussion into topic paragraphs, even if that means
merging multiple speaker turns into one paragraph.  A paragraph
should cover ONE topic thread from start to resolution (or deferral).

When the Detail section covers multiple distinct topics, use markdown
sub-headings (### level) to separate them.  ALWAYS leave a blank
line between a sub-heading and the paragraph that follows it.

WRITING STYLE — final answers only, no discussion journey:

Your job is to state WHAT was decided, concluded, or found — NOT to
recount WHO said what or HOW the discussion unfolded.  Strip out the
Q&A process entirely.  If [spk:A] asked a question and [spk:B]
answered, write only the answer.

BAD (narrates the discussion journey):
  [spk:A] asked about Topic X, noting Fact 1. [N]
  [spk:B] explained that the reason is Condition C. [N]
  [spk:A] confirmed that this means Outcome O. [N]

GOOD (states the final answer directly):
  Topic X operates under Condition C, resulting in Outcome O [N-N].

BAD (attributes every fact to a speaker):
  [spk:B] stated the capacity is N units. [spk:C] noted the cost is
  $M. [spk:B] added that the timeline is D months.

GOOD (states facts directly, speaker only for opinions/decisions):
  Capacity is N units at a cost of $M with a D-month timeline [N-N].
  [spk:B] recommended proceeding with Option A.

BAD (every sentence gets a ref — noisy):
  The system uses N units, each V m³ [ref].  The loading rate is R
  kg/m³ with a D-day retention time [ref].

GOOD (refs only on key data, combined):
  The system uses N units of V m³ each at R kg/m³ loading with a
  D-day retention time [ref-ref].

INFERENCE RULES — what you MAY vs MAY NOT infer:

MAY (simple, single-step, directly from stated numbers):
  - "tripling X" → "saves roughly two-thirds"
  - "A is 30% higher than B" → "B is roughly 23% lower than A"
  - "raised from $5M to $8M" → "a $3M / 60% increase"

MAY NOT (multi-step, domain-specific, or requires outside knowledge):
  - Financial projections (NPV, IRR, payback period)
  - Comparing options that were not directly compared in the meeting
  - Drawing conclusions that require technical domain expertise
    beyond what is stated in the transcript
  - ANY inference where you cannot point to the exact source
    sentences that contain the input numbers

When in doubt, state the raw numbers and let the reader draw
their own conclusions.

SENTENCE REFERENCES:
- Use [N] refs for key data points, numbers, decisions, and direct
  quotes.  Do NOT add refs to every sentence — narrative context
  and transitional prose do not need refs.
- Place [N] at the end of the clause it supports.
- Combine IDs: [67,70] or ranges [67-70].
- NEVER invent or concatenate IDs.

FORMAT — the Detail section MUST use this structure:

When the section covers ONE topic:
  ## Detail
  A standalone paragraph (no sub-heading needed).

When the section covers MULTIPLE topics:
  ## Detail
  ### Topic A Name
  Paragraph describing topic A.  Use [N] refs where appropriate.

  ### Topic B Name
  Paragraph describing topic B.  Use [N] refs where appropriate.

CRITICAL: Every sub-heading (###) MUST be followed by a blank line
before the paragraph begins.  Each paragraph MUST be separated from
the next sub-heading by a blank line.  Without these blank lines the
output is unreadable wall-of-text — this is a hard formatting
requirement.
</task>

<focused-sentences>
=== Sentences identified as belonging to this section, in
    chronological order.  Lines prefixed with [FOCUS] are anchor
    seeds selected by the sentence classifier.  Unmarked lines
    are temporally adjacent sentences that MAY provide context
    — verify they are actually about this section before using.
    When in doubt, rely on [FOCUS] sentences.  The full
    <transcript> above provides additional background — use it
    only to confirm topic boundaries, never to pull in extra
    content beyond the sentences listed here.

    IMPORTANT: These sentences are listed chronologically for
    reference, NOT as a writing outline.  In your Detail section,
    group them by topic — merge related sentences from different
    speakers into the same paragraph.  Do NOT write one paragraph
    per sentence or per speaker turn. ===
{merged_sentences}
</focused-sentences>

<target-section>
Name: {section_name}
Description: {section_description}
</target-section>"""


# ═══════════════════════════════════════════════════════════════════════
# Meeting — AI Section Description Generator
# ═══════════════════════════════════════════════════════════════════════

# SECTION_DESC_PROMPT
#   Purpose: Generates a section description based on the section name
#            and the meeting's General Summary content.  Used by the
#            Add Section dialog's AI assist button.
#   Role: user
#   Called by: src/meeting/service.py → MeetingService.generate_section_description()
#   Template vars: {section_name}       — user-entered section name
#                  {general_summary}    — meeting.detail (General tab markdown)
#                  {existing_sections}  — list of already-created section tabs
SECTION_DESC_PROMPT = """\
<general-summary>
{general_summary}
</general-summary>

<hot-words>
{hot_words}
</hot-words>

<taxonomy>
{taxonomy}
</taxonomy>

<existing-sections>
{existing_sections}
</existing-sections>

<task>
Respond with a JSON object.

A sentence classifier will use your description to decide whether each
sentence in the transcript belongs to this section.  The classifier
does NOT do keyword matching — it reads your description to understand
the TOPIC, then judges whether a sentence is part of the discussion
about that topic.

<taxonomy> describes how this meeting's sections are organized
(e.g. by project, by function, by department).  Use this to
understand what kind of entity "{section_name}" is — a project name,
a business function, a department, etc.  Write the description
accordingly.

<existing-sections> lists other sections already created for this
meeting.  Do NOT describe content already covered by them.  Only
describe what belongs to "{section_name}" and not to any existing
section.

Scan <general-summary> for content related to "{section_name}".

If nothing in <general-summary> relates to "{section_name}":
{{"found":false}}

If there IS relevant content, write a ``description`` (max 400 chars)
that describes ONLY what <general-summary> says about
"{section_name}" in this specific meeting.  Focus on what
distinguishes "{section_name}" from the other sections — the
classifier uses this to identify which conversation segments belong
here.

CRITICAL — MEETING-ONLY: Derive the description EXCLUSIVELY from
<general-summary>.  <general-summary> is a summary of THIS meeting's
transcript — it tells you what was actually discussed.  Do NOT inject
general knowledge about what "{section_name}" typically involves.

CRITICAL — SCOPE BOUNDARY:
- Only describe content that explicitly belongs to "{section_name}".
- Content discussed in connection with OTHER entities does NOT belong
  to "{section_name}" unless explicitly linked to it.
- If a sentence could appear in another section's summary without
  feeling out of place, do NOT use it as a signal.
- When in doubt, EXCLUDE.  A focused, narrow description produces far
  better classifier results than a broad one.

CRITICAL — CROSS-CUTTING CONCEPTS: When a general method, model, or
approach was discussed, describe ONLY how it applies specifically to
"{section_name}".  Do NOT list the general concept as a standalone
signal — the classifier will tag every sentence about that concept
regardless of entity.

CRITICAL — NO DATA POINTS: Do NOT list specific numbers, prices,
percentages, or data points from <general-summary>.  Those data
points may belong to other topics and will mislead the classifier.
Describe the TOPIC and SCOPE, not the concrete values.

Output: {{"found":true,"description":"..."}}
</task>"""


# ═══════════════════════════════════════════════════════════════════════
# Meeting Quick Chat
# ═══════════════════════════════════════════════════════════════════════

# MEETING_CHAT_SYSTEM_PROMPT
#   Purpose: System prompt for the Meeting sidebar chat.  The LLM answers
#            questions about a specific meeting's transcript, which is
#            injected ephemerally on every turn (after this prompt, before
#            dialogue history) by ChatboxAgent._build_messages — not stored
#            in the session.  A separate ephemeral speaker mapping (via
#            pre_message_context) resolves speaker IDs to display names.
#   Role: system
#   Called by: src/chatbox/agent.py → ChatboxAgent._resolve_tools_and_prompt
#              (when session_id starts with "meeting_")
#   Template vars: none (transcript and mapping are injected elsewhere)
MEETING_CHAT_SYSTEM_PROMPT = """\
You are a meeting transcript Q&A assistant. Immediately after this prompt, \
a second system message provides either (a) the full meeting transcript, \
or (b) an explicit "MEETING TRANSCRIPT STATUS: unavailable" notice. \
A separate speaker mapping (injected as context before each user message) \
resolves speaker IDs to display names.

YOUR ROLE:
- Answer only from the provided transcript message. Prefer paraphrasing \
and synthesis over verbatim quoting.
- If the transcript status is unavailable (or the transcript body is empty), \
tell the user clearly that you do not have the meeting record yet. Suggest \
waiting until transcription finishes or re-opening the meeting after it is ready.
- NEVER invent meeting content: no fabricated topics, decisions, action \
items, speaker quotes, template placeholders (e.g. [project name], \
[具体成果]), or fake structure filled with brackets.

NAME RESOLUTION (only when a real transcript is present):
1. When the user references a person by name (e.g. "What did John say?"), \
look up the speaker mapping to find the corresponding speaker ID, then \
locate that speaker's lines in the transcript and summarize them in your \
own words.
2. If no mapping entry matches the name, search the transcript directly \
for the name.  If still not found, inform the user: "No speaker named \
'{name}' found in this transcript.  Available speakers: {list}."
3. If the speaker mapping says "(unnamed)", tell the user that speaker \
has not been named yet.

CITATION FORMAT (only when a real transcript is present):
- Cite sentences as [N] (bare integer, no prefix) matching the sentence \
numbers shown in the transcript. Place [N] after the relevant sentence \
or paragraph — right after the cited fact or claim.
- Combine IDs: [67,70] or ranges [67-70] for closely related references.
- NEVER invent sentence numbers — only cite numbers that actually appear \
in the transcript.
- ONLY use [N] citations. Do NOT embed quoted transcript text in your \
answer — the user can follow the [N] link to hear the original audio.
- When multiple speakers discuss the same topic, attribute each point to \
the correct speaker.
- When the transcript is unavailable, do not use [N] citations at all.

WRITING STYLE:
- Write in natural, fluent prose. You are having a conversation, not \
presenting evidence excerpts.
- When the user asks about a topic and a real transcript is present, \
synthesize the relevant points into a coherent answer. Do NOT read off a \
list of verbatim quotes with citations.
- A good answer distills the discussion: "John proposed launching in Q3 \
and cited budget approval as the key dependency [45-48]" is better than \
"John said: 'We should launch in Q3 because...' [45] He also said: 'The \
budget...' [46]"
- Reserve direct quotation ONLY when the exact wording matters (e.g., a \
specific decision, name, or number that must be precise).
- When the user specifically asks for the exact wording or a direct quote, provide the
original transcript text with [N] citation.

WHEN INFORMATION IS MISSING:
- If the transcript is unavailable, say so and stop — do not fill gaps \
with guesses or placeholders.
- If a real transcript is present but does not contain information relevant \
to the question, say so clearly and suggest related topics that ARE in the \
transcript.

FORMATTING:
- Use Markdown for readability (headers, lists, bold/italic).
- Keep answers focused — this is a quick Q&A, not a research report.
"""


# ═══════════════════════════════════════════════════════════════════════
# Timeline — Smart To-do Suggestions
# ═══════════════════════════════════════════════════════════════════════

# TODO_SUGGEST_SYSTEM_PROMPT + TODO_SUGGEST_USER_PROMPT
#   Purpose: Given an ordered timeline chain context (node titles, groups,
#            plain-text messages, attachment short summaries) plus open todo
#            titles already on that chain, propose 1–3 concrete next todos.
#   Role: TODO_SUGGEST_SYSTEM_PROMPT → system
#         TODO_SUGGEST_USER_PROMPT   → user
#   Called by: src/file_mgmt/todo_suggestions.py → llm.generate()
#   Template vars:
#     {chain_context}     — formatted multi-node timeline text
#     {open_todo_titles}  — bullet list of existing open todo titles (or "(none)")
TODO_SUGGEST_SYSTEM_PROMPT = """You suggest process-aware next to-dos for a project timeline chain.

Before writing the JSON, reason privately (do not output this reasoning):
- What workflow is this chain on (e.g. due diligence, delivery, review), using chain/node titles, groups, messages, and attachment summaries.
- Where the work is now after the latest nodes, and what the natural next step(s) in that workflow are.
- Ground every suggestion in the chain; do not invent entities or facts not supported by the context.
Do not put citations, "based on node…", or evidence lists in the output.

Rules for the suggestions:
- Propose 1–3 todos that continue this workflow — not generic project-management advice.
- Prefer concrete, completable actions (including scheduling a meeting when that is the right next step: purpose and expected outcome).
- Avoid duplicating or restating open todos already listed.
- Each title: a few words, no trailing period.
- Each body: a refinement of that todo only — how to carry it out, scope, key sub-steps or checks, and what done looks like. Short markdown, typically 3–5 sentences (or equivalent short bullets). Prefer a body on every item. Do not restate the title as the whole body.
- Language: match the dominant language of the chain context. If the context mixes languages, write in English.
- Output ONLY a JSON array (no markdown fences, no commentary, no reasoning preamble):
  [{"title":"...","body":"..."}, ...]
- If the chain has almost no signal, return a single high-level next step rather than an empty array.
"""

TODO_SUGGEST_USER_PROMPT = """## Timeline chain (oldest → newest)

{chain_context}

## Open todos already on this chain (do not duplicate)

{open_todo_titles}

Infer the workflow and current stage from the chain, then return 1–3 next-step todo suggestions as a JSON array.
Each body should refine that todo (how / scope / done criteria), not cite sources.
"""
