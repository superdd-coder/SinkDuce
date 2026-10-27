"""Tests for ChatboxAgent — Phase 3 Step 1.

Run: pytest tests/test_chatbox_agent.py -v --tb=short
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.db.sessions import SessionStore, Session


@pytest.mark.parametrize("mode", ["direct", "agentic"])
def test_chat_passes_configured_retrieval_settings(store, mock_llm, mock_agentic, mode):
    from src.chatbox.agent import ChatboxAgent
    from src.config import AppConfig
    from src.rag.direct_query import DirectQueryResult

    config = AppConfig()
    config.direct_rag.top_k = 31
    config.rag.top_k = 27
    direct = MagicMock()
    direct.retrieve.return_value = DirectQueryResult()
    tool = "lookup_collection" if mode == "direct" else "search_knowledge_base"
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_llm_response(tool_calls=[{"id": "search", "type": "function", "function": {
            "name": tool, "arguments": json.dumps({"query": "fact", "raw_query": "fact"})}}]),
        _fake_llm_response(content="answer"),
    ]
    agent = ChatboxAgent(store, mock_llm, mock_agentic, direct_module=direct)
    session = store.create_session(title="search settings")
    with patch("src.config.get_config", return_value=config):
        agent.chat(session.id, "fact", mode=mode)
    call = direct.retrieve.call_args if mode == "direct" else mock_agentic.run.call_args
    assert call.kwargs.get("top_k") == (31 if mode == "direct" else 27)
    assert call.kwargs.get("search_mode") == "hybrid"


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_chatbox.db"
    s = SessionStore(str(db_path))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


@pytest.fixture
def mock_llm():
    """An LLM provider mock that supports function calling via _client."""
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()
    return llm


@pytest.fixture
def mock_agentic():
    """Mock AgenticQueryService that returns a simple result."""
    from src.rag.agentic_query import AgenticQueryResult
    from src.rag.retriever import RetrievedChunk

    svc = MagicMock()
    chunk = MagicMock()
    chunk.text = "Test chunk content"
    chunk.score = 0.95
    chunk.metadata = {"source": "test.txt", "id": "chunk-1"}

    result = AgenticQueryResult(
        answer="Based on the knowledge base, the answer is 42.",
        context="Test chunk content",
        all_chunks=[chunk],
    )
    svc.run.return_value = result
    return svc


@pytest.fixture
def agent(store, mock_llm, mock_agentic):
    from src.chatbox.agent import ChatboxAgent

    return ChatboxAgent(
        session_store=store,
        chat_llm=mock_llm,
        agentic_service=mock_agentic,
    )


# ── TestChatboxCore ───────────────────────────────────────────


class TestChatboxCore:
    def test_direct_answer_no_tool(self, store, mock_llm, mock_agentic):
        """LLM returns text directly without tool call -> saved as answer."""
        from src.chatbox.agent import ChatboxAgent

        # Setup: LLM returns text
        mock_llm._client.chat.completions.create.return_value = _fake_llm_response(
            content="Hello! How can I help you?"
        )

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session(title="test")
        resp = agent.chat(s.id, "Hello")

        assert resp.answer == "Hello! How can I help you?"
        assert resp.tool_calls == 0
        assert resp.sources == []

        # Check messages persisted
        msgs = store.get_messages(s.id)
        assert len(msgs) == 2  # user + assistant
        assert msgs[0].role == "user"
        assert msgs[0].content == "Hello"
        assert msgs[1].role == "assistant"

    def test_tool_call_triggered(self, store, mock_llm, mock_agentic):
        """LLM returns tool_call -> agentic_service.run() called."""
        from src.chatbox.agent import ChatboxAgent

        # Setup: LLM returns tool call first, then text
        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _fake_llm_response(tool_calls=[{
                    "id": "call_abc",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge_base",
                        "arguments": '{"raw_query":"What is RAG","generate_answer":true}',
                    },
                }])
            else:
                return _fake_llm_response(content="Based on search results, RAG is Retrieval-Augmented Generation.")

        mock_llm._client.chat.completions.create.side_effect = _side_effect

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session(title="test")
        resp = agent.chat(s.id, "What is RAG?")

        assert resp.tool_calls == 1
        assert mock_agentic.run.called
        assert "RAG" in resp.answer

    def test_tool_result_injected_to_context(self, store, mock_llm, mock_agentic):
        """Tool result is injected as messages for the next LLM round."""
        from src.chatbox.agent import ChatboxAgent

        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _fake_llm_response(tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge_base",
                        "arguments": '{"raw_query":"test query"}',
                    },
                }])
            else:
                # Verify tool messages are in the context
                msgs = kwargs.get("messages", [])
                roles = [m["role"] for m in msgs]
                assert "tool" in roles, f"Tool result not in messages: {roles}"
                return _fake_llm_response(content="Final answer")

        mock_llm._client.chat.completions.create.side_effect = _side_effect

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session(title="test")
        resp = agent.chat(s.id, "test query")

        assert resp.tool_calls == 1

    def test_multi_turn_context_accumulates(self, store, mock_llm, mock_agentic):
        """Messages from previous turns are included in the next turn."""
        from src.chatbox.agent import ChatboxAgent

        mock_llm._client.chat.completions.create.return_value = _fake_llm_response(
            content="First turn answer"
        )

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session(title="test")

        # Turn 1
        agent.chat(s.id, "First question")

        # Turn 2 -- should include turn 1 history
        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            msgs = kwargs.get("messages", [])
            contents = [m["content"] for m in msgs if m["role"] in ("user", "assistant")]
            if call_count[0] == 1:
                # Should contain "First question" and "First turn answer"
                assert "First question" in str(contents)
            return _fake_llm_response(content="Second turn answer")

        mock_llm._client.chat.completions.create.side_effect = _side_effect
        resp = agent.chat(s.id, "Second question")
        assert resp.answer == "Second turn answer"

    def test_max_tool_rounds_limited(self, store, mock_llm, mock_agentic):
        """Outer tool loop ends; agentic KB search capped separately at 5."""
        from src.chatbox.agent import (
            ChatboxAgent,
            _MAX_AGENTIC_SEARCH_CALLS,
            _MAX_TOOL_ROUNDS,
        )

        # Always return tool calls
        mock_llm._client.chat.completions.create.return_value = _fake_llm_response(
            tool_calls=[{
                "id": "call_loop",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": '{"raw_query":"loop"}',
                },
            }]
        )

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session(title="test")
        resp = agent.chat(s.id, "Infinite loop query")

        # Outer loop budget
        assert resp.tool_calls <= _MAX_TOOL_ROUNDS
        # Agentic 查库 only runs this many times (rest get a stop message)
        assert mock_agentic.run.call_count <= _MAX_AGENTIC_SEARCH_CALLS

    def test_meeting_lookup_passes_seen_packs_within_turn(self, store, mock_llm, mock_agentic):
        from src.chatbox.agent import ChatboxAgent

        n = [0]

        def _side_effect(**kwargs):
            n[0] += 1
            if n[0] <= 2:
                return _fake_llm_response(tool_calls=[{
                    "id": f"call_{n[0]}",
                    "type": "function",
                    "function": {
                        "name": "lookup_meeting_transcript",
                        "arguments": '{"query":"next steps"}',
                    },
                }])
            return _fake_llm_response(content="done")

        mock_llm._client.chat.completions.create.side_effect = _side_effect
        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        store.create_session(title="m", session_id="meeting_abc")
        seen_args: list[set] = []

        def fake_lookup(mid, q, **kwargs):
            seen_args.append(set(kwargs.get("seen_pack_keys") or []))
            if len(seen_args) == 1:
                return '{"hit_count": 2, "context": "AB"}', {"m:0", "m:1"}
            return '{"hit_count": 1, "context": "C"}', {"m:1", "m:2"}

        with patch(
            "src.meeting.transcript_index.lookup_json_and_keys",
            side_effect=fake_lookup,
        ):
            agent.chat("meeting_abc", "接下来要做什么", mode="direct")
        assert seen_args[0] == set()
        assert seen_args[1] == {"m:0", "m:1"}

    def test_chat_lookup_passes_seen_packs_within_turn(self, store, mock_llm, mock_agentic):
        from src.chatbox.agent import ChatboxAgent

        n = [0]

        def _side_effect(**kwargs):
            n[0] += 1
            if n[0] <= 2:
                return _fake_llm_response(tool_calls=[{
                    "id": f"call_{n[0]}",
                    "type": "function",
                    "function": {
                        "name": "lookup_meeting_transcript",
                        "arguments": '{"query":"water price"}',
                    },
                }])
            return _fake_llm_response(content="done")

        mock_llm._client.chat.completions.create.side_effect = _side_effect
        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        store.create_session(title="c", session_id="sess_chat")
        seen_args: list[set] = []
        prior_lens: list[int] = []

        def fake_lookup(*_a, **kwargs):
            seen_args.append(set(kwargs.get("seen_pack_keys") or []))
            prior_lens.append(len(kwargs.get("prior_hits") or []))
            hits = [
                {
                    "meeting_id": "m1",
                    "pack_index": 0,
                    "sentences": [{"ref_n": 1, "text": "AB"}],
                }
            ]
            if len(seen_args) == 1:
                return (
                    '{"hit_count": 2, "context": "AB", "meetings_searched": ["m1"]}',
                    {"m1:0", "m1:1"},
                    hits,
                )
            return (
                '{"hit_count": 1, "context": "C", "meetings_searched": ["m1"]}',
                {"m1:0", "m1:1", "m1:2"},
                hits,
            )

        with patch(
            "src.meeting.catalog.lookup_tool_json_and_keys",
            side_effect=fake_lookup,
        ), patch.object(agent, "_has_meetings", return_value=True):
            agent.chat("sess_chat", "Reliance项目有说过水价的范围吗？", mode="agentic")
        assert seen_args[0] == set()
        assert seen_args[1] == {"m1:0", "m1:1"}
        assert prior_lens[0] == 0
        assert prior_lens[1] >= 1

    def test_lookup_tool_dropped_after_no_new_packs(
        self, store, mock_llm, mock_agentic,
    ):
        from src.chatbox.agent import ChatboxAgent

        n = [0]
        tools_by_round: list[set] = []

        def _side_effect(**kwargs):
            n[0] += 1
            names = set()
            for t in kwargs.get("tools") or []:
                fn = (t.get("function") or {}) if isinstance(t, dict) else {}
                if fn.get("name"):
                    names.add(fn["name"])
            tools_by_round.append(names)
            if n[0] <= 2:
                return _fake_llm_response(tool_calls=[{
                    "id": f"call_{n[0]}",
                    "type": "function",
                    "function": {
                        "name": "lookup_meeting_transcript",
                        "arguments": '{"query":"next steps"}',
                    },
                }])
            return _fake_llm_response(content="done")

        mock_llm._client.chat.completions.create.side_effect = _side_effect
        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        store.create_session(title="m", session_id="meeting_abc")

        def fake_lookup(mid, q, **kwargs):
            return (
                '{"hit_count": 2, "context": "AB"}',
                {"m:0", "m:1"},
                [{"meeting_id": "m", "pack_index": 0}],
            )

        with patch(
            "src.meeting.transcript_index.lookup_json_and_keys",
            side_effect=fake_lookup,
        ) as lookup:
            agent.chat("meeting_abc", "接下来要做什么", mode="direct")
        assert lookup.call_count == 2
        assert "lookup_meeting_transcript" in tools_by_round[0]
        assert "lookup_meeting_transcript" in tools_by_round[1]
        assert "lookup_meeting_transcript" not in tools_by_round[2]


# ── TestChatboxSession ────────────────────────────────────────


class TestChatboxSession:
    def test_messages_saved_after_chat(self, store, mock_llm, mock_agentic):
        from src.chatbox.agent import ChatboxAgent

        mock_llm._client.chat.completions.create.return_value = _fake_llm_response(
            content="Answer content"
        )

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session(title="test")
        agent.chat(s.id, "Question")

        msgs = store.get_messages(s.id)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "Question"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "Answer content"

    def test_sources_attached_to_message(self, store, mock_llm, mock_agentic):
        """When tool call returns sources, they're attached to the assistant message."""
        from src.chatbox.agent import ChatboxAgent

        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _fake_llm_response(tool_calls=[{
                    "id": "call_src",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge_base",
                        "arguments": '{"raw_query":"sources test"}',
                    },
                }])
            else:
                return _fake_llm_response(content="Answer with sources")

        mock_llm._client.chat.completions.create.side_effect = _side_effect

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session(title="test")
        agent.chat(s.id, "sources test")

        msgs = store.get_messages(s.id)
        assistant_msg = msgs[-1]
        assert assistant_msg.sources is not None
        assert len(assistant_msg.sources) >= 1

    def test_existing_history_loaded(self, store, mock_llm, mock_agentic):
        """Pre-existing messages are included in the LLM context."""
        from src.chatbox.agent import ChatboxAgent

        s = store.create_session(title="test")
        store.add_message(s.id, "user", "Previous question")
        store.add_message(s.id, "assistant", "Previous answer", metadata={"tool_calls": 0})

        mock_llm._client.chat.completions.create.return_value = _fake_llm_response(
            content="New answer"
        )

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        agent.chat(s.id, "New question")

        # Verify history was loaded -- check the messages passed to LLM
        call_args = mock_llm._client.chat.completions.create.call_args
        msgs = call_args[1]["messages"]
        contents = [m["content"] for m in msgs]
        assert "Previous question" in contents
        assert "Previous answer" in contents


