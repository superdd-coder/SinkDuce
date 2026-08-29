"""Chat query-tool registry: schemas, allowlists, collection scope, structure dispatch.

Search facades (``search_knowledge_base`` / ``lookup_collection``) stay owned by
ChatboxAgent; this module supplies structure / summary / full-text tools that
reuse the same business logic as MCP without HTTP self-calls.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Per tool-result JSON string cap (Chat path). Keep ≥ get_document_text hard max.
_MAX_RESULT_CHARS = 96000
# get_document_text character window (Chat only; MCP may use its own defaults).
_CHAT_DOC_DEFAULT_LIMIT = 32000
_CHAT_DOC_MAX_LIMIT = 96000

# ── Shared policy snippets (keep short — models overweight repeated prefixes) ─

_FULL_TEXT_POLICY = (
    "LOW PRIORITY full-text. Prefer search for normal Q&A. "
    "Call ONLY when (1) user asks to read a named file / full text / a version, "
    "OR (2) search chunks are clearly insufficient for continuous original text. "
    "Do not call 'just in case' or as a substitute for search."
)

# Collection ID reminder only — do NOT recommend a specific structure tool here
# (a blanket "prefer list_library_tree" made the model over-call that tool).
_COL_NOTE = (
    "collection must be a collection **ID** (from list_collections), never a display name."
)

# Routing one-liners used only on the tools they apply to
_SEARCH_FIRST = (
    "PRIMARY for content facts / document Q&A. "
    "Do NOT call list_library_tree / get_timeline first when the need is what a "
    "document says — search (or summary tools) first."
)


def _fn(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        params["required"] = required
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params,
        },
    }


_COL = {
    "type": "string",
    "description": (
        "Collection **ID** (e.g. col_abc123 or default), never the display name. "
        "Use list_collections first when unsure."
    ),
}

# ── Search facades (owned by Chat; schemas live here for one registry) ─

SEARCH_KNOWLEDGE_BASE_TOOL = _fn(
    "search_knowledge_base",
    (
        f"{_SEARCH_FIRST} "
        "Search ingested document chunks across the private knowledge base.\n\n"
        "PLANNING:\n"
        "1. Vague/ambiguous question → clarify first.\n"
        "2. Chitchat / common knowledge → answer without tools.\n"
        "3. DEFAULT: one call/round; pack multi-facet needs with decompose=true.\n"
        "4. Multiple rounds only for dependency chains (N+1 needs N results).\n"
        "5. Prefer over get_document_text / get_file_chunks for normal Q&A.\n"
        "6. Browse tools (list_library_tree / get_timeline / list_files) are for "
        "library layout or project events — NOT for answering content questions."
    ),
    {
        "raw_query": {
            "type": "string",
            "description": (
                "WHAT information you need — not the user question verbatim. "
                "Natural phrase naming entities and aspects."
            ),
        },
        "generate_answer": {
            "type": "boolean",
            "default": False,
            "description": (
                "When false (default), return search context for you to synthesize. "
                "Set true only to request a preliminary answer from the query service."
            ),
        },
        "include_images": {
            "type": "boolean",
            "default": False,
            "description": "Include base64 images when your model supports vision.",
        },
        "decompose": {
            "type": "boolean",
            "default": True,
            "description": (
                "TRUE to pack multiple independent search targets into one call "
                "(parallel decompose). FALSE for a single focused search."
            ),
        },
    },
    required=["raw_query"],
)

LIST_MEETING_CATALOG_TOOL = _fn(
    "list_meeting_catalog",
    (
        "Full meeting directory with each meeting's summary head (first line "
        "of its General summary) — use the heads to route a question to the "
        "right meeting(s) before lookup_meeting_transcript. A minimal "
        "directory (titles + dates) is already in context; call this only "
        "when you need those summary heads, ids, or the full list. "
        "Not a content search."
    ),
    {
        "collection": {
            "type": "string",
            "description": (
                "Optional extra collection ID. Chat already limits the catalog "
                "to collections currently selected in the UI (all meetings if "
                "none). This further narrows to one of those. Quick Chat "
                "ignores this and uses the bound collection."
            ),
        },
    },
)

LOOKUP_MEETING_TRANSCRIPT_TOOL = _fn(
    "lookup_meeting_transcript",
    (
        "PRIMARY search over this meeting's spoken transcript (not the written summary). "
        "Each call is an independent search: there is no offset, continuation, or "
        "cursor. [ref:N] in the result is a sentence id, not a read position. "
        "A gap between excerpts is sentences that did not rank — not unread "
        "transcript. Do not look them up again. "
        "The server locks the search to the current meeting. "
        "Multiple distinct information needs → several lookup calls in the "
        "SAME round (up to 4), not one per round. "
        "If a named speaker filter returns nothing, tell the user; a short preview "
        "may show who actually said it. Ask before searching the whole meeting "
        "(speaker_scope=all). A name outside the mapping is a mention: put it in "
        "query and search; do not treat them as absent and do not use "
        "speaker_scope=all. Cite only [ref:N] ids that appear in the tool result."
    ),
    {
        "query": {
            "type": "string",
            "description": (
                "WHAT information you need — not a position in the meeting, "
                "and not the user question verbatim when it is broad. "
                "Natural phrase naming people, actions, numbers, or topics."
            ),
        },
        "speaker_scope": {
            "type": "string",
            "enum": ["auto", "all"],
            "description": (
                "auto (default): server may filter to speakers named in the user "
                "question. all: search the whole meeting (only after the user agrees)."
            ),
        },
    },
    required=["query"],
)

LOOKUP_GROUP_TRANSCRIPT_TOOL = _fn(
    "lookup_group_transcript",
    (
        "PRIMARY evidence: search spoken transcripts for meetings in this Group. "
        "Required before the user-facing answer. Written summaries are only a "
        "map of the series — use them to pick meeting_ids and the information "
        "need, then search here. The server locks the search to this Group. "
        "Pass meeting_ids from the roster (ids, not group numbers) to search a "
        "subset; omit to search every indexed member. One call is one retrieve "
        "— not a page. A name in the roster speaker maps is filtered per "
        "meeting with that meeting's speaker id. A name outside the mapping is "
        "a mention: put it in query; do not treat them as absent. Cite as "
        "[n:k] (n = meeting header, k = [ref:k] in excerpts). The user sees "
        "only n. Do not paste excerpt lines."
    ),
    {
        "query": {
            "type": "string",
            "description": (
                "WHAT information you need — not a tape position. "
                "Natural phrase naming people, actions, numbers, or topics."
            ),
        },
        "meeting_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional roster meeting_id values to search. "
                "Empty or omitted searches all indexed members of this Group."
            ),
        },
        "speaker_scope": {
            "type": "string",
            "enum": ["auto", "all"],
            "description": (
                "auto (default): per-meeting speaker filter when the user names "
                "a mapped speaker. all: no speaker filter (only after the user "
                "agrees a mapped-speaker filter was wrong)."
            ),
        },
    },
    required=["query"],
)

READ_MEETING_SUMMARY_TOOL = _fn(
    "read_meeting_summary",
    (
        "Read the written General summary for one visible meeting "
        "(id from list_meeting_catalog). Orientation / itinerary only: "
        "which meeting, who, what it is about, plus the action items it "
        "lists. Do not treat it as spoken evidence and do not answer "
        "content questions (who said what, exact numbers) from it alone — "
        "after reading, search spoken lines with lookup_meeting_transcript. "
        "WHEN NOT: the question is already answered by the summary head in "
        "the catalog, or is not about meeting content."
    ),
    {
        "meeting_id": {
            "type": "string",
            "description": "meeting_id from list_meeting_catalog.",
        },
    },
    required=["meeting_id"],
)

LOOKUP_SCOPED_TRANSCRIPT_TOOL = _fn(
    "lookup_meeting_transcript",
    (
        "PRIMARY search over spoken meeting transcripts (not written summaries). "
        "WHEN: the question is about meeting content — decisions, numbers, "
        "who said what, action items, topics discussed. "
        "WHEN NOT: document Q&A → search / lookup_collection; the directory "
        "shows no meeting that could cover the topic → do not call. "
        "Omit meeting_ids to search every indexed meeting in the current visible "
        "set. Pass catalog ids to narrow. Ids outside the visible set error. "
        "Unindexed meetings are skipped and named in the result. "
        "Each call is a new search. Already-returned packs are not repeated. "
        "A gap between excerpts is sentences that did not rank — not unread "
        "transcript. Do not look them up again. "
        "Search again only for a different information need (another topic or "
        "a subset of meetings), not to fill those gaps. "
        "Multiple distinct information needs → several lookup calls in the "
        "SAME round (up to 4), not one per round. "
        "speaker_scope=auto filters packs that contain speakers named in the "
        "user question when a mapping exists; all searches without that filter "
        "(only after the user agrees). "
        "Citation markers belong to the tool result only — the host renders "
        "citations per surface; never copy citation syntax into the answer."
    ),
    {
        "query": {
            "type": "string",
            "description": (
                "WHAT information you need — not a tape position. "
                "Natural phrase naming people, actions, numbers, or topics."
            ),
        },
        "meeting_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional meeting_id values from list_meeting_catalog. "
                "Empty or omitted searches all indexed meetings in the visible set."
            ),
        },
        "speaker_scope": {
            "type": "string",
            "enum": ["auto", "all"],
            "description": (
                "auto (default): per-meeting speaker filter when the user names "
                "a mapped speaker. all: no speaker filter (only after the user agrees)."
            ),
        },
        "collection": {
            "type": "string",
            "description": (
                "Optional extra collection ID. Chat already limits to currently "
                "selected collections (all if none). This further narrows to "
                "one of those. Quick Chat ignores this."
            ),
        },
    },
    required=["query"],
)

LOOKUP_COLLECTION_TOOL = _fn(
    "lookup_collection",
    (
        f"{_SEARCH_FIRST} "
        "Search the **current** collection's ingested chunks for factual Q&A. "
        "Prefer over get_document_text / get_file_chunks. "
        "Do not open list_library_tree just to answer 'what does X say'."
    ),
    {
        "query": {
            "type": "string",
            "description": (
                "WHAT to search for. Natural phrase; be specific about entities/topics."
            ),
        },
    },
    required=["query"],
)

WEB_SEARCH_TOOL_NAME = "request_web_search"


def build_request_web_search_tool(*, web_search_enabled: bool) -> dict[str, Any]:
    """Web tool schema with explicit enabled/disabled for the agent.

    Always exposed when an API key exists so the model sees toggle state and
    does not invent reasons (e.g. confuse a user Decline with Web=off).
    """
    try:
        from src.chatbox.web_search import has_web_search_api_key, web_toggle_label

        has_key = has_web_search_api_key()
        toggle = web_toggle_label(web_search_enabled=web_search_enabled)
    except Exception:
        has_key = False
        toggle = "disabled"

    status = toggle  # enabled | disabled
    if not has_key:
        status = "disabled"
        toggle = "disabled"

    if status == "enabled":
        desc = (
            f"Request an **internet** search (Tavily). "
            f"CURRENT STATE: web_toggle=enabled (status=enabled). "
            "Results are NOT from the private knowledge base.\n\n"
            "When you need public/current internet info (or KB lacks it): "
            "CALL this tool NOW with a focused query. "
            "Do NOT ask the user if Web is on or to send another message — "
            "the system shows Allow/Decline after you call.\n"
            "Tool results: status=ok|user_declined (user_declined ≠ disabled). "
            "Label WEB sources; never invent internet data."
        )
    else:
        desc = (
            f"Request an **internet** search (Tavily). "
            f"CURRENT STATE: web_toggle=disabled (status=disabled). "
            "Calling this tool returns status=disabled and no results. "
            "Prefer knowledge-base tools; if internet data is required, "
            "briefly note that Web is off. Do not invent internet data."
        )
    return _fn(
        WEB_SEARCH_TOOL_NAME,
        desc,
        {
            "query": {
                "type": "string",
                "description": (
                    "Focused web search query (keywords/phrases for the public internet)."
                ),
            },
        },
        required=["query"],
    )


# Default schema (enabled appearance) for static references / tests
REQUEST_WEB_SEARCH_TOOL = build_request_web_search_tool(web_search_enabled=True)

# ── Structure / summary / full-text schemas ────────────────────
# Each description is self-contained: WHEN + WHEN NOT. Avoid shared prefixes
# that push one tool (especially list_library_tree) onto every call.

STRUCTURE_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_collections": _fn(
        "list_collections",
        (
            "List all collections (id, name, points_count, catalog). "
            "Call when you need a collection **ID** or do not know which "
            "collection to use. Not a content search."
        ),
        {},
    ),
    "get_collection": _fn(
        "get_collection",
        (
            f"One collection's metadata, config, and stats. {_COL_NOTE} "
            "Not for browsing files or searching document content."
        ),
        {"collection": _COL},
        required=["collection"],
    ),
    "list_library_tree": _fn(
        "list_library_tree",
        (
            "Folder + file **layout map** for one collection (where files live). "
            f"{_COL_NOTE}\n"
            "WHEN: user asks what files/folders exist, where a file is mounted, "
            "or for a library directory map.\n"
            "WHEN NOT: factual Q&A / 'what does the doc say' → use search tools; "
            "project events / timeline nodes → get_timeline; flat unique file list "
            "with filters → list_files; chunk counts/legacy sources → list_documents.\n"
            "Note: file_count is always real; files=[] with truncated=true means "
            "payload omitted at that depth, not an empty folder."
        ),
        {
            "collection": _COL,
            "max_depth": {
                "type": "integer",
                "default": 0,
                "description": "0 = unlimited; 1 = files only at root folders.",
            },
            "fields": {
                "type": "string",
                "default": "summary",
                "description": "minimal | summary | full",
            },
        },
        required=["collection"],
    ),
    "list_folders": _fn(
        "list_folders",
        (
            "Folder tree **only** (no file rows). Rare. "
            f"{_COL_NOTE} "
            "Prefer list_library_tree when you also need files under folders."
        ),
        {"collection": _COL},
        required=["collection"],
    ),
    "list_files": _fn(
        "list_files",
        (
            "Flat **unique** file list with mounts (folder_ids). "
            f"{_COL_NOTE}\n"
            "WHEN: need all files, filter by folder_id / scope, or multi-mount "
            "detail without the folder tree.\n"
            "WHEN NOT: hierarchical 'show me the library' map → list_library_tree; "
            "content Q&A → search."
        ),
        {
            "collection": _COL,
            "folder_id": {
                "type": "string",
                "description": "If set, only files mounted in this folder.",
            },
            "scope": {
                "type": "string",
                "default": "all",
                "description": "all | orphans (when folder_id empty)",
            },
        },
        required=["collection"],
    ),
    "get_file": _fn(
        "get_file",
        (
            "Full detail for **one known file_id**: paths/mounts, versions, "
            f"linked timeline nodes, messages. {_COL_NOTE}\n"
            "WHEN NOT: only version/blob flags → list_file_versions; "
            "only body text → get_document_text; discovery by question → search."
        ),
        {
            "collection": _COL,
            "file_id": {"type": "string", "description": "Managed file id."},
        },
        required=["collection", "file_id"],
    ),
    "list_file_versions": _fn(
        "list_file_versions",
        (
            "Version history for one file_id with blob_available flags. "
            f"{_COL_NOTE} "
            "Call before get_document_text(version_id=…) on non-current versions. "
            "Not for content search."
        ),
        {
            "collection": _COL,
            "file_id": {"type": "string", "description": "Managed file id."},
        },
        required=["collection", "file_id"],
    ),
    "get_timeline": _fn(
        "get_timeline",
        (
            "Project **timeline / node graph** (events, branches, groups, "
            f"node attachments). {_COL_NOTE}\n"
            "WHEN: user asks about timeline, project events, meeting nodes, "
            "branches, or what happened in this project.\n"
            "WHEN NOT: folder/file library map → list_library_tree; "
            "document content facts → search; one known node body → get_node.\n"
            "Prefer this over list_chains + get_chain for a full graph."
        ),
        {"collection": _COL},
        required=["collection"],
    ),
    "list_chains": _fn(
        "list_chains",
        (
            "Timeline chain **skeletons only** (no nodes). Rare. "
            f"{_COL_NOTE} Prefer get_timeline for the full nested graph."
        ),
        {"collection": _COL},
        required=["collection"],
    ),
    "get_chain": _fn(
        "get_chain",
        (
            "One timeline chain with its nodes (need chain_id). "
            f"{_COL_NOTE} Prefer get_timeline when surveying the whole project."
        ),
        {
            "collection": _COL,
            "chain_id": {"type": "string"},
        },
        required=["collection", "chain_id"],
    ),
    "get_node": _fn(
        "get_node",
        (
            "One timeline **node** with full attachments and messages "
            f"(need node_id from get_timeline/get_chain). {_COL_NOTE} "
            "Not a file-library browser."
        ),
        {
            "collection": _COL,
            "node_id": {"type": "string"},
        },
        required=["collection", "node_id"],
    ),
    "list_groups": _fn(
        "list_groups",
        (
            "Timeline group labels only. Rare. "
            f"{_COL_NOTE} Prefer get_timeline for order + groups + nodes together."
        ),
        {"collection": _COL},
        required=["collection"],
    ),
    "list_documents": _fn(
        "list_documents",
        (
            "Legacy index: source keys + **chunk counts** (not folder mounts). "
            f"{_COL_NOTE}\n"
            "WHEN: need chunk counts or pre-file-mgmt collections.\n"
            "WHEN NOT (file-mgmt): folder/file map → list_library_tree; "
            "flat mounts → list_files. Not a content search."
        ),
        {"collection": _COL},
        required=["collection"],
    ),
    "get_document_text": _fn(
        "get_document_text",
        (
            f"{_FULL_TEXT_POLICY} "
            "Read a **character window** of extractable plain text for a **known** "
            "document (prefer file_id from search hits / library tools). "
            "NOT PDF page numbers.\n"
            "Default limit=32000 chars (Chat hard-caps at 96000; no unlimited).\n"
            "Returns has_more, next_offset, total_chars, truncated.\n"
            "PAGING: if has_more and the window lacks enough evidence, call again "
            "with offset=next_offset. Prefer starting from a search hit's char_offset. "
            "Stop when the answer is complete; do not page whole files by default."
        ),
        {
            "collection": _COL,
            "file_id": {
                "type": "string",
                "description": (
                    "Preferred managed file id from search hits, list_files, "
                    "or list_library_tree."
                ),
            },
            "source": {
                "type": "string",
                "description": "Optional legacy source key if file_id unknown.",
            },
            "version_id": {
                "type": "string",
                "description": "Historical version id; only when blob_available=true.",
            },
            "offset": {
                "type": "integer",
                "default": 0,
                "description": (
                    "Character offset into extractable text. "
                    "Use char_offset from a retrieved chunk header to jump near that passage."
                ),
            },
            "limit": {
                "type": "integer",
                "default": 32000,
                "description": (
                    "Max characters to return (default 32000). "
                    "Chat clamps to 32000–96000; 0 is treated as default 32000 (no unlimited)."
                ),
            },
        },
        required=["collection"],
    ),
    "get_file_chunks": _fn(
        "get_file_chunks",
        (
            f"{_FULL_TEXT_POLICY} "
            "Indexed chunks for **one known** file (what was embedded). "
            "Prefer search for discovery; prefer get_document_text for full body."
        ),
        {
            "collection": _COL,
            "file_id": {"type": "string", "description": "Preferred managed file id."},
            "source": {"type": "string", "description": "Optional source key."},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 50},
        },
        required=["collection"],
    ),
    "get_collection_summary": _fn(
        "get_collection_summary",
        (
            "LLM-generated overview of an ingested collection (from doc summaries). "
            "Useful for 'what is this collection about' before deep search. "
            "Not Collection Notes editor content; not a file list."
        ),
        {"collection": _COL},
        required=["collection"],
    ),
    "get_doc_summary": _fn(
        "get_doc_summary",
        (
            "Structured summary of **one** ingested document (facts/data points). "
            "Need source key from search / list_documents / library tools. "
            "Not full body text (use get_document_text for that)."
        ),
        {
            "collection": _COL,
            "source": {
                "type": "string",
                "description": "Document source key / filename as stored in the index.",
            },
        },
        required=["collection", "source"],
    ),
    "get_conflicts": _fn(
        "get_conflicts",
        (
            "Detected contradictions across ingested documents in a collection. "
            "Not a general search or file browser."
        ),
        {"collection": _COL},
        required=["collection"],
    ),
    "list_todos": _fn(
        "list_todos",
        (
            "List collection to-dos (checklist items, not timeline nodes). "
            "Default: open items only. include_done=true adds completed "
            "(open first). done=true/false is an exact filter and overrides "
            "include_done. mine=true keeps items assigned to People Me "
            "(error if Me is unset; unassigned is not mine).\n"
            "Omit collection to scan all libraries (Chat / MCP). "
            "Quick Chat is locked to this collection. "
            "WHEN: user asks about to-dos, due dates, or their tasks. "
            "WHEN NOT: document Q&A → search; folder layout → list_library_tree; "
            "timeline events → get_timeline."
        ),
        {
            "collection": {
                "type": "string",
                "description": (
                    "Collection **ID**. Omit to search all libraries. "
                    "Never a display name."
                ),
            },
            "include_done": {
                "type": "boolean",
                "default": False,
                "description": "Include completed to-dos (open items still first).",
            },
            "done": {
                "type": "boolean",
                "description": (
                    "Exact done filter. When set, overrides include_done."
                ),
            },
            "mine": {
                "type": "boolean",
                "default": False,
                "description": "Only to-dos assigned to People Me.",
            },
        },
    ),
    "create_todo": _fn(
        "create_todo",
        (
            "Create one or more collection to-dos in ONE call. "
            "Put every new item in todos[{title, body, ddl}]. "
            "A single title still works. Do not call create_todo once per item "
            "across multiple turns — the host runs the whole list without "
            "another model round. Do not invent tasks."
        ),
        {
            "collection": _COL,
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "ddl": {"type": "string"},
                        "assign_to_me": {"type": "boolean"},
                        "assignee_person_id": {"type": "string"},
                    },
                    "required": ["title"],
                },
                "description": "All new to-dos to create now.",
            },
            "title": {"type": "string", "description": "Single title (or use todos)."},
            "body": {"type": "string", "description": "Optional description."},
            "ddl": {
                "type": "string",
                "description": "Optional due date (ISO date or datetime).",
            },
            "assign_to_me": {
                "type": "boolean",
                "default": False,
                "description": "Assign to People Me.",
            },
            "assignee_person_id": {
                "type": "string",
                "description": "Optional People id; wins over assign_to_me.",
            },
        },
        required=["collection"],
    ),
    "update_todo": _fn(
        "update_todo",
        (
            "Update one or more to-dos in ONE call. Put every patch in "
            "updates[{todo_id, title, body, ddl, done, ...}]. "
            "A single todo_id still works. Do not call update_todo once per "
            "item across turns. Completed items cannot change content — reopen "
            "with done=false first."
        ),
        {
            "collection": _COL,
            "updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "todo_id": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "clear_body": {"type": "boolean"},
                        "ddl": {"type": "string"},
                        "clear_ddl": {"type": "boolean"},
                        "done": {"type": "boolean"},
                        "assignee_person_id": {"type": "string"},
                        "assign_to_me": {"type": "boolean"},
                        "clear_assignee": {"type": "boolean"},
                    },
                    "required": ["todo_id"],
                },
                "description": "All patches to apply now.",
            },
            "todo_id": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string", "description": "Description."},
            "clear_body": {"type": "boolean", "default": False},
            "ddl": {"type": "string", "description": "ISO due date."},
            "clear_ddl": {"type": "boolean", "default": False},
            "done": {"type": "boolean", "description": "Complete or reopen."},
            "assignee_person_id": {"type": "string"},
            "assign_to_me": {"type": "boolean", "default": False},
            "clear_assignee": {"type": "boolean", "default": False},
        },
        required=["collection"],
    ),
    "delete_todo": _fn(
        "delete_todo",
        (
            "Delete one or more to-dos. Destructive. Call ONLY when the user "
            "clearly asks to delete/remove them. Need ids from list_todos. "
            "Pass every id in todo_ids in this single call — the host confirms "
            "each item in sequence without another model round. Do not call "
            "delete_todo once per item across multiple turns."
        ),
        {
            "collection": _COL,
            "todo_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "All to-do ids to delete now. Prefer this when deleting "
                    "more than one item."
                ),
            },
            "todo_id": {
                "type": "string",
                "description": "Single id (same as todo_ids with one entry).",
            },
        },
        required=["collection"],
    ),
}

# Agent: global discovery + structure + low-priority full text + search
AGENT_STRUCTURE_NAMES: tuple[str, ...] = (
    "list_collections",
    "get_collection",
    "list_library_tree",
    "list_folders",
    "list_files",
    "get_file",
    "list_file_versions",
    "get_timeline",
    "list_chains",
    "get_chain",
    "get_node",
    "list_groups",
    "list_documents",
    "get_document_text",
    "get_file_chunks",
    "get_collection_summary",
    "get_doc_summary",
    "get_conflicts",
    "list_todos",
    "create_todo",
    "update_todo",
    "delete_todo",
)

# Quick: same without global list_collections
QUICK_STRUCTURE_NAMES: tuple[str, ...] = tuple(
    n for n in AGENT_STRUCTURE_NAMES if n != "list_collections"
)

SEARCH_TOOL_NAMES = frozenset({
    "search_knowledge_base",
    "lookup_collection",
    "lookup_meeting_transcript",
    "list_meeting_catalog",
})
STRUCTURE_TOOL_NAMES = frozenset(STRUCTURE_TOOL_SCHEMAS.keys())


def _parse_object_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def collect_todo_create_items(args: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten ``todos[]`` plus a lone title into create payloads."""
    src = args or {}
    items = _parse_object_list(src.get("todos"))
    title = str(src.get("title") or "").strip()
    if title:
        items.insert(
            0,
            {
                "title": title,
                "body": src.get("body") or "",
                "ddl": src.get("ddl") or "",
                "assign_to_me": bool(src.get("assign_to_me") or False),
                "assignee_person_id": str(src.get("assignee_person_id") or ""),
            },
        )
    return [it for it in items if str(it.get("title") or "").strip()]


