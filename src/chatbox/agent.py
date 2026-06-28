"""ChatboxAgent — conversational agent with AgenticQueryService as a tool.

Uses function calling to decide whether to search the knowledge base.
Tool-use internals (rewrite/grading loops) are NOT exposed to the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue as sync_queue
import threading
from dataclasses import dataclass, field
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Tool definition
# ═══════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "Search the private knowledge base. You are an INFORMATION PLANNER — "
                "translate the user's question into concrete information needs, then "
                "decide how to search them.\n\n"
                "PLANNING RULES:\n"
                "1. If vague/ambiguous — ask user to clarify first.\n"
                "2. For chitchat and common knowledge — answer directly.\n"
                "3. DEFAULT: one call per round. Pack ALL your information needs into it "
                "with decompose=true. The system handles decomposition and parallel "
                "search. This is the right choice for comparison, multi-entity, "
                "multi-facet, and most analytical queries.\n"
                "4. EXCEPTION: use multiple rounds ONLY when you cannot name a search "
                "target in round N+1 without seeing round N's results (dependency chain). "
                "Example: 'What DB does X use? What CVEs does that DB have?' — the second "
                "question depends on the answer to the first.\n"
                "5. If unsure, use ONE call with decompose=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_query": {
                        "type": "string",
                        "description": (
                            "WHAT information you need to find — NOT the user's original "
                            "question verbatim. Write a natural phrase naming the entities "
                            "and what aspects to cover. "
                            "For a single topic: 'Project X system architecture and security model'. "
                            "For multiple topics: 'Project X deployment strategy, Project Y cost model'. "
                            "For dependent needs, describe only what you can search NOW. "
                            "Expand abbreviations and add context from conversation history."
                        ),
                    },
                    "generate_answer": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to generate a preliminary answer from search results. Usually true.",
                    },
                    "decompose": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "Set to TRUE to pack MULTIPLE independent search targets "
                            "into this single call (different entities, topics, or facets). "
                            "The system decomposes and searches them in parallel — faster "
                            "and more thorough than making separate calls. "
                            "Set to FALSE (default) for a SINGLE focused search."
                        ),
                    },
                },
                "required": ["raw_query"],
            },
        },
    }
]

# ═══════════════════════════════════════════════════════════════════
# Default system prompt
# ═══════════════════════════════════════════════════════════════════

DEFAULT_SYSTEM_PROMPT = """You are a knowledge base assistant. You can use the search_knowledge_base tool to search the user's private knowledge base.

YOUR ROLE — Information Planner:
Before calling search_knowledge_base, think about WHAT information you need, not just
what the user asked. Translate the user's question into concrete information needs.

DECISION RULES — one call or multiple rounds?
- DEFAULT: ONE call with decompose=true. Pack ALL information needs into it.
  The system handles decomposition and parallel search internally. This applies to
  comparison, multi-entity, multi-facet, and most analytical queries.
- EXCEPTION — dependency chain: ONLY use multiple rounds when you literally cannot
  formulate round N+1 until you see round N's results. This is a dependency, not
  a preference. Example: "What DB does X use? What CVEs does that DB have?" —
  you cannot name the DB in round 2 until round 1 tells you what it is.
- If in doubt, use ONE call with decompose=true.
- For comparison, contrast, and analysis: YOU write the final answer based on
  search results — the tool provides raw information, you provide insight.

WRITING raw_query:
- NEVER pass the user's question verbatim — write WHAT to search for
- Write a natural phrase describing what information you need and what aspects to
  cover. "Project X architecture and deployment approach" is good; a keyword dump
  like "architecture, framework, database" is NOT.
- For chitchat and common knowledge, answer directly without the tool
- Expand abbreviations and add context from conversation history in raw_query
- Base your answers on tool results with source citations

Formatting:
- When using markdown tables, ALWAYS put each row on its own line with proper newlines.
  Each row MUST be separated by a line break. The separator line MUST have its own line:
  | Header A | Header B |
  |----------|----------|
  | Cell 1   | Cell 2   |