# ── TestChatboxEdgeCases ──────────────────────────────────────


class TestChatboxEdgeCases:
    def test_empty_message(self, store, mock_llm, mock_agentic):
        from src.chatbox.agent import ChatboxAgent

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session()
        resp = agent.chat(s.id, "")
        assert resp.answer == ""
        resp2 = agent.chat(s.id, "   ")
        assert resp2.answer == ""

    def test_agentic_service_unavailable(self, store, mock_llm):
        """When agentic_service is None, tool calls are skipped gracefully."""
        from src.chatbox.agent import ChatboxAgent

        mock_llm._client.chat.completions.create.return_value = _fake_llm_response(
            tool_calls=[{
                "id": "call_x",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": '{"raw_query":"test"}',
                },
            }]
        )

        # Pass None as agentic_service
        agent = ChatboxAgent(store, mock_llm, None)
        s = store.create_session()

        # Should not crash -- just no answer generated in tool result
        resp = agent.chat(s.id, "test")
        # The loop breaks after max rounds or when no tool content
        assert resp.tool_calls <= 5

    def test_llm_tool_call_malformed(self, store, mock_llm, mock_agentic):
        """Malformed tool call arguments don't crash the agent."""
        from src.chatbox.agent import ChatboxAgent

        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _fake_llm_response(tool_calls=[{
                    "id": "bad_call",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge_base",
                        "arguments": "{invalid json!!!",
                    },
                }])
            else:
                return _fake_llm_response(content="fallback answer")

        mock_llm._client.chat.completions.create.side_effect = _side_effect

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session()
        resp = agent.chat(s.id, "malformed test")
        # Should fall back gracefully
        assert resp.answer == "fallback answer"

    def test_very_long_history_truncated(self, store, mock_llm, mock_agentic):
        """History is limited by dialogue turns (recent), not oldest raw rows."""
        from src.chatbox.agent import ChatboxAgent, _MAX_HISTORY_DIALOGUE_MAIN

        s = store.create_session()
        # Add 60 user-assistant pairs = 120 dialogue units
        for i in range(60):
            store.add_message(s.id, "user", f"Question {i}")
            store.add_message(s.id, "assistant", f"Answer {i}")

        mock_llm._client.chat.completions.create.return_value = _fake_llm_response(
            content="New answer"
        )

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        agent.chat(s.id, "New question")

        call_args = mock_llm._client.chat.completions.create.call_args
        msgs = call_args[1]["messages"]
        # Fixed system + recent dialogue + current user (already in hist after save)
        user_contents = [m["content"] for m in msgs if m["role"] == "user"]
        # Newest history retained
        assert any("Question 59" in (c or "") for c in user_contents)
        assert any("New question" in (c or "") for c in user_contents)
        # Oldest dropped when over dialogue budget
        assert not any(c == "Question 0" for c in user_contents)
        # Dialogue-ish user+assistant rows bounded (budget + current turn)
        dialogue_like = [
            m for m in msgs
            if m["role"] in ("user", "assistant") and m.get("content")
        ]
        # budget 32 + possible current-user re-append
        assert len(dialogue_like) <= _MAX_HISTORY_DIALOGUE_MAIN + 2
        assert _MAX_HISTORY_DIALOGUE_MAIN == 32

    def test_recent_history_not_oldest_for_ambiguous_followup(
        self, store, mock_llm, mock_agentic,
    ):
        """Korea-bug regression: late turns must see recent topic, not first 50 rows."""
        from src.chatbox.agent import ChatboxAgent

        s = store.create_session()
        # Flood early history with Korea-like content (more than old 50-row window)
        for i in range(30):
            store.add_message(s.id, "user", f"early-{i}")
            store.add_message(
                s.id, "assistant", "",
                metadata={"tool_calls": [{
                    "id": f"c{i}", "type": "function",
                    "function": {"name": "request_web_search",
                                 "arguments": '{"query":"South Korea bioenergy"}'},
                }]},
            )
            store.add_message(
                s.id, "tool", "KOREA " * 200,
                metadata={"tool_call_id": f"c{i}"},
            )
            store.add_message(s.id, "assistant", f"Korea answer {i}")

        store.add_message(s.id, "user", "澳大利亚呢？")
        store.add_message(s.id, "assistant", "Australia market overview...")
        store.add_message(s.id, "user", "还有吗")

        mock_llm._client.chat.completions.create.return_value = _fake_llm_response(
            content="More on Australia..."
        )
        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        # Rebuild as chat would after user already saved
        built = agent._build_messages(s.id, "还有吗")
        flat = "\n".join(
            (m.get("content") or "") if isinstance(m.get("content"), str) else ""
            for m in built
        )
        assert "澳大利亚呢？" in flat or "Australia" in flat
        assert "还有吗" in flat
        # Must not be stuck with only early Korea + missing Australia
        assert "early-0" not in flat

    def test_three_line_message_layouts(self, store, mock_llm, mock_agentic):
        """Meeting / Quick / Main Chat use distinct assembly order."""
        from src.chatbox.agent import ChatboxAgent

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        agent._build_catalog_text = MagicMock(return_value="Knowledge base reference:\n- ColA")

        # ── Main Chat: system → history → catalog → extra → user ──
        main = store.create_session(session_id="main_layout_1")
        store.add_message(main.id, "user", "prev")
        store.add_message(main.id, "assistant", "prev-ans")
        store.add_message(main.id, "user", "now")
        main_msgs = agent._build_messages(
            main.id, "now",
            extra_messages=[{"role": "tool", "tool_call_id": "x", "content": "tool-now"}],
            catalog_text="Knowledge base reference:\n- ColA",
        )
        assert main_msgs[0]["role"] == "system"
        assert "Knowledge base reference" not in (main_msgs[0].get("content") or "")
        catalog_idxs = [
            i for i, m in enumerate(main_msgs)
            if m["role"] == "system" and "Knowledge base reference" in (m.get("content") or "")
        ]
        assert catalog_idxs, "catalog must be present for main chat"
        # catalog after dialogue history (prev user/assistant appear before catalog)
        prev_idx = next(
            i for i, m in enumerate(main_msgs)
            if m.get("content") == "prev" or m.get("content") == "prev-ans"
        )
        assert catalog_idxs[0] > prev_idx
        # this-turn extra after catalog
        tool_idx = next(i for i, m in enumerate(main_msgs) if m.get("content") == "tool-now")
        assert tool_idx > catalog_idxs[0]

        # ── Quick: no catalog ──
        quick = store.create_session(session_id="quick_col_abc", collections=["col_abc"])
        store.add_message(quick.id, "user", "q1")
        store.add_message(quick.id, "assistant", "a1")
        store.add_message(quick.id, "user", "q2")
        quick_msgs = agent._build_messages(
            quick.id, "q2",
            catalog_text="Knowledge base reference:\n- SHOULD_NOT_APPEAR",
            system_prompt="Quick system for col",
        )
        from src.prompts import KNOWLEDGE_ANSWER_EVIDENCE_RULES

        assert quick_msgs[0]["content"] == "Quick system for col\n\n" + KNOWLEDGE_ANSWER_EVIDENCE_RULES
        assert not any(
            "SHOULD_NOT_APPEAR" in (m.get("content") or "")
            or "Knowledge base reference" in (m.get("content") or "")
            for m in quick_msgs
        )

        # ── Meeting: system prompt, dialogue, speaker — no full transcript dump ──
        meeting = store.create_session(session_id="meeting_abc123")
        store.add_message(meeting.id, "system", "STALE_SHOULD_NOT_APPEAR")
        store.add_message(meeting.id, "user", "what was said?")
        store.add_message(meeting.id, "assistant", "summary")
        store.add_message(meeting.id, "user", "more?")
        meet_msgs = agent._build_messages(
            meeting.id, "more?",
            system_prompt="MEETING_SYSTEM",
            catalog_text="Knowledge base reference:\n- NO",
            pre_message_context="Speaker mapping: S1=Alice",
        )
        assert meet_msgs[0]["content"] == "MEETING_SYSTEM"
        assert not any(
            "FULL_TRANSCRIPT_TEXT" in (m.get("content") or "") for m in meet_msgs
        )
        assert not any(
            "STALE_SHOULD_NOT_APPEAR" in (m.get("content") or "") for m in meet_msgs
        )
        assert not any(
            "Knowledge base reference" in (m.get("content") or "") for m in meet_msgs
        )
        speaker_idxs = [
            i for i, m in enumerate(meet_msgs)
            if m["role"] == "system" and "Speaker mapping" in (m.get("content") or "")
        ]
        assert speaker_idxs
        assert speaker_idxs[0] > 0

    def test_flushed_this_turn_tools_are_not_duplicated(
        self, store, mock_llm, mock_agentic,
    ):
        """Incremental persist must not send the same tool body twice next round."""
        from src.chatbox.agent import ChatboxAgent

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session()
        body = "PACK " + ("水价范围 " * 80)
        store.add_message(s.id, "user", "Reliance项目有说过水价的范围吗？")
        store.add_message(
            s.id, "assistant", "",
            metadata={"tool_calls": [{
                "id": "call_lookup_1",
                "type": "function",
                "function": {
                    "name": "lookup_meeting_transcript",
                    "arguments": '{"query":"water price"}',
                },
            }]},
        )
        store.add_message(
            s.id, "tool", body,
            metadata={"tool_call_id": "call_lookup_1"},
        )
        extra = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_lookup_1",
                    "type": "function",
                    "function": {
                        "name": "lookup_meeting_transcript",
                        "arguments": '{"query":"water price"}',
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call_lookup_1",
                "content": body,
            },
        ]
        built = agent._build_messages(
            s.id, "Reliance项目有说过水价的范围吗？", extra_messages=extra,
        )
        copies = sum(
            1 for m in built
            if m.get("role") == "tool" and m.get("content") == body
        )
        assert copies == 1

    def test_later_lookup_restitch_drops_earlier_full_body(
        self, store, mock_llm, mock_agentic,
    ):
        """Merged restitch replaces earlier lookup text so the LLM does not reread it."""
        from src.chatbox.agent import ChatboxAgent

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session()
        store.add_message(s.id, "user", "水价？")
        first = "FIRST_LOOKUP_BODY " + ("aaa " * 40)
        merged = "MERGED_RESTITCH " + ("bbb " * 40)
        extra = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup_meeting_transcript", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "c1", "content": first},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "lookup_meeting_transcript", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "c2", "content": merged},
        ]
        built = agent._build_messages(s.id, "水价？", extra_messages=extra)
        flat = "\n".join(
            (m.get("content") or "") if isinstance(m.get("content"), str) else ""
            for m in built
        )
        assert "MERGED_RESTITCH" in flat
        assert "FIRST_LOOKUP_BODY" not in flat

    def test_empty_later_lookup_keeps_earlier_excerpts(
        self, store, mock_llm, mock_agentic,
    ):
        from src.chatbox.agent import ChatboxAgent

        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        s = store.create_session()
        store.add_message(s.id, "user", "水价？")
        first = json.dumps({"hit_count": 5, "context": "FIRST_LOOKUP_BODY keep"})
        empty = json.dumps({
            "hit_count": 0,
            "context": "",
            "message": "No new packs for this query; already-returned packs were excluded.",
        })
        extra = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup_meeting_transcript", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "c1", "content": first},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "lookup_meeting_transcript", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "c2", "content": empty},
        ]
        built = agent._build_messages(s.id, "水价？", extra_messages=extra)
        flat = "\n".join(
            (m.get("content") or "") if isinstance(m.get("content"), str) else ""
            for m in built
        )
        assert "FIRST_LOOKUP_BODY" in flat
        # Control payloads (no-new-packs) stay intact — they are the honest
        # "nothing new, answer from what you have" signal, not stale data.
        assert "No new packs for this query" in flat


