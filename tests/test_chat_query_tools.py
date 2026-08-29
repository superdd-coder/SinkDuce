"""Tests for Chat query-tool registry and ChatboxAgent structure tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.chatbox.query_tools import (
    AGENT_STRUCTURE_NAMES,
    QUICK_STRUCTURE_NAMES,
    allowed_tool_names,
    collect_todo_create_items,
    collect_todo_delete_ids,
    collect_todo_update_items,
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
        assert "list_todos" in names
        assert "create_todo" in names
        assert "update_todo" in names
        assert "delete_todo" in names
        assert "lookup_collection" not in names
        assert "list_notes" not in names
        assert "get_query_history" not in names
        assert "search_agentic_chunks" not in names

    def test_quick_excludes_global_discovery(self):
        tools = tools_for_mode("direct")
        names = {t["function"]["name"] for t in tools}
        assert "lookup_collection" in names
        assert "list_library_tree" in names
        assert "list_todos" in names
        assert "create_todo" in names
        assert "update_todo" in names
        assert "delete_todo" in names
        assert "list_collections" not in names
        assert "search_knowledge_base" not in names
        assert "list_notes" not in names

    def test_meeting_has_transcript_lookup_only(self):
        names = {t["function"]["name"] for t in tools_for_mode("direct", is_meeting=True)}
        assert names == {"lookup_meeting_transcript"}
        assert allowed_tool_names("direct", is_meeting=True) == frozenset(
            {"lookup_meeting_transcript"}
        )

    def test_has_meetings_false_drops_meeting_trio(self):
        """No ingested meetings → no meeting tools in Chat / Quick Chat."""
        meeting_tools = {"list_meeting_catalog", "lookup_meeting_transcript", "read_meeting_summary"}
        for mode in ("agentic", "direct"):
            names = {t["function"]["name"] for t in tools_for_mode(mode, has_meetings=False)}
            assert names.isdisjoint(meeting_tools)
            allowed = allowed_tool_names(mode, has_meetings=False)
            assert allowed.isdisjoint(meeting_tools)

    def test_has_meetings_default_keeps_meeting_trio(self):
        for mode in ("agentic", "direct"):
            names = {t["function"]["name"] for t in tools_for_mode(mode)}
            assert "lookup_meeting_transcript" in names
            assert "read_meeting_summary" in names

    def test_group_has_lookup_and_summary_tools(self):
        names = {t["function"]["name"] for t in tools_for_mode("direct", is_group=True)}
        assert names == {
            "list_meeting_catalog",
            "lookup_meeting_transcript",
            "read_meeting_summary",
        }
        assert allowed_tool_names("direct", is_group=True) == frozenset(names)
        lookup = next(
            t
            for t in tools_for_mode("direct", is_group=True)
            if t["function"]["name"] == "lookup_meeting_transcript"
        )
        params = lookup["function"]["parameters"]["properties"]
        assert "meeting_ids" in params
        assert "latest" not in str(params).lower()

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
        assert "do not look them up again" in desc
        assert "information need" in query_desc or "what information" in query_desc
        prompt = MEETING_CHAT_SYSTEM_PROMPT.lower()
        assert "full transcript" in prompt or "entire transcript" in prompt
        assert "outline" in prompt or "general summary" in prompt
        assert "display name" in prompt
        assert "later parts" not in prompt
        assert "next page" not in prompt
        assert "if excerpts miss a fact" not in prompt
        assert "do not look it up again" in prompt or "do not look them up again" in prompt

    def test_meeting_lookup_mentions_are_not_speaker_filters(self):
        from src.chatbox.query_tools import LOOKUP_MEETING_TRANSCRIPT_TOOL
        from src.prompts import MEETING_CHAT_SYSTEM_PROMPT

        fn = LOOKUP_MEETING_TRANSCRIPT_TOOL["function"]
        desc = fn["description"].lower()
        prompt = MEETING_CHAT_SYSTEM_PROMPT.lower()
        assert "not in the mapping" in prompt or "outside the mapping" in prompt
        assert "mention" in prompt
        assert "absent" in prompt or "not in the meeting" in prompt
        assert "keyword" in prompt or "search" in prompt
        assert "speaker_scope=all" in prompt
        assert "not in the mapping" in desc or "outside the mapping" in desc or "mention" in desc

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

    def test_todo_tool_schemas(self):
        tools = {t["function"]["name"]: t for t in tools_for_mode("agentic")}
        listed = tools["list_todos"]["function"]
        assert "collection" not in listed["parameters"].get("required", [])
        assert listed["parameters"]["properties"]["mine"]["default"] is False
        assert listed["parameters"]["properties"]["include_done"]["default"] is False
        create = tools["create_todo"]["function"]
        assert "collection" in create["parameters"].get("required", [])
        assert "todos" in create["parameters"]["properties"]
        update = tools["update_todo"]["function"]
        assert "updates" in update["parameters"]["properties"]
        assert "title" in update["parameters"]["properties"]
        assert "body" in update["parameters"]["properties"]
        assert "ddl" in update["parameters"]["properties"]
        delete = tools["delete_todo"]["function"]
        assert "todo_ids" in delete["parameters"]["properties"]
        assert "todo_id" in delete["parameters"]["properties"]
        desc = delete["description"].lower()
        assert "delete" in desc
        assert "todo_ids" in desc
        meeting_names = {t["function"]["name"] for t in tools_for_mode("direct", is_meeting=True)}
        assert "list_todos" not in meeting_names

    def test_collect_todo_create_and_update_items(self):
        assert collect_todo_create_items({"title": "A", "ddl": "2026-09-01"}) == [
            {
                "title": "A",
                "body": "",
                "ddl": "2026-09-01",
                "assign_to_me": False,
                "assignee_person_id": "",
            }
        ]
        got = collect_todo_create_items(
            {"todos": [{"title": "B"}, {"title": "C"}], "title": "A"}
        )
        assert [x["title"] for x in got] == ["A", "B", "C"]
        ups = collect_todo_update_items(
            {"updates": [{"todo_id": "t1", "done": True}, {"todo_id": "t2", "title": "X"}]}
        )
        assert [x["todo_id"] for x in ups] == ["t1", "t2"]

    def test_collect_todo_delete_ids_unique_order(self):
        assert collect_todo_delete_ids({"todo_id": "a"}) == ["a"]
        assert collect_todo_delete_ids({"todo_ids": ["b", "a", "b"], "todo_id": "a"}) == [
            "b",
            "a",
        ]
        assert collect_todo_delete_ids({"todo_ids": "x, y"}) == ["x", "y"]
        assert collect_todo_delete_ids({}) == []


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

    def test_quick_overwrites_todo_write_collection(self):
        args, err = force_collection_args(
            "create_todo",
            {"collection": "other_col", "title": "X"},
            mode="direct",
            forced_collection="col_mine",
        )
        assert err is None
        assert args["collection"] == "col_mine"
        assert args["title"] == "X"


class TestTodoStructureDispatch:
    def test_list_todos_dispatches_to_mcp(self):
        import asyncio

        from src.chatbox.query_tools import execute_structure_tool_async

        captured: list[dict] = []

        async def _fake_list(collection="", include_done=False, done=None, mine=False):
            captured.append(
                {
                    "collection": collection,
                    "include_done": include_done,
                    "done": done,
                    "mine": mine,
                }
            )
            return {"todos": [], "total": 0}

        async def _run():
            with patch(
                "src.mcp.tools.file_mgmt.list_todos",
                side_effect=_fake_list,
            ):
                raw = await execute_structure_tool_async(
                    "list_todos",
                    {"mine": True},
                    mode="agentic",
                )
                return raw

        out = asyncio.run(_run())
        assert captured == [
            {"collection": "", "include_done": False, "done": None, "mine": True}
        ]
        assert '"total": 0' in out or '"total":0' in out.replace(" ", "")

    def test_create_todo_agentic_requires_collection(self):
        import asyncio

        from src.chatbox.query_tools import execute_structure_tool_async

        async def _run():
            return await execute_structure_tool_async(
                "create_todo",
                {"title": "X"},
                mode="agentic",
            )

        out = json.loads(asyncio.run(_run()))
        assert "error" in out
        assert "collection is required" in out["error"]


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
