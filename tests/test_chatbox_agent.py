"""Tests for ChatboxAgent — Phase 3 Step 1.

Run: pytest tests/test_chatbox_agent.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.db.sessions import SessionStore, Session


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
        assert quick_msgs[0]["content"] == "Quick system for col"
        assert not any(
            "SHOULD_NOT_APPEAR" in (m.get("content") or "")
            or "Knowledge base reference" in (m.get("content") or "")
            for m in quick_msgs
        )

        # ── Meeting: system prompt, live transcript, dialogue, speaker ──
        meeting = store.create_session(session_id="meeting_abc123")
        # Legacy DB system rows must be ignored; live load supplies transcript
        store.add_message(meeting.id, "system", "STALE_SHOULD_NOT_APPEAR")
        store.add_message(meeting.id, "user", "what was said?")
        store.add_message(meeting.id, "assistant", "summary")
        store.add_message(meeting.id, "user", "more?")
        with patch(
            "src.chatbox.meeting_context.meeting_transcript_context_message",
            return_value="FULL_TRANSCRIPT_TEXT",
        ):
            meet_msgs = agent._build_messages(
                meeting.id, "more?",
                system_prompt="MEETING_SYSTEM",
                catalog_text="Knowledge base reference:\n- NO",
                pre_message_context="Speaker mapping: S1=Alice",
            )
        assert meet_msgs[0]["content"] == "MEETING_SYSTEM"
        assert meet_msgs[1]["role"] == "system"
        assert meet_msgs[1]["content"] == "FULL_TRANSCRIPT_TEXT"
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
        # speaker after transcript and dialogue
        assert speaker_idxs[0] > 1
        transcript_idx = 1
        assert speaker_idxs[0] > transcript_idx


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