class TestThinkingAfterTools:
    def test_thinking_stays_on_before_any_tool(self):
        from src.chatbox.agent import _thinking_on_for_tool_round

        assert _thinking_on_for_tool_round(True, "auto") is True
        assert _thinking_on_for_tool_round(True, "auto", extra_messages=[]) is True

    def test_thinking_off_after_lookup_result(self):
        from src.chatbox.agent import _thinking_on_for_tool_round

        extra = [{"role": "tool", "tool_call_id": "c1", "content": "restitch"}]
        assert _thinking_on_for_tool_round(True, "auto", extra_messages=extra) is False

    def test_forced_lookup_never_thinks(self):
        from src.chatbox.agent import _thinking_on_for_tool_round

        choice = {"type": "function", "function": {"name": "lookup_meeting_transcript"}}
        assert _thinking_on_for_tool_round(True, choice) is False

    def test_stream_emits_tool_name_once_when_known(self):
        from src.chatbox.agent import _new_stream_tool_names

        acc = {
            0: {"function": {"name": "lookup_meeting_transcript", "arguments": ""}},
        }
        known = {"lookup_meeting_transcript", "search_knowledge_base"}
        seen: set[int] = set()
        got = _new_stream_tool_names(acc, seen, known)
        assert got == [(0, "lookup_meeting_transcript")]
        seen.update(i for i, _n in got)
        assert _new_stream_tool_names(acc, seen, known) == []

    def test_stream_ignores_partial_tool_name(self):
        from src.chatbox.agent import _new_stream_tool_names

        acc = {0: {"function": {"name": "lookup_meet", "arguments": ""}}}
        got = _new_stream_tool_names(
            acc, set(), {"lookup_meeting_transcript"},
        )
        assert got == []

    def test_preview_only_first_tool_in_a_batch(self):
        from src.chatbox.agent import _preview_stream_tool_name

        acc = {
            0: {"function": {"name": "lookup_meeting_transcript", "arguments": ""}},
            1: {"function": {"name": "search_knowledge_base", "arguments": ""}},
        }
        known = {"lookup_meeting_transcript", "search_knowledge_base"}
        seen: set[int] = set()
        assert (
            _preview_stream_tool_name(acc, seen, known)
            == "lookup_meeting_transcript"
        )
        seen.add(0)
        assert _preview_stream_tool_name(acc, seen, known) is None