- Use standard alignment: :--- (left), :---: (center), ---: (right). Never use ::--
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
# ChatboxAgent
# ═══════════════════════════════════════════════════════════════════

_MAX_TOOL_ROUNDS = 5
_MAX_HISTORY_MESSAGES = 50
_TOTAL_MAX_TOKENS = 128000  # generous ceiling


class ChatboxAgent:
    """Chat agent with conversation memory and RAG tool access.

    Uses function calling to decide when to search the knowledge base.
    Tool-use internals (rewrite/grading loops) are NOT exposed to the user.
    """

    def __init__(
        self,
        session_store,
        chat_llm,
        agentic_service,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ):
        self._store = session_store
        self._llm = chat_llm
        self._agentic = agentic_service
        self._system_prompt = system_prompt

    # ── helpers ──────────────────────────────────────────────────────

    def _build_catalog_text(self, session_id: str) -> str:
        """Build a concise catalog summary for the system prompt."""
        if self._agentic is None:
            return ""
        try:
            catalog = self._agentic.catalog
            if catalog is None:
                return ""
            cols = self._get_collections(session_id)
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

    def _build_messages(
        self,
        session_id: str,
        user_message: str,
        *,
        extra_messages: list[dict] | None = None,
    ) -> list[dict]:
        """Build OpenAI-compatible messages array for the LLM call."""
        messages: list[dict] = []

        # Static system prompt (position 0 — always cache-hit)
        messages.append({"role": "system", "content": self._system_prompt})

        # Catalog reference as separate message (position 1 — per-session, static within session)
        catalog_text = self._build_catalog_text(session_id)
        if catalog_text:
            messages.append({"role": "system", "content": catalog_text})

        # Load history (including persisted tool calls and results)
        hist = self._store.get_messages(session_id, limit=_MAX_HISTORY_MESSAGES)
        for m in hist:
            meta = m.metadata or {}
            msg: dict = {"role": m.role, "content": m.content}

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
                msg["content"] = m.content

            messages.append(msg)

        # Extra tool-call messages (injected during tool-use loop)
        if extra_messages:
            messages.extend(extra_messages)

        # Current user message — only if not already the last history message
        # (it was saved before the LLM call and loaded above)
        if not hist or hist[-1].content != user_message or hist[-1].role != "user":
            messages.append({"role": "user", "content": user_message})

        return messages

    # ── non-streaming chat ───────────────────────────────────────────

    def chat(self, session_id: str, user_message: str) -> ChatResponse:
        """Non-streaming chat. Returns final answer with sources."""
        if not user_message or not user_message.strip():
            return ChatResponse(answer="")

        collections = self._get_collections(session_id)
        total_tool_calls = 0

        # Save user message
        self._store.add_message(session_id, "user", user_message)

        extra_messages: list[dict] = []
        final_answer = ""
        all_sources: list[dict] = []

        for _round in range(_MAX_TOOL_ROUNDS):
            messages = self._build_messages(
                session_id, user_message, extra_messages=extra_messages,
            )

            # Call LLM with tools — use underlying OpenAI client directly
            # (avoids modifying the LLMProvider ABC)
            response = self._call_llm_with_tools(messages)

            if response.get("tool_calls"):
                # ── LLM wants to use a tool ──
                tcs = response["tool_calls"]
                # Merge multiple calls into one decompose=true (safety net)
                if len(tcs) > 1:
                    queries = []
                    for tc in tcs:
                        try:
                            a = json.loads(tc["function"]["arguments"])
                            q = a.get("raw_query", "")
                            if q:
                                queries.append(q)
                        except json.JSONDecodeError:
                            pass
                    if queries:
                        merged = ", ".join(queries)
                        logger.info("Merged %d tool_calls → 1 decompose=true: %r",
                                    len(tcs), merged[:200])
                        tcs = [{
                            "id": tcs[0].get("id", "call_1"),
                            "type": "function",
                            "function": {
                                "name": "search_knowledge_base",
                                "arguments": json.dumps({
                                    "raw_query": merged,
                                    "decompose": True,
                                }),
                            },
                        }]
                for tc in tcs:
                    if tc["function"]["name"] != "search_knowledge_base":
                        logger.warning("Unknown tool: %s", tc["function"]["name"])
                        continue

                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {"raw_query": user_message, "generate_answer": True}

                    raw_query = args.get("raw_query", user_message)
                    generate_answer = args.get("generate_answer", True)
                    decompose = args.get("decompose", True)

                    if self._agentic is None:
                        logger.warning("Tool call requested but agentic_service is None")
                        tool_content = "Knowledge base search is not configured. Please enable Function Calling on an LLM model in Settings."
                    else:
                        result = self._agentic.run(
                            raw_query,
                            collections=collections or None,
                            generate_answer=generate_answer,
                            decompose=decompose,
                        )

                        total_tool_calls += 1

                        # Build tool result message
                        tool_content_parts = []
                        if result.answer:
                            tool_content_parts.append(result.answer)
                        elif result.context:
                            tool_content_parts.append(result.context)

                        tool_content = "\n\n".join(tool_content_parts) if tool_content_parts else "No relevant information found."

                        # Collect sources
                        for chunk in result.all_chunks:
                            source = {
                                "text": getattr(chunk, "text", "")[:500],
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
                            "name": "search_knowledge_base",
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
    ) -> AsyncGenerator[dict, None]:
        """Streaming chat — yields SSE event dicts.

        Events:
          {"type":"tool_call_start", "tool":"search_knowledge_base"}
          {"type":"tool_step", "step":"decompose|aq_start|...", ...}
          {"type":"tool_result", "status":"done", "sources":[...]}
          {"type":"token", "content":"Hello"}
          {"type":"done", "sources":[...]}
        """
        if not user_message or not user_message.strip():
            yield {"type": "done", "sources": []}
            return

        # Prefer request collections over session's stored collections
        if collections is None:
            collections = self._get_collections(session_id)
        total_tool_calls = 0
        thinking_aq_count = 0
        thinking_task_count = 0
        thinking_summary: dict = {"aq_count": 0, "task_count": 0, "tasks": []}

        # Save user message
        self._store.add_message(session_id, "user", user_message)

        extra_messages: list[dict] = []
        all_sources: list[dict] = []

        for _round in range(_MAX_TOOL_ROUNDS):
            messages = self._build_messages(
                session_id, user_message, extra_messages=extra_messages,
            )

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

                stream_kwargs = dict(
                    model=model, messages=messages, temperature=0.1,
                    tools=TOOLS, tool_choice="auto", stream=True,
                    extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}},
                )
                try:
                    mt = getattr(self._llm, "_default_max_tokens", 0)
                    if isinstance(mt, int) and mt > 0:
                        stream_kwargs["max_tokens"] = mt
                except (TypeError, AttributeError):
                    pass

                try:
                    stream = client.chat.completions.create(**stream_kwargs)
                except Exception as e:
                    logger.exception("LLM streaming call failed")
                    token_queue.put(("error", str(e)))
                    return

                content = ""
                tool_calls_acc: dict[int, dict] = {}  # index → accumulated delta
                reasoning = None
                finish_reason = None

                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    finish_reason = choice.finish_reason

                    # Content tokens → emit immediately
                    if delta.content:
                        content += delta.content
                        token_queue.put(("token", delta.content))

                    # Tool call deltas → accumulate
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

                    # Reasoning (DeepSeek) — stream to frontend
                    delta_reasoning = getattr(delta, "reasoning_content", None) or None
                    if delta_reasoning:
                        if reasoning is None:
                            reasoning = ""
                        reasoning += delta_reasoning
                        token_queue.put(("thinking", delta_reasoning))

                # Build result
                result: dict = {}
                if tool_calls_acc:
                    result["tool_calls"] = [
                        tool_calls_acc[i] for i in sorted(tool_calls_acc)
                    ]
                else:
                    result["content"] = content
                if reasoning:
                    result["reasoning_content"] = reasoning
                token_queue.put(("done", result))

            loop_obj = asyncio.get_event_loop()
            future = loop_obj.run_in_executor(None, _stream_llm)

            # Poll for tokens while LLM is running
            response = None
            while not future.done() or not token_queue.empty():
                while True:
                    try:
                        kind, data = token_queue.get_nowait()
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
                if not future.done():
                    await asyncio.sleep(0.02)

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
                # Merge multiple calls into one decompose=true (safety net)
                if len(tcs) > 1:
                    queries = []
                    for tc in tcs:
                        try:
                            a = json.loads(tc["function"]["arguments"])
                            q = a.get("raw_query", "")
                            if q:
                                queries.append(q)
                        except json.JSONDecodeError:
                            pass
                    if queries:
                        merged = ", ".join(queries)
                        logger.info("Merged %d tool_calls → 1 decompose=true: %r",
                                    len(tcs), merged[:200])
                        tcs = [{
                            "id": tcs[0].get("id", "call_1"),
                            "type": "function",
                            "function": {
                                "name": "search_knowledge_base",
                                "arguments": json.dumps({
                                    "raw_query": merged,
                                    "decompose": True,
                                }),
                            },
                        }]
                for tc in tcs:
                    if tc["function"]["name"] != "search_knowledge_base":
                        continue

                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {"raw_query": user_message, "generate_answer": True}

                    raw_query = args.get("raw_query", user_message)
                    generate_answer = args.get("generate_answer", True)
                    decompose = args.get("decompose", True)

                    # Emit tool_call_start
                    yield {
                        "type": "tool_call_start",
                        "tool": "search_knowledge_base",
                        "raw_query": raw_query,
                        "tool_call_id": tc.get("id", ""),
                    }

                    if self._agentic is None:
                        # No agentic service — skip tool execution
                        yield {
                            "type": "tool_result",
                            "status": "error",
                            "content": "Knowledge base search is not configured.",
                        }
                        tool_content = "Knowledge base search is not configured. Please enable Function Calling on an LLM model in Settings."
                    else:

                        step_queue: sync_queue.Queue = sync_queue.Queue()

                        def _on_step(event: dict):
                            if event.get("step") in (
                                "decompose", "task_start", "aq_start",
                                "aq_done", "synthesize_task", "synthesize_merge",
                                # Rewrite loop internals — per-AQ progress
                                "retrieve", "retrieving", "grading",
                                "rewriting", "rewrite_loop_done",
                            ):
                                step_queue.put(event)

                        # Run agentic service in thread pool (non-blocking)
                        loop = asyncio.get_event_loop()
                        future = loop.run_in_executor(
                            None,
                            lambda: self._agentic.run(
                                raw_query,
                                collections=collections or None,
                                generate_answer=generate_answer,
                                on_step=_on_step,
                                decompose=decompose,
                            ),
                        )

                        total_tool_calls += 1

                        # Stream events in real-time as they arrive
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
                            # Emit progressive summary after each batch
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

                        # Final summary
                        thinking_summary = _build_thinking_summary(all_thinking_events)
                        yield {"type": "thinking_summary", **thinking_summary}

                        # Collect sources
                        for chunk in result.all_chunks:
                            source = {
                                "text": getattr(chunk, "text", "")[:500],
                                "score": getattr(chunk, "score", 0.0),
                                "metadata": getattr(chunk, "metadata", {}),
                            }
                            if source not in all_sources:
                                all_sources.append(source)

                        # Yield tool_result
                        yield {
                            "type": "tool_result",
                            "status": "done",
                            "sources_count": len(all_sources),
                            "tool_call_id": tc.get("id", ""),
                        }

                        # Tool result content: use answer + context
                        tool_content_parts = []
                        if result.answer:
                            tool_content_parts.append(result.answer)
                        elif result.context:
                            tool_content_parts.append(result.context)
                        tool_content = "\n\n".join(tool_content_parts) if tool_content_parts else "No relevant information found."

                    # Inject assistant tool_call + tool result for next LLM round
                    tool_call_id = tc.get("id", "call_1")
                    tool_call_data = [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": "search_knowledge_base",
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
                if response.get("reasoning_content"):
                    meta["reasoning_content"] = response["reasoning_content"]
                # Persist tool messages for KV cache reuse in future rounds
                _persist_extra_messages(self._store, session_id, extra_messages)
                self._store.add_message(
                    session_id, "assistant", final_content,
                    sources=all_sources if all_sources else None,
                    metadata=meta,
                )

                yield {"type": "done", "sources": all_sources}
                return

        # Max rounds reached — save whatever we got
        yield {"type": "done", "sources": all_sources}

    # ── LLM call with tools ──────────────────────────────────────────

    def _call_llm_with_tools(self, messages: list[dict]) -> dict:
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

        kwargs = dict(
            model=model,
            messages=messages,
            temperature=0.1,
            tools=TOOLS,
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


def _persist_extra_messages(store, session_id: str, extra_messages: list[dict]) -> None:
    """Save tool_call and tool_result messages to DB for KV cache reuse."""
    for em in extra_messages:
        try:
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
    aq_rewrites: dict[str, list[str]] = {}
    aq_current_chunks: dict[str, int] = {}  # progressive chunk count
    status = ""

    for e in events:
        step = e.get("step", "")
        content = e.get("content", "")

        # Track latest activity for live status
        if step in ("decompose", "retrieving", "grading", "rewriting",
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
            aq_rewrites[aq_id] = []
            if task_key in tasks:
                tasks[task_key]["aqs"].append({
                    "aq_id": aq_id,
                    "query": content,
                    "iterations": 0,
                    "rewritten": [],
                    "final_chunks": 0,
                    "current_chunks": 0,
                    "sufficient": False,
                })

        elif step == "retrieving":
            aq_id = e.get("aq_id", "")
            # Parse progressive chunk count: "Retrieved N new chunks (total seen: M)"
            if "total seen: " in content:
                try:
                    total = int(content.split("total seen: ")[1].split(")")[0])
                    aq_current_chunks[aq_id] = total
                except (ValueError, IndexError):
                    pass

        elif step == "rewriting":
            aq_id = e.get("aq_id", "")
            if "Rewritten to:" not in content:
                continue  # skip placeholder events
            rewritten = content.split("Rewritten to:", 1)[1].strip().rstrip(")")
            aq_rewrites.setdefault(aq_id, []).append(rewritten)

        elif step == "aq_done":
            aq_id = e.get("aq_id", "")
            chunks = e.get("chunks", 0)
            sufficient = e.get("sufficient", False)
            for t in tasks.values():
                for aq in t["aqs"]:
                    if aq["aq_id"] == aq_id:
                        aq["final_chunks"] = chunks
                        aq["current_chunks"] = chunks
                        aq["sufficient"] = sufficient
                        aq["iterations"] = len(aq_rewrites.get(aq_id, [])) + 1
                        aq["rewritten"] = aq_rewrites.get(aq_id, [])
                        t["useful_chunks"] = sum(a["final_chunks"] for a in t["aqs"])
                        break

    # Apply progressive state (not yet finalized)
    for t in tasks.values():
        for aq in t["aqs"]:
            aid = aq["aq_id"]
            if aid in aq_current_chunks and aq["final_chunks"] == 0:
                aq["current_chunks"] = aq_current_chunks[aid]
            # Show rewrites as they happen, not just at aq_done
            if aid in aq_rewrites and not aq["rewritten"]:
                aq["rewritten"] = aq_rewrites[aid]
                aq["iterations"] = len(aq_rewrites[aid]) + 1

    return {
        "aq_count": total_aqs,
        "task_count": len(tasks),
        "tasks": [tasks[t] for t in task_order],
        "status": status,
    }
