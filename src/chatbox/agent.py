"""ChatboxAgent — conversational agent with knowledge-base + structure tools.

Uses function calling to decide whether to search, browse the library tree,
or (rarely) read full document text. Search internals stay hidden from the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue as sync_queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncGenerator

from src.chatbox.query_tools import (
    STRUCTURE_TOOL_NAMES,
    TOOLS,
    WEB_SEARCH_TOOL_NAME,
    allowed_tool_names,
    execute_structure_tool,
    execute_structure_tool_async,
    force_collection_args,
    merge_search_tool_calls,
    tools_for_mode,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Default system prompt
# ═══════════════════════════════════════════════════════════════════

DEFAULT_SYSTEM_PROMPT = """You are a knowledge base assistant for ingested documents.

TOOLS:
- search_knowledge_base — primary tool for factual Q&A over the private knowledge base.
- Structure tools (list_collections, list_library_tree, list_files, get_file,
  get_timeline, list_file_versions, …) — browse what exists in the library.
- get_document_text / get_file_chunks — LOW PRIORITY full-body / index inspection.
- get_collection_summary / get_doc_summary / get_conflicts — ingested summaries.
- request_web_search — optional internet search when public/current info is needed.
  If web_toggle=enabled, CALL it immediately — do not ask the user whether Web is on.
  If web_toggle=disabled, say the library lacks data and Web is off (briefly).

YOUR ROLE — Information Planner:
Translate the user's question into concrete information needs before calling tools.

DECISION RULES:
- Check the knowledge base reference first. If the topic is not covered by any
  collection, say so and list what IS available — do not search blindly.
- DEFAULT for content facts: ONE search_knowledge_base call with decompose=true.
- Use structure tools when the user asks what files/folders/versions exist, or
  where something lives in the library — not as a substitute for search.
- get_document_text / get_file_chunks: ONLY when the user explicitly asks to read
  a named file / full text / a version, OR you judge that search chunks are
  insufficient and continuous original text is required. Never call them "just
  in case". Prefer search first. When reading text: use a character window
  (default ~32k, hard max ~96k). If search already gave char_offset, start
  get_document_text there. If has_more and the window is still not enough to
  answer, page forward with offset=next_offset (like turning pages) until you
  have sufficient evidence; stop when the answer is complete (do not read
  whole files by default).
- Web search: when public/current internet info is needed or KB lacks it, and
  web_toggle=enabled — CALL request_web_search immediately (do not ask the user
  about the Web toggle). Prefer KB first. Separate WEB vs KB claims with labels.
- EXCEPTION — dependency chain: multiple rounds only when round N+1 cannot be
  formulated without round N results.
- For comparison and analysis: YOU write the final answer; tools supply evidence.

WRITING raw_query:
- NEVER pass the user's question verbatim — write WHAT to search for.
- Expand abbreviations and add conversation context.
- Base answers on tool results with source citations.

Formatting:
- When using markdown tables, ALWAYS put each row on its own line with proper newlines.
  Each row MUST be separated by a line break. The separator line MUST have its own line:
  | Header A | Header B |
  |----------|----------|
  | Cell 1   | Cell 2   |
- Use standard alignment: :--- (left), :---: (center), ---: (right). Never use ::--
- Keep tables simple. Prefer lists over tables when comparing only 2-3 items."""

QUICK_CHAT_SYSTEM_PROMPT = """You are a quick Q&A assistant for the document collection "%(collection_name)s".

All collection tools are locked to THIS collection only. You cannot query other collections.

TOOLS:
- lookup_collection — primary search over this collection's ingested chunks.
- Structure tools (list_library_tree, list_files, get_file, get_timeline, …) —
  browse files/folders/versions in this collection.
- get_document_text / get_file_chunks — LOW PRIORITY; only when the user asks to
  read a named file/full text, or chunks are clearly insufficient. Prefer
  search first. get_document_text uses character windows (~32k default;
  has_more/next_offset). If the current page lacks enough evidence, continue
  with offset=next_offset or a chunk's char_offset — stop when the answer is
  complete (do not page entire files by default).
- get_collection_summary / get_doc_summary / get_conflicts — ingested summaries.
- request_web_search — internet search when public/current info is needed and
  web_toggle=enabled. Prefer this collection first. Label WEB results clearly.

YOUR ROLE:
- Answer questions about this collection concisely and accurately.
- Prefer lookup_collection for factual content questions.
- Use structure tools for "what files exist / where is X" questions.
- For chitchat and common knowledge, answer directly without tools.
- If the collection lacks the answer and web_toggle=enabled: CALL request_web_search
  immediately. Do NOT ask the user to check the Web toggle or send another message.
  The system handles Allow/Decline UI after you call the tool.
- If web_toggle=disabled and the collection lacks data: briefly say Web is off and
  answer what you can from the collection. Do not invent internet facts.

RULES:
- Base factual answers on tool results — do NOT fabricate.
- Cite specific data points when present.
- If the collection lacks relevant information, say so clearly.
- Keep answers focused — quick Q&A, not deep multi-collection research.