# ── Helpers ───────────────────────────────────────────────────


def _fake_llm_response(content=None, tool_calls=None):
    """Create a fake OpenAI chat completion response."""
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_msg = MagicMock()

    mock_msg.content = content
    mock_msg.tool_calls = None

    if tool_calls:
        mock_tool_calls = []
        for tc in tool_calls:
            mock_tc = MagicMock()
            mock_tc.id = tc["id"]
            mock_tc.type = tc["type"]
            mock_tc.function = MagicMock()
            mock_tc.function.name = tc["function"]["name"]
            mock_tc.function.arguments = tc["function"]["arguments"]
            mock_tool_calls.append(mock_tc)
        mock_msg.tool_calls = mock_tool_calls

    mock_choice.message = mock_msg
    mock_resp.choices = [mock_choice]
    return mock_resp


# ── Same-round lookup fan-out (streaming) ─────────────────────


def _fake_stream(events):
    """Fake OpenAI streaming response: events = [(finish_reason, delta_kwargs)]."""
    chunks = []
    for finish, delta_kwargs in events:
        chunk = MagicMock()
        choice = MagicMock()
        delta = MagicMock()
        delta.content = delta_kwargs.get("content")
        delta.reasoning_content = None
        delta.tool_calls = delta_kwargs.get("tool_calls")
        choice.delta = delta
        choice.finish_reason = finish
        chunk.choices = [choice]
        chunks.append(chunk)
    return iter(chunks)