def collect_todo_update_items(args: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten ``updates[]`` plus a lone todo_id patch."""
    src = args or {}
    items = _parse_object_list(src.get("updates"))
    tid = str(src.get("todo_id") or "").strip()
    if tid:
        items.insert(
            0,
            {
                "todo_id": tid,
                "title": src.get("title") or "",
                "body": src.get("body") if "body" in src else "",
                "clear_body": bool(src.get("clear_body") or False),
                "ddl": src.get("ddl") or "",
                "clear_ddl": bool(src.get("clear_ddl") or False),
                "done": src.get("done"),
                "assignee_person_id": str(src.get("assignee_person_id") or ""),
                "assign_to_me": bool(src.get("assign_to_me") or False),
                "clear_assignee": bool(src.get("clear_assignee") or False),
            },
        )
    return [it for it in items if str(it.get("todo_id") or "").strip()]


def collect_todo_delete_ids(args: dict[str, Any] | None) -> list[str]:
    """Unique todo ids from ``todo_ids`` and/or ``todo_id``, order preserved."""
    out: list[str] = []
    seen: set[str] = set()
    raw = (args or {}).get("todo_ids")
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = [p.strip() for p in text.split(",") if p.strip()]
        else:
            raw = [p.strip() for p in text.split(",") if p.strip()]
    if isinstance(raw, list):
        for item in raw:
            sid = str(item or "").strip()
            if sid and sid not in seen:
                seen.add(sid)
                out.append(sid)
    one = str((args or {}).get("todo_id") or "").strip()
    if one and one not in seen:
        out.append(one)
    return out


def _maybe_web_tool(*, web_search_enabled: bool) -> list[dict[str, Any]]:
    """Always expose web tool when Tavily key exists (status=enabled|disabled in schema).

    When the UI Web toggle is off, the tool is still listed as disabled so the
    agent does not invent reasons; calling it returns status=disabled.
    """
    try:
        from src.chatbox.web_search import has_web_search_api_key

        if has_web_search_api_key():
            return [build_request_web_search_tool(web_search_enabled=web_search_enabled)]
        # No key: still expose as disabled so the model sees the capability gap
        return [build_request_web_search_tool(web_search_enabled=False)]
    except Exception:
        return [build_request_web_search_tool(web_search_enabled=False)]


def tools_for_mode(
    mode: str,
    *,
    is_meeting: bool = False,
    is_group: bool = False,
    web_search_enabled: bool = False,
    has_meetings: bool = True,
) -> list[dict[str, Any]]:
    """Return OpenAI tools array for agentic / direct / meeting / group.

    ``has_meetings=False`` drops the meeting trio from Chat / Quick Chat:
    when the scope has no ingested meetings the model cannot call them.
    """
    if is_group:
        return [
            LIST_MEETING_CATALOG_TOOL,
            LOOKUP_SCOPED_TRANSCRIPT_TOOL,
            READ_MEETING_SUMMARY_TOOL,
        ]
    if is_meeting:
        return [LOOKUP_MEETING_TRANSCRIPT_TOOL]
    meeting_tools = (
        [
            LIST_MEETING_CATALOG_TOOL,
            LOOKUP_SCOPED_TRANSCRIPT_TOOL,
            READ_MEETING_SUMMARY_TOOL,
        ]
        if has_meetings
        else []
    )
    if mode == "direct":
        tools = [LOOKUP_COLLECTION_TOOL, *meeting_tools]
        for name in QUICK_STRUCTURE_NAMES:
            tools.append(STRUCTURE_TOOL_SCHEMAS[name])
        tools.extend(_maybe_web_tool(web_search_enabled=web_search_enabled))
        return tools
    # agentic (default)
    tools = [SEARCH_KNOWLEDGE_BASE_TOOL, *meeting_tools]
    for name in AGENT_STRUCTURE_NAMES:
        tools.append(STRUCTURE_TOOL_SCHEMAS[name])
    tools.extend(_maybe_web_tool(web_search_enabled=web_search_enabled))
    return tools


def allowed_tool_names(
    mode: str,
    *,
    is_meeting: bool = False,
    is_group: bool = False,
    web_search_enabled: bool = False,
    has_meetings: bool = True,
) -> frozenset[str]:
    if is_group:
        return frozenset(
            {
                "list_meeting_catalog",
                "lookup_meeting_transcript",
                "read_meeting_summary",
            }
        )
    if is_meeting:
        return frozenset({"lookup_meeting_transcript"})
    names: set[str]
    if mode == "direct":
        names = {
            "lookup_collection",
            *QUICK_STRUCTURE_NAMES,
        }
    else:
        names = {
            "search_knowledge_base",
            *AGENT_STRUCTURE_NAMES,
        }
    if has_meetings:
        names |= {
            "list_meeting_catalog",
            "lookup_meeting_transcript",
            "read_meeting_summary",
        }
    # Always allow the name so execution can return status=disabled / user_declined
    # (toggle state is enforced inside the tool handler, not by omitting the tool).
    names.add(WEB_SEARCH_TOOL_NAME)
    return frozenset(names)

def force_collection_args(
    tool_name: str,
    args: dict[str, Any],
    *,
    mode: str,
    forced_collection: str | None,
) -> tuple[dict[str, Any], str | None]:
    """Apply Quick Chat collection lock. Returns (args, error_or_none)."""
    out = dict(args or {})
    if mode != "direct":
        return out, None
    if tool_name in (
        "lookup_collection",
        "list_meeting_catalog",
        "lookup_meeting_transcript",
        "read_meeting_summary",
    ):
        if not forced_collection:
            return out, "No collection is bound to this Quick Chat session."
        if tool_name != "lookup_collection":
            out["collection"] = forced_collection
        return out, None
    if tool_name not in STRUCTURE_TOOL_NAMES:
        return out, None
    if tool_name == "list_collections":
        return out, "list_collections is not available in Quick Chat."
    if not forced_collection:
        return out, "No collection is bound to this Quick Chat session."
    # Always overwrite — do not trust model-supplied collection
    out["collection"] = forced_collection
    return out, None


def merge_search_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Merge multiple search_knowledge_base calls; leave other tools intact.

    Preserves relative order: non-search tools stay; merged search is placed
    at the position of the first search call.
    """
    if len(tool_calls) <= 1:
        return tool_calls

    search_indices = [
        i
        for i, tc in enumerate(tool_calls)
        if tc.get("function", {}).get("name") == "search_knowledge_base"
    ]
    if len(search_indices) <= 1:
        return tool_calls

    queries: list[str] = []
    first = tool_calls[search_indices[0]]
    for i in search_indices:
        try:
            a = json.loads(tool_calls[i]["function"].get("arguments") or "{}")
            q = a.get("raw_query", "")
            if q:
                queries.append(q)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    if not queries:
        return tool_calls

    merged = {
        "id": first.get("id", "call_1"),
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "arguments": json.dumps(
                {"raw_query": ", ".join(queries), "decompose": True},
                ensure_ascii=False,
            ),
        },
    }
    logger.info(
        "Merged %d search_knowledge_base calls → 1 decompose=true: %r",
        len(search_indices),
        (", ".join(queries))[:200],
    )

    search_set = set(search_indices)
    result: list[dict] = []
    inserted = False
    for i, tc in enumerate(tool_calls):
        if i in search_set:
            if not inserted:
                result.append(merged)
                inserted = True
            continue
        result.append(tc)
    return result