Formatting:
- Use Markdown for readability (headers, lists, bold/italic).
- When using markdown tables, put each row on its own line with proper newlines.
- Keep tables simple. Prefer lists over tables when comparing only 2-3 items."""

# ═══════════════════════════════════════════════════════════════════
# Response types
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ChatResponse:
    answer: str
    sources: list[dict] = field(default_factory=list)
    tool_calls: int = 0


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _build_speaker_mapping(meeting_id: str) -> str:
    """Build speaker mapping string for meeting chat.

    Reads speaker_names from the meeting store and formats as:
        Current speaker mapping:
        - speaker1: John
        - speaker2: Sarah

    Injected as ephemeral context before each user message.
    """
    try:
        from src.meeting.store import get_meeting
        meeting = get_meeting(meeting_id)
    except Exception:
        return "No speaker names configured for this meeting."
    if meeting is None or meeting.speaker_names is None:
        return "No speaker names configured for this meeting."
    lines = ["Current speaker mapping:"]
    for spk_id, name in meeting.speaker_names.items():
        display_name = name if name else "(unnamed)"
        lines.append(f"- {spk_id}: {display_name}")
    if not meeting.speaker_names:
        return "No speaker names configured for this meeting."
    return "\n".join(lines)


def _format_current_time() -> str:
    """Return current local time with timezone, e.g. '2026-07-03 14:30 CST (UTC+08:00)'."""
    now = datetime.now().astimezone()
    offset = now.utcoffset()
    if offset is not None:
        total_seconds = int(offset.total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        hours, minutes = divmod(abs(total_seconds), 3600)
        minutes //= 60
        tz_str = f"UTC{sign}{hours:02d}:{minutes:02d}"
    else:
        tz_str = "UTC"
    tz_name = now.strftime("%Z") or tz_str
    return f"{now.strftime('%Y-%m-%d %H:%M')} {tz_name} ({tz_str})"


# ═══════════════════════════════════════════════════════════════════
# ChatboxAgent
# ═══════════════════════════════════════════════════════════════════

# Outer LLM↔tool loop (web + structure + KB + synthesis). Higher than the
# agentic-search-only cap so HITL web / multi-tool turns still fit.
_MAX_TOOL_ROUNDS = 15
# Cap *search_knowledge_base* (Agentic 查库) per user message — separate from
# outer rounds so web/structure tools do not burn the KB budget, and so the
# model is pushed to answer after enough evidence instead of endless search.
_MAX_AGENTIC_SEARCH_CALLS = 5
# Legacy row cap (tests / callers may still reference). Context window uses dialogue turns.
_MAX_HISTORY_MESSAGES = 50
# LLM history: count user + final-assistant answers (tool rows ride with the turn).
_MAX_HISTORY_DIALOGUE_MAIN = 32   # ~16 Q&A rounds for agentic Chat
_MAX_HISTORY_DIALOGUE_QUICK = 20  # aligns with quick warn/trim scale
_MAX_HISTORY_DIALOGUE_MEETING = 40  # no tools; more room for pure dialogue
_QUICK_MAX_MESSAGES = 30
_QUICK_WARN_THRESHOLD = 20
_QUICK_TRIM_KEEP = 5
_MEETING_MAX_MESSAGES = 50
_MEETING_TRIM_KEEP = 10
_TOTAL_MAX_TOKENS = 128000  # generous ceiling
# Cap historical tool payloads so old web dumps do not drown newer user intent
_MAX_HISTORY_TOOL_CHARS = 6000


class ChatboxAgent:
    """Chat agent with conversation memory and RAG tool access.

    Uses function calling to decide when to search the knowledge base.
    Supports two modes:
      - "agentic" (default): search_knowledge_base tool → AgenticQueryService
      - "direct": lookup_collection tool → DirectQueryModule (lightweight,
        collection-scoped Q&A; no decompose/fan-out/synthesize)
    """

    def __init__(
        self,
        session_store,
        chat_llm,
        agentic_service,
        direct_module=None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ):
        self._store = session_store
        self._llm = chat_llm
        self._agentic = agentic_service
        self._direct = direct_module
        self._system_prompt = system_prompt

    # ── helpers ──────────────────────────────────────────────────────

    def _resolve_tools_and_prompt(
        self,
        mode: str,
        session_id: str,
        collections: list[str] | None = None,
        *,
        web_search_enabled: bool = False,
    ):
        """Return (tools, system_prompt, catalog_text) for the given mode."""
        is_meeting = session_id.startswith("meeting_")
        if mode == "direct":
            if is_meeting:
                from src.prompts import MEETING_CHAT_SYSTEM_PROMPT
                return [], MEETING_CHAT_SYSTEM_PROMPT, ""
            tools = tools_for_mode(
                "direct", is_meeting=False, web_search_enabled=web_search_enabled,
            )
            cols = collections or self._get_collections(session_id)
            col_name = cols[0] if cols else "this collection"
            system_prompt = QUICK_CHAT_SYSTEM_PROMPT % {"collection_name": col_name}
            return tools, system_prompt, ""
        catalog_text = self._build_catalog_text(session_id, collections=collections)
        tools = tools_for_mode(
            "agentic", is_meeting=False, web_search_enabled=web_search_enabled,
        )
        return tools, self._system_prompt, catalog_text

    def _forced_collection(self, mode: str, collections: list[str] | None) -> str | None:
        if mode != "direct":
            return None
        if collections:
            return collections[0]
        return None

    def _is_allowed_tool(
        self,
        tool_name: str,
        mode: str,
        session_id: str,
        *,
        web_search_enabled: bool = False,
    ) -> bool:
        is_meeting = session_id.startswith("meeting_")
        return tool_name in allowed_tool_names(
            mode, is_meeting=is_meeting, web_search_enabled=web_search_enabled,
        )

    def _check_session_truncation(self, session_id: str) -> int | None:
        """Check and enforce session **turn** limits (1 user + 1 reply = 1 turn).

        Counts **user messages only**. Tool rows and assistant replies do not
        increase the turn counter.

        For quick_ sessions: trim at _QUICK_MAX_MESSAGES turns, keep _QUICK_TRIM_KEEP.
        For meeting_ sessions: trim at _MEETING_MAX_MESSAGES turns, keep _MEETING_TRIM_KEEP.

        Returns the current turn count BEFORE any truncation, or None if not a
        quick/meeting session.
        """
        if session_id.startswith("quick_"):
            max_turns, trim_keep = _QUICK_MAX_MESSAGES, _QUICK_TRIM_KEEP
        elif session_id.startswith("meeting_"):
            max_turns, trim_keep = _MEETING_MAX_MESSAGES, _MEETING_TRIM_KEEP
        else:
            return None
        count_fn = getattr(self._store, "count_dialogue_turns", None)
        if callable(count_fn):
            count = count_fn(session_id)
        else:
            # Fallback: approximate turns as half of user+assistant dialogue rows
            dlg = getattr(self._store, "count_dialogue_messages", None)
            raw = dlg(session_id) if callable(dlg) else self._store.count_messages(
                session_id, exclude_system=True,
            )
            count = (raw + 1) // 2
        if count >= max_turns:
            logger.info(
                "Session %s hit %d dialogue turns, trimming to %d",
                session_id, count, trim_keep,
            )
            trim_fn = getattr(self._store, "trim_to_dialogue_turns", None)
            if callable(trim_fn):
                trim_fn(session_id, trim_keep)
            else:
                trim_old = getattr(self._store, "trim_to_dialogue_messages", None)
                if callable(trim_old):
                    trim_old(session_id, trim_keep)
                else:
                    self._store.trim_messages(session_id, trim_keep)
            return trim_keep
        return count

    def _build_catalog_text(self, session_id: str, *, collections: list[str] | None = None) -> str:
        """Build a concise catalog summary for the system prompt."""
        if self._agentic is None:
            return ""
        try:
            catalog = self._agentic.catalog
            if catalog is None:
                return ""
            cols = collections if collections is not None else self._get_collections(session_id)
            entries = catalog.get_catalog(cols if cols else None)
        except Exception:
            return ""
        if not entries:
            return ""
        lines = ["Knowledge base reference:"]
        for e in entries:
            if isinstance(e, dict):
                name = e.get("name", "")
                defn = e.get("definition", "")
                tags = e.get("tags", [])
            else:
                name = getattr(e, "name", "")
                defn = getattr(e, "definition", "")
                tags = getattr(e, "tags", [])
            parts = [f"- {name}"]
            if tags:
                parts.append(f" [{', '.join(tags)}]")
            if defn:
                parts.append(f"\n  {defn}")
            lines.append("".join(parts))
        return "\n".join(lines)

    def _get_collections(self, session_id: str) -> list[str]:
        session = self._store.get_session(session_id)
        if session and session.collections:
            return session.collections
        return []

    def _history_dialogue_budget(self, session_id: str) -> int:
        """Max user+final-assistant dialogue units for LLM history by session type."""
        if session_id.startswith("meeting_"):
            return _MAX_HISTORY_DIALOGUE_MEETING
        if session_id.startswith("quick_"):
            return _MAX_HISTORY_DIALOGUE_QUICK
        return _MAX_HISTORY_DIALOGUE_MAIN

    def _store_message_to_llm_dict(self, m) -> dict:
        """Convert a persisted Message into an OpenAI-style dict for the LLM."""
        meta = m.metadata or {}
        content = m.content or ""
        msg: dict = {"role": m.role, "content": content}

        if m.role == "assistant":
            tool_calls = meta.get("tool_calls")
            if isinstance(tool_calls, list):
                msg["tool_calls"] = tool_calls
                msg["content"] = None
            rc = meta.get("reasoning_content")
            if rc:
                msg["reasoning_content"] = rc
        elif m.role == "tool":
            msg["tool_call_id"] = meta.get("tool_call_id", "")
            # Truncate huge historical tool bodies (e.g. prior WEB dumps).
            if len(content) > _MAX_HISTORY_TOOL_CHARS:
                content = (
                    content[:_MAX_HISTORY_TOOL_CHARS]
                    + "\n…[truncated historical tool result]"
                )
            msg["content"] = content
        return msg

    def _build_messages(
        self,
        session_id: str,
        user_message: str,
        *,
        extra_messages: list[dict] | None = None,
        collections: list[str] | None = None,
        system_prompt: str | None = None,
        catalog_text: str | None = None,
        pre_message_context: str | None = None,
    ) -> list[dict]:
        """Build OpenAI-compatible messages for the LLM (three layouts).

        **Meeting** (``meeting_*``)::

            [0] fixed system prompt
                live transcript (ephemeral — from meeting store each turn;
                    NOT persisted in the session)
                recent dialogue history only (DB system rows ignored)
                speaker mapping (ephemeral — NOT persisted / not history)
                current user (+ time if re-appended)

        **Quick** (``quick_*``)::

            [0] fixed system prompt
                recent dialogue + tool trajectory
                extra_messages (this-turn FC)
                current user

        **Main Chat** (agentic)::

            [0] fixed system prompt
                recent dialogue + tool trajectory
                catalog (ephemeral — NOT persisted / not history; after history)
                extra_messages (this-turn FC)
                current user

        History is windowed by **dialogue turns** (user + final assistant) via
        ``get_messages(limit=N)`` / ``get_context_messages``. Meeting transcript,
        catalog, and speaker mapping are injected only here and never written
        to the session store. (Legacy DB system rows on meeting sessions are
        ignored so stale appends cannot pollute context.)
        """
        is_meeting = session_id.startswith("meeting_")
        is_quick = session_id.startswith("quick_")

        messages: list[dict] = []

        # ── [0] Static system prompt (always first — cache prefix) ──
        sp = system_prompt if system_prompt is not None else self._system_prompt
        messages.append({"role": "system", "content": sp})

        # ── Meeting: live transcript (or explicit unavailable) after fixed system ──
        # Always inject one message so the model never assumes a hidden transcript.
        if is_meeting:
            meeting_id = session_id[len("meeting_"):]
            try:
                from src.chatbox.meeting_context import meeting_transcript_context_message
                live_tx = meeting_transcript_context_message(meeting_id)
            except Exception:
                logger.exception(
                    "Failed to load live transcript for session %s", session_id
                )
                from src.chatbox.meeting_context import MEETING_TRANSCRIPT_UNAVAILABLE
                live_tx = MEETING_TRANSCRIPT_UNAVAILABLE
            messages.append({"role": "system", "content": live_tx})

        # ── History: last N dialogue turns ──
        # Declined web searches stay in history so the model knows what was refused.
        # Catalog / speaker mapping / meeting transcript are NOT part of hist.
        max_dialogue = self._history_dialogue_budget(session_id)
        get_ctx = getattr(self._store, "get_context_messages", None)
        if callable(get_ctx):
            hist = get_ctx(session_id, max_dialogue=max_dialogue)
        else:
            hist = self._store.get_messages(session_id, limit=max_dialogue)

        # Meeting: ignore any legacy system rows (old transcript appends).
        # Non-meeting: keep system_hist for rare stored system context.
        system_hist = [] if is_meeting else [m for m in hist if m.role == "system"]
        dialogue_hist = [m for m in hist if m.role != "system"]

        for m in system_hist:
            messages.append(self._store_message_to_llm_dict(m))

        for m in dialogue_hist:
            messages.append(self._store_message_to_llm_dict(m))

        # ── Catalog (main Chat only; ephemeral; after history; never DB) ──
        if not is_meeting and not is_quick:
            ct = (
                catalog_text
                if catalog_text is not None
                else self._build_catalog_text(session_id, collections=collections)
            )
            if ct:
                messages.append({"role": "system", "content": ct})

        # ── This-turn tool trajectory (FC loop; persisted separately after turn) ──
        if extra_messages:
            messages.extend(extra_messages)

        # ── Speaker mapping etc. (ephemeral; never persisted as history) ──
        if pre_message_context:
            messages.append({"role": "system", "content": pre_message_context})

        # ── Current user message ──
        # Saved before the LLM call; usually already last in hist. Re-append with
        # clock if missing (e.g. empty user_message rebuild) or hist truncated oddly.
        last = dialogue_hist[-1] if dialogue_hist else None
        if (
            last is None
            or last.role != "user"
            or (last.content or "") != user_message
        ):
            if user_message:
                timestamped = f"[Current time: {_format_current_time()}]\n\n{user_message}"
                messages.append({"role": "user", "content": timestamped})

        return messages

    # ── non-streaming chat ───────────────────────────────────────────

    def chat(
        self,
        session_id: str,
        user_message: str,
        *,
        mode: str = "agentic",
        web_search_enabled: bool = False,
    ) -> ChatResponse:
        """Non-streaming chat. Returns final answer with sources."""
        if not user_message or not user_message.strip():
            return ChatResponse(answer="")

        _tools, _sys_prompt, _cat_text = self._resolve_tools_and_prompt(
            mode, session_id, web_search_enabled=web_search_enabled,
        )
        self._check_session_truncation(session_id)

        collections = self._get_collections(session_id)
        # Speaker mapping for meeting sessions (ephemeral, per-message)
        meeting_speaker_mapping = None
        if session_id.startswith("meeting_"):
            meeting_id = session_id[len("meeting_"):]
            meeting_speaker_mapping = _build_speaker_mapping(meeting_id)
        total_tool_calls = 0
        agentic_search_calls = 0  # search_knowledge_base only

        # Save user message
        self._store.add_message(session_id, "user", user_message)

        extra_messages: list[dict] = []
        final_answer = ""
        all_sources: list[dict] = []

        for _round in range(_MAX_TOOL_ROUNDS):
            messages = self._build_messages(
                session_id, user_message, extra_messages=extra_messages,
                collections=collections,
                system_prompt=_sys_prompt, catalog_text=_cat_text,
                pre_message_context=meeting_speaker_mapping,
            )

            # CHECKPOINT: log multimodal messages
            for i, msg in enumerate(messages):
                ct = msg.get("content")
                if isinstance(ct, list):
                    logger.info(
                        "[Chatbox] MSG[%d] role=%s MULTIMODAL parts=%d imgs=%d",
                        i, msg["role"], len(ct),
                        sum(1 for p in ct if p.get("type") == "image_url"),
                    )

            # Call LLM with tools — use underlying OpenAI client directly
            # (avoids modifying the LLMProvider ABC)
            response = self._call_llm_with_tools(messages, tools=_tools)

            if response.get("tool_calls"):
                # ── LLM wants to use tools ──
                tcs = response["tool_calls"]
                if mode != "direct" and len(tcs) > 1:
                    tcs = merge_search_tool_calls(tcs)
                forced_col = self._forced_collection(mode, collections)

                for tc in tcs:
                    tool_name = tc["function"]["name"]
                    if not self._is_allowed_tool(
                        tool_name, mode, session_id, web_search_enabled=web_search_enabled,
                    ):
                        logger.warning("Unknown or disallowed tool: %s", tool_name)
                        continue

                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {"raw_query": user_message, "generate_answer": True} if mode != "direct" else {"query": user_message}

                    is_multimodal = False
                    tool_content: str | list

                    if tool_name == WEB_SEARCH_TOOL_NAME:
                        from src.chatbox.web_search import WEB_BANNER, has_web_search_api_key
                        wq = str(args.get("query") or user_message).strip()
                        if not has_web_search_api_key():
                            tool_content = "Tavily API key is missing. Add it under Settings → Web Search (Tavily)."
                        else:
                            tool_content = (
                                f"{WEB_BANNER}\n\n"
                                "Web search requires the streaming Chat UI for user "
                                f"confirmation (query was: {wq!r}). No internet search was run."
                            )
                        total_tool_calls += 1
                    elif tool_name in STRUCTURE_TOOL_NAMES:
                        tool_content = execute_structure_tool(
                            tool_name, args, mode=mode, forced_collection=forced_col,
                        )
                        total_tool_calls += 1
                    else:
                        # Search facades
                        if mode == "direct":
                            raw_query = args.get("query", user_message)
                        else:
                            raw_query = args.get("raw_query", user_message)
                        generate_answer = args.get("generate_answer", False) if mode != "direct" else False
                        include_images = args.get("include_images", False)

                        if not include_images:
                            model = getattr(self._llm, "_model", "")
                            from src.config import get_config as _get_cfg
                            _cfg = _get_cfg()
                            for _p in _cfg.llm.providers:
                                if hasattr(_p, "visual_model_ids") and model in _p.visual_model_ids:
                                    include_images = True
                                    break

                        decompose = args.get("decompose", True) if mode != "direct" else False

                        if mode == "direct":
                            if self._direct is None:
                                tool_content = "Direct retrieval is not configured."
                            else:
                                query = args.get("query", raw_query)
                                direct_cols = [forced_col] if forced_col else (collections or [])
                                direct_result = self._direct.retrieve(
                                    query,
                                    collections=direct_cols,
                                    top_k=10,
                                    generate_answer=False,
                                )
                                total_tool_calls += 1
                                for chunk in direct_result.chunks:
                                    source = {
                                        "text": getattr(chunk, "text", "")[:200],
                                        "score": getattr(chunk, "score", 0.0),
                                        "metadata": getattr(chunk, "metadata", {}),
                                    }
                                    if source not in all_sources:
                                        all_sources.append(source)

                                if include_images and direct_result.chunks:
                                    from src.rag.agentic_query import (
                                        _stitch_images_from_chunks,
                                        _build_multimodal_context,
                                    )
                                    images_payload = _stitch_images_from_chunks(direct_result.chunks)
                                    if images_payload:
                                        is_multimodal = True
                                        tool_content = _build_multimodal_context(
                                            direct_result.context, images_payload,
                                        )
                                    else:
                                        tool_content = direct_result.context if direct_result.context else "No relevant information found."
                                else:
                                    tool_content = direct_result.context if direct_result.context else "No relevant information found."
                        elif self._agentic is None:
                            logger.warning("Tool call requested but agentic_service is None")
                            tool_content = "Knowledge base search is not configured. Please enable Function Calling on an LLM model in Settings."
                        elif (
                            tool_name == "search_knowledge_base"
                            and agentic_search_calls >= _MAX_AGENTIC_SEARCH_CALLS
                        ):
                            tool_content = (
                                f"Agentic knowledge-base search limit reached "
                                f"({_MAX_AGENTIC_SEARCH_CALLS} calls this turn). "
                                "Do NOT call search_knowledge_base again. "
                                "Synthesize the final answer now from tool results "
                                "already in this conversation."
                            )
                            total_tool_calls += 1
                        else:
                            result = self._agentic.run(
                                raw_query,
                                collections=collections or None,
                                generate_answer=generate_answer,
                                include_images=include_images,
                                decompose=decompose,
                            )
                            total_tool_calls += 1
                            if tool_name == "search_knowledge_base":
                                agentic_search_calls += 1
                            is_multimodal = isinstance(result.answer, list) if result else False
                            if is_multimodal:
                                tool_content = result.answer
                            else:
                                tool_content_parts = []
                                if result.answer:
                                    tool_content_parts.append(result.answer)
                                elif result.context:
                                    tool_content_parts.append(result.context)
                                tool_content = "\n\n".join(tool_content_parts) if tool_content_parts else "No relevant information found."
                            for chunk in result.all_chunks:
                                source = {
                                    "text": getattr(chunk, "text", "")[:200],
                                    "score": getattr(chunk, "score", 0.0),
                                    "metadata": getattr(chunk, "metadata", {}),
                                }
                                if source not in all_sources:
                                    all_sources.append(source)

                    # Inject assistant tool_call + tool result into extra messages
                    tool_call_id = tc.get("id", "call_1")
                    tool_call_data = [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": tc["function"]["arguments"],
                        },
                    }]
                    extra_messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_call_data,
                    })
                    extra_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_content,
                    })
            else:
                # ── LLM returned text — final answer ──
                final_answer = response.get("content", "") or ""
                break

        # If loop exhausted without a text response, force-generate
        if not final_answer and total_tool_calls > 0:
            logger.info(
                "[Chatbox] Max rounds (%d) reached with %d tool calls — force generating answer",
                _MAX_TOOL_ROUNDS, total_tool_calls,
            )
            _persist_extra_messages(self._store, session_id, extra_messages)
            messages = self._build_messages(
                session_id, user_message, extra_messages=extra_messages,
                collections=collections,
                system_prompt=_sys_prompt, catalog_text=_cat_text,
                pre_message_context=meeting_speaker_mapping,
            )
            final_answer = _force_generate_answer(self._llm, messages)

        # Save assistant message + extra messages (tool calls/results)
        _persist_extra_messages(self._store, session_id, extra_messages)
        if final_answer:
            self._store.add_message(
                session_id, "assistant", final_answer,
                sources=all_sources if all_sources else None,
                metadata={"tool_calls": total_tool_calls},
            )

        return ChatResponse(
            answer=final_answer,
            sources=all_sources,
            tool_calls=total_tool_calls,
        )

    # ── streaming chat ───────────────────────────────────────────────

    async def chat_stream(
        self, session_id: str, user_message: str, *,
        thinking: bool = True, collections: list[str] | None = None,
        mode: str = "agentic",
        provider_id: str | None = None, model: str | None = None,
        web_search_enabled: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """Streaming chat — yields SSE event dicts."""
        if not user_message or not user_message.strip():
            yield {"type": "done", "sources": []}
            return

        # Temporary model override: swap self._llm for this request, restore after
        _saved_llm = self._llm
        if provider_id and model:
            from src.config import get_config as _get_cfg
            from src.providers.llm import create_llm_for_provider
            _cfg = _get_cfg()
            for _p in _cfg.llm.providers:
                if _p.id == provider_id:
                    try:
                        self._llm = create_llm_for_provider(_p, model=model)
                        logger.info("[Chatbox] Model override: %s/%s", _p.name, model)
                    except Exception:
                        pass
                    break

        # ── Mode-specific setup ─────────────────────────────────────
        _tools, _sys_prompt, _cat_text = self._resolve_tools_and_prompt(
            mode, session_id, collections, web_search_enabled=web_search_enabled,
        )
        # Explicit toggle state for the model (enabled vs disabled ≠ user Decline)
        try:
            from src.chatbox.web_search import web_toggle_label

            _wt = web_toggle_label(web_search_enabled=web_search_enabled)
            if _wt == "enabled":
                _sys_prompt = (
                    f"{_sys_prompt}\n\n"
                    "[Web search toggle: enabled. "
                    "When you need public/internet info (or the knowledge base lacks it), "
                    "CALL request_web_search immediately with a good query. "
                    "Do NOT ask the user whether Web is on, and do NOT ask them to "
                    "send another message first. The UI will handle Allow/Decline. "
                    "Tool results: status=ok|user_declined|disabled; "
                    "user_declined ≠ disabled.]"
                )
            else:
                _sys_prompt = (
                    f"{_sys_prompt}\n\n"
                    "[Web search toggle: disabled. "
                    "request_web_search will return status=disabled. "
                    "Do not ask the user to Allow web search in chat text — "
                    "briefly note that Web is off if internet data is required, "
                    "and answer from the knowledge base only.]"
                )
        except Exception:
            pass

        # Quick-chat truncation check
        msg_count = self._check_session_truncation(session_id)

        # Speaker mapping for meeting sessions (ephemeral, per-message)
        meeting_speaker_mapping = None
        if session_id.startswith("meeting_"):
            meeting_id = session_id[len("meeting_"):]
            meeting_speaker_mapping = _build_speaker_mapping(meeting_id)

        _quick_warn = (
            msg_count is not None
            and msg_count >= _QUICK_WARN_THRESHOLD
            and msg_count < _QUICK_MAX_MESSAGES
        )

        # Prefer request collections over session's stored collections
        if collections is None:
            collections = self._get_collections(session_id)
        total_tool_calls = 0
        agentic_search_calls = 0  # search_knowledge_base only (see _MAX_AGENTIC_SEARCH_CALLS)
        thinking_aq_count = 0
        thinking_task_count = 0
        thinking_summary: dict = {"aq_count": 0, "task_count": 0, "tasks": []}
        # Persisted for UI reload (structure / web / search steps)
        tool_trace: list[dict] = []

        # Save user message
        self._store.add_message(session_id, "user", user_message)

        extra_messages: list[dict] = []
        all_sources: list[dict] = []
        # Engineering control (not prompt-only): web HITL once per user-message stream.
        # approved → later request_web_search runs without dialog
        # declined → web tool removed for rest of this stream; no more confirms
        # next user message starts a fresh stream (Web toggle / Always-allow apply again)
        web_hitl_decision: str | None = None  # None | "approved" | "declined"

        def _tools_this_round() -> list:
            """Drop request_web_search after decline so the model cannot re-call it."""
            if web_hitl_decision != "declined" or not _tools:
                return _tools or []
            out = []
            for t in _tools:
                fn = (t.get("function") or {}) if isinstance(t, dict) else {}
                if fn.get("name") == WEB_SEARCH_TOOL_NAME:
                    continue
                out.append(t)
            return out

        def _web_tool_result(
            *,
            status: str,
            query: str = "",
            message: str = "",
        ) -> str:
            from src.chatbox.web_search import format_web_tool_result, web_toggle_label

            return format_web_tool_result(
                status=status,
                web_toggle=web_toggle_label(web_search_enabled=web_search_enabled),
                query=query,
                message=message,
            )

        for _round in range(_MAX_TOOL_ROUNDS):
            messages = self._build_messages(
                session_id, user_message, extra_messages=extra_messages,
                collections=collections,
                system_prompt=_sys_prompt, catalog_text=_cat_text,
                pre_message_context=meeting_speaker_mapping,
            )
            tools_round = _tools_this_round()

            # ── Streaming LLM call (real token-by-token, threaded) ──
            token_queue: sync_queue.Queue = sync_queue.Queue()
            client = getattr(self._llm, "_client", None)
            model = getattr(self._llm, "_model", "gpt-4")

            def _stream_llm():
                """Run streaming LLM call in thread. Puts (kind, data) tuples in queue."""
                nonlocal client, model
                if client is None:
                    # Fallback: non-streaming generate()
                    logger.warning("Chat LLM has no _client; falling back to generate()")
                    prompt = messages[-1]["content"] if messages else ""
                    system = ""
                    if messages and messages[0]["role"] == "system":
                        system = messages[0]["content"]
                    content = self._llm.generate(prompt, system=system)
                    token_queue.put(("token", content))
                    token_queue.put(("done", {"content": content}))
                    return

                stream_kwargs: dict = dict(
                    model=model, messages=messages, temperature=0.1,
                    stream=True,
                )
                # Engineering: omit web tool after decline; force answer if no tools left
                if tools_round:
                    stream_kwargs["tools"] = tools_round
                    stream_kwargs["tool_choice"] = "auto"
                else:
                    stream_kwargs["tool_choice"] = "none"
                # Think toggle: must set enabled/disabled for DeepSeek-class models.
                # If we omit extra_body when Think is OFF, some models still fill
                # reasoning_content first and only emit content at the end → no
                # token-by-token answer stream (one big dump).
                model_lower = (model or "").lower()
                base_url = str(getattr(self._llm, "_base_url", "") or "").lower()
                is_dashscope = "dashscope" in base_url or "aliyuncs" in base_url
                is_deepseek = "deepseek" in model_lower or "deepseek" in base_url
                build_extra = getattr(self._llm, "_build_thinking_extra", None)
                if callable(build_extra):
                    stream_kwargs["extra_body"] = build_extra(bool(thinking))
                elif thinking:
                    if is_dashscope:
                        stream_kwargs["extra_body"] = {"enable_thinking": True}
                    elif "minimax" in model_lower:
                        stream_kwargs["extra_body"] = {
                            "thinking": {"type": "adaptive"}
                        }
                    else:
                        stream_kwargs["extra_body"] = {
                            "thinking": {"type": "enabled"}
                        }
                elif is_dashscope:
                    stream_kwargs["extra_body"] = {"enable_thinking": False}
                elif "minimax" in model_lower or is_deepseek:
                    stream_kwargs["extra_body"] = {
                        "thinking": {"type": "disabled"}
                    }
                try:
                    mt = getattr(self._llm, "_default_max_tokens", 0)
                    if isinstance(mt, int) and mt > 0:
                        stream_kwargs["max_tokens"] = mt
                except (TypeError, AttributeError):
                    pass

                try:
                    stream = client.chat.completions.create(**stream_kwargs)
                except Exception as e:
                    # Retry once without thinking extra_body if provider rejects it
                    logger.warning("LLM streaming call failed: %s", e)
                    if stream_kwargs.get("extra_body") is not None:
                        try:
                            stream_kwargs.pop("extra_body", None)
                            stream = client.chat.completions.create(**stream_kwargs)
                            logger.warning(
                                "Retried stream without thinking extra_body after error: %s", e
                            )
                        except Exception as e2:
                            logger.exception("LLM streaming retry failed")
                            token_queue.put(("error", str(e2)))
                            return
                    else:
                        logger.exception("LLM streaming call failed")
                        token_queue.put(("error", str(e)))
                        return

                content = ""
                tool_calls_acc: dict[int, dict] = {}
                reasoning = None
                finish_reason = None
                # State machine for <think> tag parsing (MiniMax / R1-style models)
                think_buf = ""      # accumulated text for current segment
                in_think = False    # inside <think>...</think>
                all_thinking = ""
                streamed_answer_chars = 0  # content tokens already pushed to UI

                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    finish_reason = choice.finish_reason

                    delta_reasoning = getattr(delta, "reasoning_content", None) or None

                    if delta.content:
                        text = delta.content
                        # Parse <think> tags inline (strip always; surface only if Think ON)
                        while text:
                            if not in_think:
                                idx = text.find("<think>")
                                if idx == -1:
                                    # Check for partial "<think" at end
                                    partial = _find_partial_tag(text, "<think>")
                                    if partial >= 0:
                                        think_buf = text[partial:]
                                        emit = text[:partial]
                                        if emit:
                                            content += emit
                                            token_queue.put(("token", emit))
                                            streamed_answer_chars += len(emit)
                                        break
                                    content += text
                                    if text:
                                        token_queue.put(("token", text))
                                        streamed_answer_chars += len(text)
                                    break
                                else:
                                    # Emit text before <think>
                                    before = text[:idx]
                                    if before:
                                        content += before
                                        token_queue.put(("token", before))
                                        streamed_answer_chars += len(before)
                                    text = text[idx + len("<think>"):]
                                    in_think = True
                                    think_buf = ""
                            else:
                                idx = text.find("</think>")
                                if idx == -1:
                                    think_buf += text
                                    all_thinking += text
                                    if thinking:
                                        token_queue.put(("thinking", text))
                                    break
                                else:
                                    think_buf += text[:idx]
                                    all_thinking += text[:idx]
                                    if thinking and think_buf:
                                        token_queue.put(("thinking", think_buf))
                                    text = text[idx + len("</think>"):]
                                    in_think = False
                                    think_buf = ""

                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc_delta.id or "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            acc = tool_calls_acc[idx]
                            if tc_delta.id:
                                acc["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    acc["function"]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    acc["function"]["arguments"] += tc_delta.function.arguments

                    if delta_reasoning:
                        if reasoning is None:
                            reasoning = ""
                        reasoning += delta_reasoning
                        if thinking:
                            # Think ON → timeline reasoning (streamed)
                            token_queue.put(("thinking", delta_reasoning))
                        # Think OFF: do not dump reasoning into the answer mid-stream;
                        # rely on thinking:disabled + content tokens. Fallback below
                        # only if content stayed empty.

                # Build result
                result: dict = {}
                if tool_calls_acc:
                    result["tool_calls"] = [
                        tool_calls_acc[i] for i in sorted(tool_calls_acc)
                    ]
                else:
                    from src.providers.llm.openai_compat import _strip_think
                    final_text = _strip_think(content) or ""
                    # Fallback only when no content was streamed at all
                    if not final_text and (reasoning or all_thinking):
                        final_text = _strip_think(reasoning or all_thinking) or ""
                    # Avoid re-sending the whole answer if we already streamed it
                    if final_text and streamed_answer_chars == 0:
                        # Chunk large fallback so the UI still paints progressively
                        step = 24
                        for i in range(0, len(final_text), step):
                            token_queue.put(("token", final_text[i : i + step]))
                    result["content"] = final_text or content
                if reasoning:
                    result["reasoning_content"] = reasoning
                elif all_thinking:
                    result["reasoning_content"] = all_thinking
                token_queue.put(("done", result))

            loop_obj = asyncio.get_event_loop()
            future = loop_obj.run_in_executor(None, _stream_llm)

            # Poll for tokens while LLM is running — yield often so SSE flushes
            response = None
            while not future.done() or not token_queue.empty():
                drained = False
                while True:
                    try:
                        kind, data = token_queue.get_nowait()
                        drained = True
                        if kind == "thinking":
                            yield {"type": "thinking", "content": data}
                        elif kind == "token":
                            yield {"type": "token", "content": data}
                        elif kind == "done":
                            response = data
                        elif kind == "error":
                            yield {"type": "error", "content": f"LLM call failed: {data}"}
                            self._store.add_message(session_id, "assistant", f"Error: {data}")
                            yield {"type": "done", "sources": []}
                            return
                    except sync_queue.Empty:
                        break
                # Let the event loop flush SSE frames to the client
                if drained:
                    await asyncio.sleep(0)
                elif not future.done():
                    await asyncio.sleep(0.01)

            # Ensure future is fully consumed
            try:
                await future
            except Exception as e:
                logger.exception("LLM streaming thread raised")
                yield {"type": "error", "content": f"LLM error: {e}"}
                return

            if response is None:
                yield {"type": "error", "content": "No response from LLM"}
                return

            if response.get("tool_calls"):
                # ── Tool call path ──
                tcs = response["tool_calls"]
                if mode != "direct" and len(tcs) > 1:
                    tcs = merge_search_tool_calls(tcs)
                forced_col = self._forced_collection(mode, collections)

                for tc in tcs:
                    tool_name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = (
                            {"raw_query": user_message}
                            if mode != "direct"
                            else {"query": user_message}
                        )

                    is_multimodal = False
                    tool_content: str | list = ""
                    _ui_status = "done"
                    _ui_source_type = None
                    # Per-tool RAG/web hit count (structure tools omit this)
                    _sources_this_call: int | None = None

                    if tool_name == WEB_SEARCH_TOOL_NAME:
                        raw_query = args.get("query", user_message)
                    elif tool_name in STRUCTURE_TOOL_NAMES:
                        raw_query = (
                            args.get("file_id")
                            or args.get("source")
                            or args.get("collection")
                            or tool_name
                        )
                    elif mode == "direct":
                        raw_query = args.get("query", user_message)
                    else:
                        raw_query = args.get("raw_query", user_message)

                    yield {
                        "type": "tool_call_start",
                        "tool": tool_name,
                        "raw_query": str(raw_query)[:200],
                        "tool_call_id": tc.get("id", ""),
                    }

                    # Disallowed tools (non-web) — return a result so the loop continues
                    if not self._is_allowed_tool(
                        tool_name, mode, session_id, web_search_enabled=web_search_enabled,
                    ):
                        logger.warning(
                            "Disallowed tool %s (mode=%s web=%s) — returning error result",
                            tool_name, mode, web_search_enabled,
                        )
                        tool_content = (
                            f"Tool '{tool_name}' is not available in this context. "
                            "Continue without it."
                        )
                        _ui_status = "error"
                        total_tool_calls += 1
                        _skip_exec = True
                    else:
                        _skip_exec = False

                    # ── Web search (toggle status + HITL) ──
                    if not _skip_exec and tool_name == WEB_SEARCH_TOOL_NAME:
                        from src.chatbox.web_search import (
                            format_web_results_for_llm,
                            get_web_search_config,
                            has_web_search_api_key,
                            tavily_search,
                            web_results_to_sources,
                            web_search_confirm_store,
                            web_toggle_label,
                        )
                        wq = str(args.get("query") or user_message).strip()
                        toggle = web_toggle_label(web_search_enabled=web_search_enabled)

                        # Toggle OFF or no API key → status=disabled (not a user Decline)
                        if not web_search_enabled or not has_web_search_api_key():
                            if not has_web_search_api_key():
                                msg = (
                                    "Web search is disabled: Tavily API key is missing "
                                    "(Settings → Web Search). Answer from knowledge base only."
                                )
                            else:
                                msg = (
                                    "status=disabled: Web toggle is OFF (not a user Decline). "
                                    "No internet results. Answer from knowledge base only."
                                )
                            tool_content = _web_tool_result(
                                status="disabled",
                                query=wq,
                                message=msg,
                            )
                            _ui_status = "declined"
                            _ui_source_type = "web"
                        elif web_hitl_decision == "declined":
                            # User already declined this turn (toggle still enabled)
                            tool_content = _web_tool_result(
                                status="user_declined",
                                query=wq,
                                message=(
                                    "User manually declined web search for this turn "
                                    f"(query={wq[:180]!r}). web_toggle is still enabled — "
                                    "this is not a disabled toggle. No internet results. "
                                    "Answer from knowledge base only; do not invent web facts."
                                ),
                            )
                            _ui_status = "declined"
                            _ui_source_type = "web"
                        else:
                            cfg = get_web_search_config()
                            # First call this turn → HITL; after Allow, skip dialog
                            if web_hitl_decision != "approved":
                                confirm_id = web_search_confirm_store.create(wq)
                                yield {
                                    "type": "web_search_confirm",
                                    "confirm_id": confirm_id,
                                    "query": wq,
                                    "tool_call_id": tc.get("id", ""),
                                    "message": (
                                        "Allow searching the public internet for this "
                                        "turn? Results are external data, not knowledge base."
                                    ),
                                }
                                timeout = float(
                                    getattr(cfg, "confirm_timeout_sec", 120) or 120
                                )
                                approved = await web_search_confirm_store.wait(
                                    confirm_id, timeout=timeout
                                )
                                if not approved:
                                    web_hitl_decision = "declined"
                                    tool_content = _web_tool_result(
                                        status="user_declined",
                                        query=wq,
                                        message=(
                                            "User manually clicked Decline for this web "
                                            f"search (query={wq[:180]!r}). "
                                            f"web_toggle={toggle} (still enabled). "
                                            "This is a user refusal of this turn's search, "
                                            "not a disabled toggle. No internet results. "
                                            "Answer from knowledge base only."
                                        ),
                                    )
                                    _ui_status = "declined"
                                    _ui_source_type = "web"
                                    logger.info(
                                        "[Chatbox] Web search user_declined this turn "
                                        "(web_toggle=%s) — remove tool for remaining rounds",
                                        toggle,
                                    )
                                else:
                                    web_hitl_decision = "approved"
                                    logger.info(
                                        "[Chatbox] Web search approved this turn "
                                        "(web_toggle=%s) — further calls skip confirm",
                                        toggle,
                                    )

                            if web_hitl_decision == "approved":
                                yield {
                                    "type": "searching",
                                    "query": f"[WEB] {wq[:180]}",
                                    "tool_call_id": tc.get("id", ""),
                                    "source_type": "web",
                                }
                                loop = asyncio.get_event_loop()
                                payload = await loop.run_in_executor(
                                    None,
                                    lambda: tavily_search(
                                        wq,
                                        api_key=cfg.api_key,
                                        max_results=cfg.max_results,
                                        search_depth=cfg.search_depth,
                                    ),
                                )
                                body = format_web_results_for_llm(payload)
                                tool_content = (
                                    _web_tool_result(
                                        status="ok",
                                        query=wq,
                                        message="Web search completed.",
                                    )
                                    + "\n\n"
                                    + body
                                )
                                _web_n = 0
                                for s in web_results_to_sources(payload):
                                    if s not in all_sources:
                                        all_sources.append(s)
                                    _web_n += 1
                                _sources_this_call = _web_n
                                _ui_source_type = "web"
                        total_tool_calls += 1

                    # ── Structure / summary / full-text ──
                    elif not _skip_exec and tool_name in STRUCTURE_TOOL_NAMES:
                        tool_content = await execute_structure_tool_async(
                            tool_name,
                            args,
                            mode=mode,
                            forced_collection=forced_col,
                        )
                        # Structure tools don't produce RAG "sources" for the Sources panel
                        _sources_this_call = None
                        total_tool_calls += 1

                    # ── Direct search ──
                    elif not _skip_exec and mode == "direct":
                        if self._direct is None:
                            tool_content = "Direct retrieval is not configured."
                            _ui_status = "error"
                            _sources_this_call = 0
                        else:
                            query = args.get("query", raw_query)
                            yield {
                                "type": "searching",
                                "query": str(query)[:200],
                                "tool_call_id": tc.get("id", ""),
                            }
                            include_images = args.get("include_images", False)
                            if not include_images:
                                model = getattr(self._llm, "_model", "")
                                from src.config import get_config as _get_cfg
                                _cfg = _get_cfg()
                                for _p in _cfg.llm.providers:
                                    if hasattr(_p, "visual_model_ids") and model in _p.visual_model_ids:
                                        include_images = True
                                        break
                            direct_cols = [forced_col] if forced_col else (collections or [])
                            _loop = asyncio.get_event_loop()

                            def _run_direct_retrieval():
                                result = self._direct.retrieve(
                                    query,
                                    collections=direct_cols,
                                    top_k=10,
                                    generate_answer=False,
                                )
                                _sources = []
                                for chunk in result.chunks:
                                    s = {
                                        "text": getattr(chunk, "text", "")[:200],
                                        "score": getattr(chunk, "score", 0.0),
                                        "metadata": getattr(chunk, "metadata", {}),
                                    }
                                    if s not in _sources:
                                        _sources.append(s)
                                _multimodal = None
                                if include_images and result.chunks:
                                    try:
                                        from src.rag.agentic_query import (
                                            _stitch_images_from_chunks,
                                            _build_multimodal_context,
                                        )
                                        imgs = _stitch_images_from_chunks(result.chunks)
                                        if imgs:
                                            _multimodal = _build_multimodal_context(
                                                result.context, imgs,
                                            )
                                    except Exception:
                                        logger.exception("[Chatbox] Image stitching failed")
                                return {
                                    "sources": _sources,
                                    "context": result.context or "No relevant information found.",
                                    "multimodal": _multimodal,
                                }

                            direct_data = await _loop.run_in_executor(None, _run_direct_retrieval)
                            total_tool_calls += 1
                            for s in direct_data["sources"]:
                                if s not in all_sources:
                                    all_sources.append(s)
                            _sources_this_call = len(direct_data["sources"])
                            if direct_data["multimodal"] is not None:
                                is_multimodal = True
                                tool_content = direct_data["multimodal"]
                            else:
                                tool_content = direct_data["context"]

                    # ── Agentic search (search_knowledge_base / other search facades) ──
                    elif not _skip_exec and self._agentic is None:
                        tool_content = (
                            "Knowledge base search is not configured. "
                            "Please enable Function Calling on an LLM model in Settings."
                        )
                        _ui_status = "error"
                    elif not _skip_exec and (
                        tool_name == "search_knowledge_base"
                        and agentic_search_calls >= _MAX_AGENTIC_SEARCH_CALLS
                    ):
                        # Separate from outer _MAX_TOOL_ROUNDS: stop KB thrash,
                        # leave budget for web / synthesis.
                        tool_content = (
                            f"Agentic knowledge-base search limit reached "
                            f"({_MAX_AGENTIC_SEARCH_CALLS} calls this turn). "
                            "Do NOT call search_knowledge_base again. "
                            "Synthesize the final answer now from tool results "
                            "already in this conversation."
                        )
                        _ui_status = "error"
                        total_tool_calls += 1
                        logger.info(
                            "[Chatbox] Blocked search_knowledge_base "
                            "(agentic_search_calls=%d/%d)",
                            agentic_search_calls,
                            _MAX_AGENTIC_SEARCH_CALLS,
                        )
                    elif not _skip_exec:
                        generate_answer = args.get("generate_answer", False)
                        include_images = args.get("include_images", False)
                        if not include_images:
                            model = getattr(self._llm, "_model", "")
                            from src.config import get_config as _get_cfg
                            _cfg = _get_cfg()
                            for _p in _cfg.llm.providers:
                                if hasattr(_p, "visual_model_ids") and model in _p.visual_model_ids:
                                    include_images = True
                                    break
                        decompose = args.get("decompose", True)

                        step_queue: sync_queue.Queue = sync_queue.Queue()

                        def _on_step(event: dict):
                            if event.get("step") in (
                                "decompose", "task_start", "aq_start",
                                "aq_done", "synthesize_task", "synthesize_merge",
                                "retrieve", "retrieving", "grading",
                                "variant_generation", "rewrite_loop_done",
                            ):
                                step_queue.put(event)

                        loop = asyncio.get_event_loop()
                        future = loop.run_in_executor(
                            None,
                            lambda: self._agentic.run(
                                raw_query,
                                collections=collections or None,
                                generate_answer=generate_answer,
                                include_images=include_images,
                                on_step=_on_step,
                                decompose=decompose,
                            ),
                        )
                        total_tool_calls += 1
                        if tool_name == "search_knowledge_base":
                            agentic_search_calls += 1
                        all_thinking_events: list[dict] = []
                        while not future.done() or not step_queue.empty():
                            batch_new = False
                            while True:
                                try:
                                    evt = step_queue.get_nowait()
                                    batch_new = True
                                    if evt.get("step") == "aq_start":
                                        thinking_aq_count += 1
                                    elif evt.get("step") == "task_start":
                                        thinking_task_count += 1
                                    all_thinking_events.append(evt)
                                    yield {
                                        "type": "tool_step",
                                        "step": evt.get("step", ""),
                                        "content": evt.get("content", ""),
                                        "task": evt.get("task", ""),
                                        "iteration": evt.get("iteration", 0),
                                        "chunks": evt.get("chunks", 0),
                                        "aq_id": evt.get("aq_id", ""),
                                        "aq_count": evt.get("aq_count", 0),
                                        "sufficient": evt.get("sufficient", False),
                                        "error": evt.get("error", False),
                                    }
                                except sync_queue.Empty:
                                    break
                            if batch_new:
                                summary = _build_thinking_summary(all_thinking_events)
                                yield {"type": "thinking_summary", **summary}
                            if not future.done():
                                await asyncio.sleep(0.05)

                        result = await future
                        while True:
                            try:
                                evt = step_queue.get_nowait()
                                if evt.get("step") == "aq_start":
                                    thinking_aq_count += 1
                                elif evt.get("step") == "task_start":
                                    thinking_task_count += 1
                                all_thinking_events.append(evt)
                                yield {
                                    "type": "tool_step",
                                    "step": evt.get("step", ""),
                                    "content": evt.get("content", ""),
                                    "task": evt.get("task", ""),
                                    "iteration": evt.get("iteration", 0),
                                    "chunks": evt.get("chunks", 0),
                                    "aq_id": evt.get("aq_id", ""),
                                    "aq_count": evt.get("aq_count", 0),
                                    "sufficient": evt.get("sufficient", False),
                                    "error": evt.get("error", False),
                                }
                            except sync_queue.Empty:
                                break

                        thinking_summary = _build_thinking_summary(all_thinking_events)
                        yield {"type": "thinking_summary", **thinking_summary}

                        _chunk_n = 0
                        for chunk in result.all_chunks:
                            source = {
                                "text": getattr(chunk, "text", "")[:200],
                                "score": getattr(chunk, "score", 0.0),
                                "metadata": getattr(chunk, "metadata", {}),
                            }
                            if source not in all_sources:
                                all_sources.append(source)
                            _chunk_n += 1
                        _sources_this_call = _chunk_n

                        is_multimodal = isinstance(result.answer, list)
                        if is_multimodal:
                            tool_content = result.answer
                        else:
                            parts = []
                            if result.answer:
                                parts.append(result.answer)
                            elif result.context:
                                parts.append(result.context)
                            tool_content = "\n\n".join(parts) if parts else "No relevant information found."

                    # UI: tool result with preview for collapsible timeline
                    _result_preview = _preview_tool_content(tool_content)
                    _tr: dict = {
                        "type": "tool_result",
                        "status": _ui_status,
                        "content": _result_preview,
                        "tool": tool_name,
                        "source_type": _ui_source_type,
                        "tool_call_id": tc.get("id", ""),
                    }
                    # Only search/web tools report sources_count (avoid "0 sources" on list_library_tree)
                    if _sources_this_call is not None:
                        _tr["sources_count"] = _sources_this_call
                    yield _tr
                    # Record for session reload (frontend timeline is not stored otherwise)
                    _trace_entry: dict = {
                        "tool": tool_name,
                        "toolQuery": str(raw_query)[:300] if raw_query else "",
                        "toolResult": _result_preview,
                        "toolStatus": _ui_status,
                        "sourceType": _ui_source_type,
                    }
                    if _sources_this_call is not None:
                        _trace_entry["sourcesCount"] = _sources_this_call
                    if (
                        tool_name == "search_knowledge_base"
                        and thinking_summary.get("aq_count", 0) > 0
                    ):
                        # Snapshot agentic summary for classic retrieval UI
                        _trace_entry["summary"] = {
                            "aq_count": thinking_summary.get("aq_count", 0),
                            "task_count": thinking_summary.get("task_count", 0),
                            "tasks": list(thinking_summary.get("tasks") or []),
                        }
                    tool_trace.append(_trace_entry)

                    # Inject assistant tool_call + tool result for next LLM round
                    tool_call_id = tc.get("id", "call_1")
                    tool_call_data = [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": tc["function"]["arguments"],
                        },
                    }]
                    assistant_extra: dict = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_call_data,
                    }
                    if response.get("reasoning_content"):
                        assistant_extra["reasoning_content"] = response["reasoning_content"]
                    extra_messages.append(assistant_extra)
                    extra_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_content,
                    })

            else:
                # ── Text response — tokens already streamed, just finalize ──
                final_content = response.get("content", "") or ""

                # Save assistant message
                meta: dict = {"tool_calls": total_tool_calls}
                if thinking_summary.get("aq_count", 0) > 0:
                    meta["thinking_summary"] = thinking_summary
                if tool_trace:
                    meta["tool_trace"] = tool_trace
                if response.get("reasoning_content"):
                    meta["reasoning_content"] = response["reasoning_content"]
                # Persist tool messages for KV cache reuse in future rounds
                _persist_extra_messages(self._store, session_id, extra_messages)
                self._store.add_message(
                    session_id, "assistant", final_content,
                    sources=all_sources if all_sources else None,
                    metadata=meta,
                )

                logger.info(
                    "[Chatbox] YIELD done: all_sources=%d total_tool_calls=%d mode=%s tool_trace=%d",
                    len(all_sources), total_tool_calls, mode, len(tool_trace),
                )
                logger.info(
                    "[Chatbox] YIELD done: sources=%d tool_calls=%d mode=%s",
                    len(all_sources), total_tool_calls, mode,
                )
                if all_sources:
                    for i, s in enumerate(all_sources[:3]):
                        logger.info(
                            "[Chatbox]   source[%d]: text=%s... score=%.3f meta_keys=%s",
                            i, s.get("text", "")[:60], s.get("score", 0),
                            list((s.get("metadata") or {}).keys())[:6],
                        )
                # message_count: Q&A turns for quick/meeting UI; raw rows for main Chat sidebar
                _done_count = msg_count if msg_count is not None else 0
                if session_id.startswith("quick_") or session_id.startswith("meeting_"):
                    _ct = getattr(self._store, "count_dialogue_turns", None)
                    if callable(_ct):
                        try:
                            _done_count = int(_ct(session_id))
                        except Exception:
                            pass
                else:
                    try:
                        _done_count = int(
                            self._store.count_messages(session_id, exclude_system=True)
                        )
                    except Exception:
                        pass
                yield {
                    "type": "done",
                    "sources": all_sources,
                    "message_count": _done_count,
                    "context_warning": _quick_warn,
                }
                return

        # Max rounds reached — force a final answer without tools
        logger.info(
            "[Chatbox] Max rounds (%d) reached with %d tool calls — force generating answer",
            _MAX_TOOL_ROUNDS, total_tool_calls,
        )
        _persist_extra_messages(self._store, session_id, extra_messages)
        messages = self._build_messages(
            session_id, user_message, extra_messages=extra_messages,
            collections=collections,
            system_prompt=_sys_prompt, catalog_text=_cat_text,
        )
        final_content = _force_generate_answer(self._llm, messages)
        if final_content:
            # Yield the answer as token events so the frontend shows it,
            # then persist and emit done.
            for i in range(0, len(final_content), 4):
                yield {"type": "token", "content": final_content[i:i + 4]}
            meta_final: dict = {"tool_calls": total_tool_calls}
            if thinking_summary.get("aq_count", 0) > 0:
                meta_final["thinking_summary"] = thinking_summary
            if tool_trace:
                meta_final["tool_trace"] = tool_trace
            self._store.add_message(
                session_id, "assistant", final_content,
                sources=all_sources if all_sources else None,
                metadata=meta_final,
            )
        else:
            logger.warning(
                "[Chatbox] Force generate returned empty — total_tool_calls=%d sources=%d",
                total_tool_calls, len(all_sources),
            )
        _done_count = msg_count if msg_count is not None else 0
        if session_id.startswith("quick_") or session_id.startswith("meeting_"):
            _ct = getattr(self._store, "count_dialogue_turns", None)
            if callable(_ct):
                try:
                    _done_count = int(_ct(session_id))
                except Exception:
                    pass
        else:
            try:
                _done_count = int(
                    self._store.count_messages(session_id, exclude_system=True)
                )
            except Exception:
                pass
        yield {
            "type": "done",
            "sources": all_sources,
            "message_count": _done_count,
            "context_warning": _quick_warn,
        }
        return

    # ── LLM call with tools ──────────────────────────────────────────

    def _call_llm_with_tools(self, messages: list[dict], tools=None) -> dict:
        """Call the Chat LLM with tools enabled.

        Uses the underlying OpenAI-compatible client directly for function
        calling support (not available through the LLMProvider ABC).

        Returns:
            {"content": str} — text response
            {"tool_calls": [...]} — function call request
        """
        client = getattr(self._llm, "_client", None)
        model = getattr(self._llm, "_model", "gpt-4")

        if client is None:
            # Fallback: use generate() — no tool calling
            logger.warning("Chat LLM has no _client; falling back to generate()")
            prompt = messages[-1]["content"] if messages else ""
            system = ""
            if messages and messages[0]["role"] == "system":
                system = messages[0]["content"]
            content = self._llm.generate(prompt, system=system)
            return {"content": content}

        _tools = tools if tools is not None else TOOLS

        kwargs = dict(
            model=model,
            messages=messages,
            temperature=0.1,
            tools=_tools,
            tool_choice="auto",
        )
        # Apply max_tokens if available (guard against mock objects)
        try:
            mt = getattr(self._llm, "_default_max_tokens", 0)
            if isinstance(mt, int) and mt > 0:
                kwargs["max_tokens"] = mt
        except (TypeError, AttributeError):
            pass

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.exception("LLM tool call failed: %s", e)
            return {"content": f"Request failed: {e}"}

        if not resp.choices:
            return {"content": ""}

        choice = resp.choices[0]
        msg = choice.message

        # Extract reasoning_content (DeepSeek thinking mode requires it
        # to be passed back in subsequent requests)
        reasoning = getattr(msg, "reasoning_content", None) or None

        # Check for tool calls
        if msg.tool_calls:
            result: dict = {
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            }
            if reasoning:
                result["reasoning_content"] = reasoning
            return result

        result = {"content": msg.content or ""}
        if reasoning:
            result["reasoning_content"] = reasoning
        return result

    # ── tokenizer for streaming ─────────────────────────────────────

    @staticmethod
    def _tokenize(text: str, chunk_size: int = 4):
        """Yield text in small chunks for SSE streaming."""
        for i in range(0, len(text), chunk_size):
            yield text[i:i + chunk_size]


def _preview_tool_content(content, *, max_chars: int = 4000) -> str:
    """Short plain-text preview of a tool result for Chat timeline UI."""
    if content is None:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(str(p.get("text") or ""))
                elif p.get("type") == "image_url":
                    parts.append("[image]")
            elif isinstance(p, str):
                parts.append(p)
        s = "\n".join(x for x in parts if x).strip() or "[multimodal tool result]"
    else:
        s = str(content).strip()
    if len(s) > max_chars:
        return s[:max_chars] + f"\n…[{len(s)} chars total]"
    return s


def _force_generate_answer(llm, messages: list[dict]) -> str:
    """Generate a final answer without tools (strips thinking tags).

    Appends a synthesis instruction so the LLM knows to produce an answer
    from the tool results rather than requesting more tools.
    """
    client = getattr(llm, "_client", None)
    model = getattr(llm, "_model", "gpt-4")

    # Append a clear synthesis instruction — the LLM may have been in a
    # tool-calling loop and needs an explicit cue to stop and synthesize.
    _msgs = list(messages)
    _msgs.append({
        "role": "user",
        "content": (
            "You have reached the maximum number of search rounds. "
            "Please synthesize a comprehensive answer NOW from all the search "
            "results above. Do NOT request any more tools — just write your "
            "final answer directly, citing specific details from the results."
        ),
    })

    try:
        if client:
            kwargs = dict(model=model, messages=_msgs, temperature=0.1, max_tokens=4096)
            if "minimax" not in (model or "").lower():
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            resp = client.chat.completions.create(**kwargs)
            from src.providers.llm.openai_compat import _strip_think
            return _strip_think(resp.choices[0].message.content or "")
        else:
            return llm.generate(
                "Generate a final answer based on the conversation.",
                system="You are a helpful assistant.",
                thinking=False,
            ) or ""
    except Exception:
        logger.exception("[Chatbox] Force generate answer failed")
        return ""


def _find_partial_tag(text: str, tag: str) -> int:
    """Find the start of a partial open tag in *text*. Returns -1 if not found."""
    for i in range(1, len(tag)):
        if text.endswith(tag[:i]):
            return len(text) - i
    return -1


def _persist_extra_messages(store, session_id: str, extra_messages: list[dict]) -> None:
    """Save tool_call and tool_result messages to DB for KV cache reuse."""
    for em in extra_messages:
        try:
            # Skip multimodal messages — content is list[dict], can't store in TEXT column
            if isinstance(em.get("content"), list):
                continue
            role = em.get("role", "")
            content = em.get("content") or ""
            meta: dict = {}
            if role == "assistant" and em.get("tool_calls"):
                meta["tool_calls"] = em["tool_calls"]
                rc = em.get("reasoning_content")
                if rc:
                    meta["reasoning_content"] = rc
            elif role == "tool":
                meta["tool_call_id"] = em.get("tool_call_id", "")
            store.add_message(session_id, role, content, metadata=meta if meta else None)
        except Exception:
            logger.exception("Failed to persist extra message role=%s", em.get("role", "?"))


def _build_thinking_summary(events: list[dict]) -> dict:
    """Aggregate raw tool_step events into a structured thinking summary."""
    tasks: dict[str, dict] = {}
    task_order: list[str] = []
    total_aqs = 0
    aq_current_chunks: dict[str, int] = {}  # progressive chunk count
    status = ""

    for e in events:
        step = e.get("step", "")
        content = e.get("content", "")

        # Track latest activity for live status
        if step in ("decompose", "retrieving", "grading", "variant_generation",
                     "synthesize_task", "synthesize_merge", "retrieve"):
            status = content

        if step == "task_start":
            task_key = e.get("task", "") or content
            if task_key not in tasks:
                tasks[task_key] = {
                    "task": task_key,
                    "task_query": content,
                    "aq_count": e.get("aq_count", 0),
                    "aqs": [],
                    "useful_chunks": 0,
                }
                task_order.append(task_key)

        elif step == "aq_start":
            total_aqs += 1
            aq_id = e.get("aq_id", "")
            task_key = e.get("task", "")
            if task_key in tasks:
                tasks[task_key]["aqs"].append({
                    "aq_id": aq_id,
                    "query": content,
                    "variants": [],
                    "variant_count": 0,
                    "final_chunks": 0,
                    "current_chunks": 0,
                    "has_gaps": False,
                })

        elif step == "variant_generation":
            aq_id = e.get("aq_id", "")
            variants = e.get("variants", [])
            vc = e.get("variant_count", 0)
            for t in tasks.values():
                for aq in t["aqs"]:
                    if aq["aq_id"] == aq_id:
                        aq["variants"] = list(variants)
                        aq["variant_count"] = vc
                        break

        elif step == "retrieving":
            aq_id = e.get("aq_id", "")
            # Parse: "Retrieved N unique chunks across M search variants"
            if "unique chunks" in content:
                try:
                    total = int(content.split("unique chunks")[0].split()[-1])
                    aq_current_chunks[aq_id] = total
                except (ValueError, IndexError):
                    pass

        elif step == "aq_done":
            aq_id = e.get("aq_id", "")
            chunks = e.get("chunks", 0)
            has_gaps = e.get("has_gaps", True)
            for t in tasks.values():
                for aq in t["aqs"]:
                    if aq["aq_id"] == aq_id:
                        aq["final_chunks"] = chunks
                        aq["current_chunks"] = chunks
                        aq["has_gaps"] = has_gaps
                        t["useful_chunks"] = sum(a["final_chunks"] for a in t["aqs"])
                        break

    # Apply progressive state (not yet finalized)
    for t in tasks.values():
        for aq in t["aqs"]:
            aid = aq["aq_id"]
            if aid in aq_current_chunks and aq["final_chunks"] == 0:
                aq["current_chunks"] = aq_current_chunks[aid]

    return {
        "aq_count": total_aqs,
        "task_count": len(tasks),
        "tasks": [tasks[t] for t in task_order],
        "status": status,
    }