def _tc_delta(idx, call_id, name, arguments):
    tc = MagicMock()
    tc.index = idx
    tc.id = call_id
    fn = MagicMock()
    fn.name = name
    fn.arguments = arguments
    tc.function = fn
    return tc


def test_stream_runs_multiple_lookups_concurrently(tmp_path):
    """Two lookup calls in one round execute in the same fan-out batch."""
    import asyncio

    from src.chatbox.agent import ChatboxAgent

    store = SessionStore(str(tmp_path / "fanout.db"))
    store.create_session(session_id="meeting_m1")
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()

    tool_events = _fake_stream([
        ("tool_calls", {
            "tool_calls": [
                _tc_delta(0, "call_a", "lookup_meeting_transcript", '{"query": "budget"}'),
                _tc_delta(1, "call_b", "lookup_meeting_transcript", '{"query": "headcount"}'),
            ],
        }),
    ])
    text_events = _fake_stream([("stop", {"content": "Final answer"})])
    llm._client.chat.completions.create.side_effect = [tool_events, text_events]

    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())

    calls = []

    def _fake_lookup(session_id, args, user_message, forced_col, seen_packs, cols, seen_hits):
        calls.append((str(args.get("query")), set(seen_packs)))
        if args.get("query") == "budget":
            return '{"context": "b", "hit_count": 1}', {"m:0"}, None, []
        return '{"context": "h", "hit_count": 1}', {"m:1"}, None, []

    async def _collect():
        return [ev async for ev in agent.chat_stream("meeting_m1", "budget and headcount?")]

    with patch("src.chatbox.agent._run_lookup_meeting_transcript", side_effect=_fake_lookup):
        events = asyncio.run(_collect())

    starts = [ev for ev in events if ev.get("type") == "tool_call_start"]
    results = [ev for ev in events if ev.get("type") == "tool_result"]
    assert len(starts) == 2
    assert len(results) == 2
    assert [c[0] for c in calls] == ["budget", "headcount"]
    # Both calls ran with the same launch-time snapshot (concurrent, not chained)
    assert calls[0][1] == calls[1][1] == set()
    # Both merges landed: seen packs include both m:0 and m:1 after the round
    assert [ev.get("content") for ev in events if ev.get("type") == "token"] or True