def _truncate(text: str) -> str:
    if len(text) <= _MAX_RESULT_CHARS:
        return text
    return (
        text[:_MAX_RESULT_CHARS]
        + f"\n\n…[truncated, {len(text)} chars total; narrow with offset/limit or filters]"
    )


def _result_to_str(result: Any) -> str:
    if result is None:
        return json.dumps({"error": "empty tool result"})
    if isinstance(result, str):
        return _truncate(result)
    # MCP CallToolResult-like
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured is not None:
        return _truncate(json.dumps(structured, ensure_ascii=False, default=str))
    content = getattr(result, "content", None)
    if content is not None:
        parts: list[str] = []
        for c in content:
            t = getattr(c, "text", None)
            if t:
                parts.append(t)
            elif isinstance(c, dict) and c.get("text"):
                parts.append(str(c["text"]))
        if parts:
            return _truncate("\n".join(parts))
    if isinstance(result, dict):
        return _truncate(json.dumps(result, ensure_ascii=False, default=str))
    return _truncate(str(result))


def _run_coro(coro) -> Any:
    """Run async tool from sync or already-running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    def _in_thread():
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_in_thread).result(timeout=180)


async def _dispatch_structure_async(name: str, args: dict[str, Any]) -> Any:
    """Call the same MCP tool callables (in-process, no HTTP)."""
    col = (args.get("collection") or "").strip()

    if name == "list_collections":
        from src.mcp.tools.collections import list_collections

        return await list_collections()

    if name == "get_collection":
        from src.mcp.tools.collections import get_collection

        return await get_collection(col)

    if name == "list_library_tree":
        from src.mcp.tools.file_mgmt import list_library_tree

        return await list_library_tree(
            col,
            max_depth=int(args.get("max_depth") or 0),
            fields=str(args.get("fields") or "summary"),
        )

    if name == "list_folders":
        from src.mcp.tools.file_mgmt import list_folders

        return await list_folders(col)

    if name == "list_files":
        from src.mcp.tools.file_mgmt import list_files

        return await list_files(
            col,
            folder_id=str(args.get("folder_id") or ""),
            scope=str(args.get("scope") or "all"),
        )

    if name == "get_file":
        from src.mcp.tools.file_mgmt import get_file

        return await get_file(col, str(args.get("file_id") or ""))

    if name == "list_file_versions":
        from src.mcp.tools.file_mgmt import list_file_versions

        return await list_file_versions(col, str(args.get("file_id") or ""))

    if name == "get_timeline":
        from src.mcp.tools.file_mgmt import get_timeline

        return await get_timeline(col)

    if name == "list_chains":
        from src.mcp.tools.file_mgmt import list_chains

        return await list_chains(col)

    if name == "get_chain":
        from src.mcp.tools.file_mgmt import get_chain

        return await get_chain(col, str(args.get("chain_id") or ""))

    if name == "get_node":
        from src.mcp.tools.file_mgmt import get_node

        return await get_node(col, str(args.get("node_id") or ""))

    if name == "list_groups":
        from src.mcp.tools.file_mgmt import list_groups

        return await list_groups(col)

    if name == "list_documents":
        from src.mcp.tools.documents import list_documents

        return await list_documents(col)

    if name == "get_document_text":
        from src.mcp.tools.documents import get_document_text

        # Chat path: no unlimited dump — default 32k, hard max 96k per call
        off = int(args.get("offset") or 0)
        if args.get("limit") is None:
            lim = _CHAT_DOC_DEFAULT_LIMIT
        else:
            lim = int(args.get("limit"))
        if lim <= 0:
            lim = _CHAT_DOC_DEFAULT_LIMIT
        lim = min(lim, _CHAT_DOC_MAX_LIMIT)

        return await get_document_text(
            col,
            file_id=str(args.get("file_id") or ""),
            source=str(args.get("source") or ""),
            version_id=str(args.get("version_id") or ""),
            offset=max(0, off),
            limit=lim,
        )

    if name == "get_file_chunks":
        from src.mcp.tools.documents import get_file_chunks

        return await get_file_chunks(
            col,
            file_id=str(args.get("file_id") or ""),
            source=str(args.get("source") or ""),
            offset=int(args.get("offset") or 0),
            limit=int(args.get("limit") if args.get("limit") is not None else 50),
        )

    if name == "get_collection_summary":
        from src.mcp.tools.summaries import get_collection_summary

        return await get_collection_summary(col)

    if name == "get_doc_summary":
        from src.mcp.tools.summaries import get_doc_summary

        return await get_doc_summary(col, str(args.get("source") or ""))

    if name == "get_conflicts":
        from src.mcp.tools.summaries import get_conflicts

        return await get_conflicts(col)

    if name == "list_todos":
        from src.mcp.tools.file_mgmt import list_todos

        done = args.get("done")
        if done is not None:
            done = bool(done)
        return await list_todos(
            col,
            include_done=bool(args.get("include_done") or False),
            done=done,
            mine=bool(args.get("mine") or False),
        )

    if name == "create_todo":
        from src.mcp.tools.file_mgmt import create_todo

        items = collect_todo_create_items(args)
        return await create_todo(col, todos=items)

    if name == "update_todo":
        from src.mcp.tools.file_mgmt import update_todo

        items = collect_todo_update_items(args)
        return await update_todo(col, updates=items)

    if name == "delete_todo":
        from src.mcp.tools.file_mgmt import delete_todo

        return await delete_todo(
            col,
            todo_id=str(args.get("todo_id") or ""),
            todo_ids=collect_todo_delete_ids(args) or None,
        )

    return json.dumps({"error": f"Unknown structure tool: {name}"})


async def execute_structure_tool_async(
    name: str,
    args: dict[str, Any],
    *,
    mode: str,
    forced_collection: str | None = None,
) -> str:
    """Execute a structure/summary/full-text tool; return text for the tool message."""
    if name not in STRUCTURE_TOOL_NAMES:
        return json.dumps({"error": f"Tool not allowed: {name}"})

    scoped, err = force_collection_args(
        name, args, mode=mode, forced_collection=forced_collection
    )
    if err:
        return json.dumps({"error": err})

    try:
        raw = await _dispatch_structure_async(name, scoped)
        return _result_to_str(raw)
    except Exception as exc:
        logger.exception("Structure tool %s failed", name)
        return json.dumps({"error": str(exc), "tool": name})


def execute_structure_tool(
    name: str,
    args: dict[str, Any],
    *,
    mode: str,
    forced_collection: str | None = None,
) -> str:
    """Sync wrapper for non-async chat paths."""
    return _run_coro(
        execute_structure_tool_async(
            name, args, mode=mode, forced_collection=forced_collection
        )
    )


# Back-compat aliases used by agent module
TOOLS = [SEARCH_KNOWLEDGE_BASE_TOOL]
LOOKUP_TOOL = [LOOKUP_COLLECTION_TOOL]
