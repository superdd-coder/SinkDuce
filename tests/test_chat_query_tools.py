"""Tests for Chat query-tool registry and ChatboxAgent structure tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.chatbox.query_tools import (
    AGENT_STRUCTURE_NAMES,
    QUICK_STRUCTURE_NAMES,
    allowed_tool_names,
    force_collection_args,
    merge_search_tool_calls,
    tools_for_mode,
)
from src.db.sessions import SessionStore


# ── Allowlists ────────────────────────────────────────────────


class TestAllowlists:
    def test_agent_includes_search_and_structure(self):
        tools = tools_for_mode("agentic")
        names = {t["function"]["name"] for t in tools}
        assert "search_knowledge_base" in names
        assert "list_collections" in names
        assert "list_library_tree" in names
        assert "get_document_text" in names
        assert "lookup_collection" not in names
        assert "list_notes" not in names
        assert "get_query_history" not in names
        assert "search_agentic_chunks" not in names

    def test_quick_excludes_global_discovery(self):
        tools = tools_for_mode("direct")
        names = {t["function"]["name"] for t in tools}
        assert "lookup_collection" in names
        assert "list_library_tree" in names
        assert "list_collections" not in names
        assert "search_knowledge_base" not in names
        assert "list_notes" not in names

    def test_meeting_has_transcript_lookup_only(self):
        names = {t["function"]["name"] for t in tools_for_mode("direct", is_meeting=True)}
        assert names == {"lookup_meeting_transcript"}
        assert allowed_tool_names("direct", is_meeting=True) == frozenset(
            {"lookup_meeting_transcript"}
        )

    def test_meeting_lookup_tool_is_a_search_not_a_reader(self):
        from src.chatbox.query_tools import LOOKUP_MEETING_TRANSCRIPT_TOOL
        from src.prompts import MEETING_CHAT_SYSTEM_PROMPT

        fn = LOOKUP_MEETING_TRANSCRIPT_TOOL["function"]
        desc = fn["description"].lower()
        query_desc = fn["parameters"]["properties"]["query"]["description"].lower()
        assert "search" in desc
        assert "independent" in desc or "each call" in desc
        assert "later parts" not in desc
        assert "next page" not in desc
        assert "information need" in query_desc or "what information" in query_desc
        prompt = MEETING_CHAT_SYSTEM_PROMPT.lower()
        assert "full transcript" in prompt or "entire transcript" in prompt
        assert "outline" in prompt or "general summary" in prompt
        assert "display name" in prompt
        assert "later parts" not in prompt
        assert "next page" not in prompt

    def test_quick_structure_subset(self):
        assert "list_collections" not in QUICK_STRUCTURE_NAMES
        assert set(QUICK_STRUCTURE_NAMES) == set(AGENT_STRUCTURE_NAMES) - {"list_collections"}

    def test_full_text_tools_marked_low_priority(self):
        tools = {t["function"]["name"]: t for t in tools_for_mode("agentic")}
        for name in ("get_document_text", "get_file_chunks"):
            desc = tools[name]["function"]["description"].upper()
            assert "LOW PRIORITY" in desc

    def test_structure_tools_not_blanket_prefer_library_tree(self):
        """list_library_tree must not be recommended on every structure tool."""
        tools = {t["function"]["name"]: t for t in tools_for_mode("agentic")}
        # Tools that must NOT push list_library_tree as the default action
        for name in ("get_timeline", "get_collection", "list_file_versions", "get_conflicts"):
            desc = tools[name]["function"]["description"].lower()
            assert "prefer list_library_tree" not in desc, name
        # list_library_tree itself should state when NOT to use (content/search)
        lib = tools["list_library_tree"]["function"]["description"].lower()
        assert "when not" in lib or "not:" in lib
        assert "search" in lib or "content" in lib
        # get_timeline should be for timeline/events, not library layout default
        tl = tools["get_timeline"]["function"]["description"].lower()
        assert "timeline" in tl
        assert "when not" in tl or "list_library_tree" in tl
        # Search is primary for content
        sk = tools["search_knowledge_base"]["function"]["description"].lower()
        assert "primary" in sk
        assert "list_library_tree" in sk  # explicit anti-pattern mention

    def test_get_document_text_schema_documents_window_and_char_offset(self):
        tools = {t["function"]["name"]: t for t in tools_for_mode("agentic")}
        gdt = tools["get_document_text"]["function"]
        desc = gdt["description"].lower()
        assert "has_more" in desc
        assert "next_offset" in desc
        assert "char_offset" in desc
        props = gdt["parameters"]["properties"]
        assert props["offset"]["default"] == 0
        assert props["limit"]["default"] == 32000
        assert "page" in desc or "continuation" in desc or "paging" in desc


# ── get_document_text Chat clamp ──────────────────────────────


class TestGetDocumentTextChatClamp:
    def test_default_limit_and_hard_cap(self):
        import asyncio

        from src.chatbox.query_tools import (
            _CHAT_DOC_DEFAULT_LIMIT,
            _CHAT_DOC_MAX_LIMIT,
            execute_structure_tool_async,
        )

        captured: list[dict] = []

        async def _fake_gdt(collection, **kwargs):
            captured.append({"collection": collection, **kwargs})
            return {
                "content": "x",
                "has_more": False,
                "next_offset": None,
                "total_chars": 1,
            }

        async def _run():
            with patch(
                "src.mcp.tools.documents.get_document_text",
                side_effect=_fake_gdt,
            ):
                # default limit when omitted
                await execute_structure_tool_async(
                    "get_document_text",
                    {"collection": "col_a", "file_id": "f1"},
                    mode="agentic",
                )
                # 0 / negative → default
                await execute_structure_tool_async(
                    "get_document_text",
                    {"collection": "col_a", "file_id": "f1", "limit": 0},
                    mode="agentic",
                )
                # over hard max → clamp
                await execute_structure_tool_async(
                    "get_document_text",
                    {
                        "collection": "col_a",
                        "file_id": "f1",
                        "limit": 999999,
                        "offset": 100,
                    },
                    mode="agentic",
                )

        asyncio.run(_run())
        assert len(captured) == 3
        assert captured[0]["limit"] == _CHAT_DOC_DEFAULT_LIMIT == 32000
        assert captured[0]["offset"] == 0
        assert captured[1]["limit"] == _CHAT_DOC_DEFAULT_LIMIT
        assert captured[2]["limit"] == _CHAT_DOC_MAX_LIMIT == 96000
        assert captured[2]["offset"] == 100


# ── Collection lock ───────────────────────────────────────────


class TestCollectionLock:
    def test_quick_overwrites_collection(self):
        args, err = force_collection_args(
            "list_library_tree",
            {"collection": "other_col", "max_depth": 1},
            mode="direct",
            forced_collection="col_mine",
        )
        assert err is None
        assert args["collection"] == "col_mine"
        assert args["max_depth"] == 1

    def test_quick_rejects_without_collection(self):
        _, err = force_collection_args(
            "list_library_tree",
            {"collection": "x"},
            mode="direct",
            forced_collection=None,
        )
        assert err is not None

    def test_agentic_does_not_force(self):
        args, err = force_collection_args(
            "list_library_tree",
            {"collection": "col_a"},
            mode="agentic",
            forced_collection=None,
        )
        assert err is None
        assert args["collection"] == "col_a"

    def test_quick_blocks_list_collections(self):
        _, err = force_collection_args(
            "list_collections",
            {},
            mode="direct",
            forced_collection="col_mine",
        )
        assert err is not None


# ── Merge search only ─────────────────────────────────────────


class TestMergeSearch:
    def test_merges_multiple_searches(self):
        tcs = [
            {
                "id": "1",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": json.dumps({"raw_query": "topic A"}),
                },
            },
            {
                "id": "2",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": json.dumps({"raw_query": "topic B"}),
                },
            },
        ]
        out = merge_search_tool_calls(tcs)
        assert len(out) == 1
        args = json.loads(out[0]["function"]["arguments"])
        assert "topic A" in args["raw_query"]
        assert "topic B" in args["raw_query"]
        assert args["decompose"] is True

    def test_preserves_structure_tools_when_mixed(self):
        tcs = [
            {
                "id": "s1",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": json.dumps({"raw_query": "A"}),
                },
            },
            {
                "id": "t1",
                "type": "function",
                "function": {
                    "name": "list_library_tree",
                    "arguments": json.dumps({"collection": "col_x"}),
                },
            },
            {
                "id": "s2",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": json.dumps({"raw_query": "B"}),
                },
            },
        ]
        out = merge_search_tool_calls(tcs)
        names = [tc["function"]["name"] for tc in out]
        assert names.count("search_knowledge_base") == 1
        assert "list_library_tree" in names
        assert len(out) == 2


# ── Agent integration ─────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "chat_qt.db"))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


def _fake_llm_response(content=None, tool_calls=None):
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


class TestChatboxStructureTools:
    def test_agent_mode_exposes_structure_tools_to_llm(self, store):
        from src.chatbox.agent import ChatboxAgent

        llm = MagicMock()
        llm._model = "test"
        llm._client = MagicMock()
        llm._client.chat.completions.create.return_value = _fake_llm_response(
            content="ok"
        )
        agent = ChatboxAgent(store, llm, MagicMock())
        s = store.create_session()
        agent.chat(s.id, "hi")
        kwargs = llm._client.chat.completions.create.call_args[1]
        names = {t["function"]["name"] for t in kwargs["tools"]}
        assert "search_knowledge_base" in names
        assert "list_library_tree" in names
        assert "get_document_text" in names

    def test_structure_tool_executed(self, store):
        from src.chatbox.agent import ChatboxAgent

        llm = MagicMock()
        llm._model = "test"
        llm._client = MagicMock()
        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _fake_llm_response(
                    tool_calls=[
                        {
                            "id": "call_tree",
                            "type": "function",
                            "function": {
                                "name": "list_library_tree",
                                "arguments": json.dumps({"collection": "col_x"}),
                            },
                        }
                    ]
                )
            return _fake_llm_response(content="Tree looks fine.")

        llm._client.chat.completions.create.side_effect = _side_effect
        agentic = MagicMock()
        agent = ChatboxAgent(store, llm, agentic)
        s = store.create_session()

        with patch(
            "src.chatbox.agent.execute_structure_tool",
            return_value=json.dumps({"folders": [], "orphans": []}),
        ) as mock_exec:
            resp = agent.chat(s.id, "What files are in col_x?")
            assert mock_exec.called
            assert mock_exec.call_args[0][0] == "list_library_tree"
            assert resp.tool_calls == 1
            assert "Tree" in resp.answer
            assert not agentic.run.called

    def test_quick_mode_forces_collection_on_structure(self, store):
        from src.chatbox.agent import ChatboxAgent

        llm = MagicMock()
        llm._model = "test"
        llm._client = MagicMock()
        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _fake_llm_response(
                    tool_calls=[
                        {
                            "id": "call_tree",
                            "type": "function",
                            "function": {
                                "name": "list_library_tree",
                                "arguments": json.dumps({"collection": "evil_col"}),
                            },
                        }
                    ]
                )
            return _fake_llm_response(content="Only this collection.")

        llm._client.chat.completions.create.side_effect = _side_effect
        direct = MagicMock()
        agent = ChatboxAgent(store, llm, None, direct_module=direct)
        s = store.create_session(collections=["col_mine"])

        with patch(
            "src.chatbox.agent.execute_structure_tool",
            return_value=json.dumps({"ok": True}),
        ) as mock_exec:
            agent.chat(s.id, "list files", mode="direct")
            assert mock_exec.called
            assert mock_exec.call_args[1]["forced_collection"] == "col_mine"
            assert mock_exec.call_args[1]["mode"] == "direct"

    def test_meeting_mode_transcript_lookup_tool(self, store):
        from src.chatbox.agent import ChatboxAgent

        llm = MagicMock()
        llm._model = "test"
        llm._client = MagicMock()
        llm._client.chat.completions.create.return_value = _fake_llm_response(
            content="meeting answer"
        )
        agent = ChatboxAgent(store, llm, MagicMock())
        s = store.create_session(title="m", session_id="meeting_m1")
        agent.chat(s.id, "summary?", mode="direct")
        kwargs = llm._client.chat.completions.create.call_args[1]
        tools = kwargs.get("tools") or []
        names = {t["function"]["name"] for t in tools}
        assert names == {"lookup_meeting_transcript"}