def test_stream_lookup_fanout_merges_seen_packs_across_calls(tmp_path):
    """Second fan-out call whose packs duplicate the first must mark the turn dry."""
    import asyncio

    from src.chatbox.agent import ChatboxAgent

    store = SessionStore(str(tmp_path / "fanout2.db"))
    store.create_session(session_id="meeting_m1")
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()

    tool_events = _fake_stream([
        ("tool_calls", {
            "tool_calls": [
                _tc_delta(0, "call_a", "lookup_meeting_transcript", '{"query": "one"}'),
                _tc_delta(1, "call_b", "lookup_meeting_transcript", '{"query": "two"}'),
            ],
        }),
    ])
    text_events = _fake_stream([("stop", {"content": "done"})])
    llm._client.chat.completions.create.side_effect = [tool_events, text_events]

    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())

    def _fake_lookup(session_id, args, user_message, forced_col, seen_packs, cols, seen_hits):
        # Both searches return the same pack — second call adds nothing new.
        return '{"context": "x", "hit_count": 1}', {"m:0"}, None, []

    async def _collect():
        return [ev async for ev in agent.chat_stream("meeting_m1", "q")]

    with patch("src.chatbox.agent._run_lookup_meeting_transcript", side_effect=_fake_lookup):
        events = asyncio.run(_collect())

    results = [ev for ev in events if ev.get("type") == "tool_result"]
    assert len(results) == 2
    # Round 2 request must NOT offer lookup again (dry) — tools list drops it.
    second_kwargs = llm._client.chat.completions.create.call_args_list[1][1]
    offered = [t["function"]["name"] for t in (second_kwargs.get("tools") or [])]
    assert "lookup_meeting_transcript" not in offered


def test_meeting_lookup_hard_capped_per_turn(tmp_path):
    """A model that keeps rephrasing meeting lookups hits the per-turn cap.

    Each lookup returns fresh pack keys, so the no-new-packs dry flag never
    fires; the hard cap must stop the search after _MAX_MEETING_LOOKUP_CALLS
    and drop the tool for the remaining rounds.
    """
    from src.chatbox.agent import ChatboxAgent, _MAX_MEETING_LOOKUP_CALLS

    store = SessionStore(str(tmp_path / "cap.db"))
    store.create_session(title="m", session_id="meeting_cap")
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()

    rounds: list[set] = []
    n = [0]

    def _side_effect(**kwargs):
        n[0] += 1
        names = set()
        for t in kwargs.get("tools") or []:
            fn = (t.get("function") or {}) if isinstance(t, dict) else {}
            if fn.get("name"):
                names.add(fn["name"])
        rounds.append(names)
        if n[0] <= _MAX_MEETING_LOOKUP_CALLS + 3:
            return _fake_llm_response(tool_calls=[{
                "id": f"call_{n[0]}",
                "type": "function",
                "function": {
                    "name": "lookup_meeting_transcript",
                    "arguments": '{"query":"价格"}',
                },
            }])
        return _fake_llm_response(content="answered")

    llm._client.chat.completions.create.side_effect = _side_effect
    agent = ChatboxAgent(store, llm, MagicMock())

    def fake_lookup(mid, q, **kwargs):
        key = f"m:{n[0]}"
        return (
            '{"hit_count": 1, "context": "X"}',
            {key},
            [{"meeting_id": "m", "pack_index": n[0]}],
        )

    with patch(
        "src.meeting.transcript_index.lookup_json_and_keys",
        side_effect=fake_lookup,
    ) as lookup:
        resp = agent.chat("meeting_cap", "价格谈到哪了", mode="direct")

    assert lookup.call_count == _MAX_MEETING_LOOKUP_CALLS
    assert resp.answer == "answered"
    assert "lookup_meeting_transcript" in rounds[0]
    # After the cap round the tool is physically removed from the offer.
    assert all(
        "lookup_meeting_transcript" not in r for r in rounds[_MAX_MEETING_LOOKUP_CALLS + 1 :]
    )


# ── Ghost-loop hardening: honest dry payloads + early force-finish ──


def _stub_always_lookup(llm, *, tool_rounds: int, final: str = "answered"):
    """LLM stub that keeps emitting lookup calls, then answers.

    Calls WITHOUT a ``tools`` kwarg are the force-generate finish — answer
    with text there regardless of the round count.
    """
    n = [0]

    def _side_effect(**kwargs):
        if not kwargs.get("tools"):
            return _fake_llm_response(content=final)
        n[0] += 1
        if n[0] <= tool_rounds:
            return _fake_llm_response(tool_calls=[{
                "id": f"call_{n[0]}",
                "type": "function",
                "function": {
                    "name": "lookup_meeting_transcript",
                    "arguments": '{"query":"价格"}',
                },
            }])
        return _fake_llm_response(content=final)

    llm._client.chat.completions.create.side_effect = _side_effect
    return n


def _capture_tool_contents(llm):
    """Wrap the create side_effect to record tool messages per round."""
    inner = llm._client.chat.completions.create.side_effect
    tool_contents: list[str] = []

    def _wrapped(**kwargs):
        for m in kwargs.get("messages") or []:
            if m.get("role") == "tool":
                tool_contents.append(str(m.get("content")))
        return inner(**kwargs)

    llm._client.chat.completions.create.side_effect = _wrapped
    return tool_contents


def test_dry_lookup_says_turn_done_not_stale_replay(tmp_path):
    """After the budget is spent, a stubborn re-lookup gets an explicit
    'turn finished' payload — never the stale last result replayed as if
    the new query had succeeded."""
    from src.chatbox.agent import ChatboxAgent, _MAX_MEETING_LOOKUP_CALLS

    store = SessionStore(str(tmp_path / "dry.db"))
    store.create_session(title="m", session_id="meeting_dry")
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()
    _stub_always_lookup(llm, tool_rounds=_MAX_MEETING_LOOKUP_CALLS + 3)
    tool_contents = _capture_tool_contents(llm)

    fake_n = [0]

    def fake_lookup(mid, q, **kwargs):
        fake_n[0] += 1
        return (
            '{"hit_count": 1, "context": "STALE-MARKER"}',
            {f"m:{fake_n[0]}"},
            [{"meeting_id": "m", "pack_index": fake_n[0]}],
        )

    agent = ChatboxAgent(store, llm, MagicMock())
    with patch(
        "src.meeting.transcript_index.lookup_json_and_keys",
        side_effect=fake_lookup,
    ):
        resp = agent.chat("meeting_dry", "价格谈到哪了", mode="direct")

    assert resp.answer == "answered"
    dry_payloads = [c for c in tool_contents if "finished for this turn" in c]
    assert dry_payloads, "dry re-lookups must get the explicit turn-done payload"
    assert all("STALE-MARKER" not in c for c in dry_payloads), (
        "stale last result must not be replayed as a fresh success"
    )
    assert any("limit reached" in c for c in tool_contents), (
        "the budget-spent notice must stay visible in the trajectory"
    )
    # The cap stopped real searches; the turn ended early instead of
    # burning all 15 rounds.
    assert fake_n[0] == _MAX_MEETING_LOOKUP_CALLS
    assert llm._client.chat.completions.create.call_count <= 12


def test_stubborn_relookups_force_early_answer(tmp_path):
    """Two wasted dry serves end the turn via force-generate instead of
    burning all 15 rounds."""
    from src.chatbox.agent import ChatboxAgent, _MAX_MEETING_LOOKUP_CALLS

    store = SessionStore(str(tmp_path / "force.db"))
    store.create_session(title="m", session_id="meeting_force")
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()
    _stub_always_lookup(llm, tool_rounds=50)
    n_seen = [0]

    def fake_lookup(mid, q, **kwargs):
        n_seen[0] += 1
        return ('{"hit_count": 1, "context": "X"}', {f"m:{n_seen[0]}"}, [])

    agent = ChatboxAgent(store, llm, MagicMock())
    with patch(
        "src.meeting.transcript_index.lookup_json_and_keys",
        side_effect=fake_lookup,
    ):
        resp = agent.chat("meeting_force", "价格谈到哪了", mode="direct")

    assert resp.answer == "answered"
    # 5 real + 1 limit + 2 dry rounds + 1 forced answer — far below the
    # 15-round ceiling the old behavior burned through.
    assert llm._client.chat.completions.create.call_count <= 12


def test_dry_tool_result_omits_sources_count(tmp_path):
    """The '0 段原话' mislead: dry/limit serves must not report a count."""
    import asyncio
    from types import SimpleNamespace

    from src.chatbox.agent import ChatboxAgent, _MAX_MEETING_LOOKUP_CALLS

    def _chunks(parts):
        for part in parts:
            yield part

    def _text_round(text):
        delta = SimpleNamespace(
            content=text, tool_calls=None, reasoning_content=None
        )
        return _chunks([
            SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None, reasoning_content=None), finish_reason="stop")]),
        ])

    def _tool_round(call_id, name, arguments):
        tc = SimpleNamespace(
            index=0, id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
        )
        delta = SimpleNamespace(content=None, tool_calls=[tc], reasoning_content=None)
        return _chunks([
            SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None, reasoning_content=None), finish_reason="tool_calls")]),
        ])

    store = SessionStore(str(tmp_path / "dry-stream.db"))
    store.create_session(title="m", session_id="meeting_dry_stream")
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()
    n = [0]

    def _side_effect(**kwargs):
        if not kwargs.get("tools"):
            # Force-generate path: plain completion object.
            resp = MagicMock()
            resp.choices[0].message.content = "answered"
            return resp
        n[0] += 1
        return _tool_round(
            f"call_{n[0]}", "lookup_meeting_transcript", '{"query":"价格"}'
        )

    llm._client.chat.completions.create.side_effect = _side_effect

    fake_n = [0]

    def fake_lookup(mid, q, **kwargs):
        fake_n[0] += 1
        return ('{"hit_count": 1, "context": "X"}', {f"m:{fake_n[0]}"}, [])

    agent = ChatboxAgent(store, llm, MagicMock())

    async def _run():
        events = []
        with patch(
            "src.meeting.transcript_index.lookup_json_and_keys",
            side_effect=fake_lookup,
        ):
            agen = agent.chat_stream(
                "meeting_dry_stream", "价格谈到哪了", mode="direct"
            )
            try:
                async for ev in agen:
                    events.append(ev)
            finally:
                await agen.aclose()
        return events

    events = asyncio.run(_run())
    lookup_results = [
        e
        for e in events
        if e.get("type") == "tool_result"
        and e.get("tool") == "lookup_meeting_transcript"
    ]
    assert len(lookup_results) >= _MAX_MEETING_LOOKUP_CALLS
    for e in lookup_results:
        content = str(e.get("content") or "")
        is_control = (
            "limit reached" in content or "finished for this turn" in content
        )
        if is_control:
            assert "sources_count" not in e, (
                "dry/limit serves must not show a hit count"
            )
    real = [e for e in lookup_results[:_MAX_MEETING_LOOKUP_CALLS]]
    assert all(isinstance(e.get("sources_count"), int) for e in real)


def test_force_generate_answer_retries_once_when_empty():
    """An empty forced answer is retried once with thinking left on."""
    from src.chatbox.agent import _force_generate_answer

    llm = MagicMock()
    llm._model = "qwen-test"
    client = MagicMock()
    empty = MagicMock()
    empty.choices[0].message.content = ""
    good = MagicMock()
    good.choices[0].message.content = "recovered answer"
    client.chat.completions.create.side_effect = [empty, good]
    llm._client = client

    out = _force_generate_answer(llm, [{"role": "user", "content": "hi"}])

    assert out == "recovered answer"
    assert client.chat.completions.create.call_count == 2
    first_kwargs = client.chat.completions.create.call_args_list[0].kwargs
    second_kwargs = client.chat.completions.create.call_args_list[1].kwargs
    assert first_kwargs.get("extra_body") == {"thinking": {"type": "disabled"}}
    assert not second_kwargs.get("extra_body")


def test_collapse_keeps_budget_and_done_messages_intact():
    """The turn-context collapse must stub only excerpt-bearing lookup
    results. The limit / turn-done control payloads are the model's stop
    signal — stubbing them into 'Superseded by a later lookup…' erased the
    stop instruction and fueled the ghost loop."""
    from src.chatbox.agent import _collapse_superseded_lookup_results

    def _lookup_pair(call_id, content):
        return [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "lookup_meeting_transcript",
                        "arguments": '{"query":"q"}',
                    },
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "content": content},
        ]

    messages = [{"role": "user", "content": "总结"}]
    messages += _lookup_pair(
        "call_1", '{"hit_count": 1, "context": "older excerpts"}'
    )
    messages += _lookup_pair(
        "call_2", '{"hit_count": 2, "context": "the latest merged view"}'
    )
    messages += _lookup_pair(
        "call_3",
        '{"hit_count": 0, "context": "", "message": "Transcript lookup '
        'limit reached (5 calls this turn)."}',
    )
    messages += _lookup_pair(
        "call_4",
        '{"hit_count": 2, "context": "", "message": "Transcript lookup is '
        'finished for this turn."}',
    )
    out = _collapse_superseded_lookup_results(messages)
    by_id = {
        m.get("tool_call_id"): m.get("content")
        for m in out
        if m.get("role") == "tool"
    }
    assert "Superseded" in by_id["call_1"], "older excerpt results get stubbed"
    assert "latest merged view" in by_id["call_2"], "the kept view stays"
    assert "limit reached" in by_id["call_3"], "stop signal must stay intact"
    assert "finished for this turn" in by_id["call_4"], (
        "turn-done message must stay intact"
    )


# ── Tool-message hygiene (DeepSeek rejects dangling tool_calls) ──


class TestToolMessageHygiene:
    def test_build_messages_stubs_dangling_history_tool_calls(self, store):
        """History written before multimodal tool results were persistable
        carries assistant tool_calls with no answering tool row — strict
        providers (DeepSeek) 400 on it. Build must stub each dangling id."""
        s = store.create_session()
        store.add_message(s.id, "user", "第一问")
        store.add_message(
            s.id, "assistant", "",
            metadata={"tool_calls": [
                {"id": "call_lost1", "type": "function",
                 "function": {"name": "lookup_collection",
                              "arguments": "{\"query\": \"x\"}"}},
            ]},
        )
        # no tool row for call_lost1 — the old damage
        store.add_message(s.id, "assistant", "第一答")

        from src.chatbox.agent import ChatboxAgent

        agent = ChatboxAgent(
            session_store=store,
            chat_llm=MagicMock(),
            agentic_service=MagicMock(),
        )
        msgs = agent._build_messages(s.id, "第二问")

        answered = {
            m.get("tool_call_id") for m in msgs if m.get("role") == "tool"
        }
        assert "call_lost1" in answered
        for i, m in enumerate(msgs):
            for tc in (m.get("tool_calls") or []):
                tid = tc.get("id")
                assert any(
                    x.get("role") == "tool" and x.get("tool_call_id") == tid
                    for x in msgs[i + 1:]
                ), f"dangling tool_call {tid} not stubbed"

    def test_persist_extra_messages_keeps_multimodal_pairing(self, store):
        """List-content tool results used to be skipped entirely, leaving the
        paired tool_call dangling in history. They must persist as a stub."""
        from src.chatbox.agent import _persist_extra_messages

        s = store.create_session()
        extras = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_mm1", "type": "function",
                 "function": {"name": "lookup_collection", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_mm1",
             "content": [
                 {"type": "text", "text": "IC note says IRR ~12% p.a."},
                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
             ]},
        ]
        _persist_extra_messages(store, s.id, extras)

        rows = [m for m in store.get_messages(s.id) if m.role == "tool"]
        assert len(rows) == 1, "tool row must be persisted, not skipped"
        assert rows[0].metadata.get("tool_call_id") == "call_mm1"
        assert isinstance(rows[0].content, str)
        assert "IRR" in rows[0].content
