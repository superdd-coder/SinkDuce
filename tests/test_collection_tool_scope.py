"""Selected Chat collections are an execution boundary, including stale history."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.chatbox.query_tools import STRUCTURE_TOOL_NAMES, execute_structure_tool_async
from tests.test_chatbox_agent import (
    store, mock_llm, mock_agentic, _fake_stream, _tc_delta,
    _fake_llm_response,
)


COLLECTION_TOOLS = sorted(STRUCTURE_TOOL_NAMES - {"list_collections"})
DIRECTORY = [
    {"id": "col-a", "name": "Project Alpha"},
    {"id": "col-b", "name": "Project Beta"},
]


@pytest.mark.parametrize("name", COLLECTION_TOOLS)
def test_every_collection_tool_rejects_unselected_id_before_dispatch(name):
    dispatch = AsyncMock(return_value='{"secret": "outside content"}')
    with patch("src.chatbox.query_tools._dispatch_structure_async", dispatch):
        result = asyncio.run(execute_structure_tool_async(
            name, {"collection": "col-b"}, mode="agentic",
            selected_collections=["col-a"],
        ))
    assert "error" in json.loads(result)
    assert "outside content" not in result
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("name", COLLECTION_TOOLS)
def test_every_collection_tool_allows_selected_id_without_changing_arguments(name):
    args = {"collection": "col-a", "file_id": "file-1", "offset": 200}
    dispatch = AsyncMock(return_value='{"ok": true}')
    with patch("src.chatbox.query_tools._dispatch_structure_async", dispatch):
        result = asyncio.run(execute_structure_tool_async(
            name, args, mode="agentic", selected_collections=["col-a", "col-c"],
        ))
    assert json.loads(result) == {"ok": True}
    if name in {"create_note", "list_notes", "get_note", "update_note"}:
        dispatch.assert_awaited_once_with(name, args, selected_collections=["col-a", "col-c"])
    else:
        dispatch.assert_awaited_once_with(name, args)


@pytest.mark.parametrize("target", [None, "", "   ", ["col-a"], 42, "Project Beta"])
def test_scoped_read_rejects_missing_malformed_or_named_targets(target):
    dispatch = AsyncMock(return_value='{"secret": "outside content"}')
    with patch("src.chatbox.query_tools._dispatch_structure_async", dispatch):
        result = asyncio.run(execute_structure_tool_async(
            "get_document_text", {"collection": target}, mode="agentic",
            selected_collections=["col-a"],
        ))
    assert "error" in json.loads(result)
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("selected", [None, []])
def test_unscoped_chat_preserves_existing_dispatch(selected):
    args = {"collection": "col-b"}
    dispatch = AsyncMock(return_value='{"ok": true}')
    with patch("src.chatbox.query_tools._dispatch_structure_async", dispatch):
        result = asyncio.run(execute_structure_tool_async(
            "get_document_text", args, mode="agentic", selected_collections=selected,
        ))
    assert json.loads(result) == {"ok": True}
    dispatch.assert_awaited_once_with("get_document_text", args)


def test_direct_forces_bound_collection_before_checking_scope():
    args = {"collection": "col-b", "file_id": "file-1"}
    dispatch = AsyncMock(return_value='{"ok": true}')
    with patch("src.chatbox.query_tools._dispatch_structure_async", dispatch):
        result = asyncio.run(execute_structure_tool_async(
            "get_document_text", args, mode="direct", forced_collection="col-a",
            selected_collections=["col-a"],
        ))
    assert json.loads(result) == {"ok": True}
    dispatch.assert_awaited_once_with("get_document_text", {**args, "collection": "col-a"})
    assert args["collection"] == "col-b"


@pytest.mark.parametrize("target", ["Project Beta", "unknown project", ""])
def test_note_name_resolution_cannot_bypass_scope_or_disclose_directory(target):
    dispatch = AsyncMock(return_value='{"ok": true}')
    with patch("src.chatbox.collection_resolve.build_collection_directory", return_value=DIRECTORY), \
         patch("src.chatbox.query_tools._dispatch_structure_async", dispatch):
        result = asyncio.run(execute_structure_tool_async(
            "create_note", {"collection": target, "title": "Note"}, mode="agentic",
            selected_collections=["col-a"],
        ))
    assert "error" in json.loads(result)
    assert "Project Beta" not in result and "col-b" not in result
    dispatch.assert_not_awaited()


def test_selected_note_name_keeps_original_wording_for_confirmation():
    args = {"collection": "Project Alpha", "title": "Note"}
    dispatch = AsyncMock(return_value='{"ok": true}')
    with patch("src.chatbox.collection_resolve.build_collection_directory", return_value=DIRECTORY), \
         patch("src.chatbox.query_tools._dispatch_structure_async", dispatch):
        result = asyncio.run(execute_structure_tool_async(
            "create_note", args, mode="agentic", selected_collections=["col-a"],
        ))
    assert json.loads(result) == {"ok": True}
    dispatch.assert_awaited_once_with("create_note", args, selected_collections=["col-a"])


def test_deleted_selected_note_target_never_exposes_other_collection_directory():
    create_note = AsyncMock(return_value='{"ok": true}')
    with patch("src.chatbox.collection_resolve.build_collection_directory", return_value=[DIRECTORY[1]]), \
         patch("src.chatbox.query_tools._check_tool_allowed", AsyncMock(return_value=None)), \
         patch("src.mcp.tools.notes.create_note", create_note):
        result = asyncio.run(execute_structure_tool_async(
            "create_note", {"collection": "col-a", "title": "Note"}, mode="agentic",
            selected_collections=["col-a"],
        ))
    assert "error" in json.loads(result)
    assert "col-b" not in result and "Project Beta" not in result
    create_note.assert_not_awaited()


@pytest.mark.parametrize("name", [
    "get_document_text", "get_collection_summary", "list_files", "list_todos",
    "create_todo", "update_todo", "delete_todo", "create_note",
    "list_notes", "get_note", "update_note",
])
def test_stream_cannot_use_unselected_id_from_history(
    store, mock_llm, mock_agentic, name,
):
    from src.chatbox.agent import ChatboxAgent

    session = store.create_session(title="scope regression")
    store.add_message(session.id, "assistant", "Earlier target was col-b.")
    args = {"collection": "col-b", "file_id": "file-1", "title": "Note",
            "todo_id": "todo-1", "content": "body"}
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_stream([("tool_calls", {"tool_calls": [
            _tc_delta(0, "outside", name, json.dumps(args)),
        ]})]),
        _fake_stream([("stop", {"content": "answer"})]),
    ]
    dispatch = AsyncMock(return_value='{"secret": "outside content"}')
    peek_note = Mock(return_value={"error": "outside content"})
    peek_todo = Mock(return_value={"error": "outside content"})

    async def collect():
        with patch("src.chatbox.query_tools._dispatch_structure_async", dispatch), \
             patch("src.chatbox.note_create_confirm.peek_note_for_create", peek_note), \
             patch("src.chatbox.todo_delete_confirm.peek_todo_for_delete", peek_todo):
            return [event async for event in ChatboxAgent(store, mock_llm, mock_agentic).chat_stream(
                session.id, "Read the selected project", collections=["col-a"], mode="agentic",
            )]

    events = asyncio.run(collect())
    messages = mock_llm._client.chat.completions.create.call_args.kwargs["messages"]
    results = [m["content"] for m in messages if m["role"] == "tool"]
    assert results and "error" in json.loads(results[-1])
    assert "outside content" not in json.dumps(messages)
    assert not any(e["type"] in {"note_create_confirm", "todo_delete_confirm"} for e in events)
    dispatch.assert_not_awaited()
    peek_note.assert_not_called()
    peek_todo.assert_not_called()


def test_mixed_delete_batch_checks_each_collection_before_preview(store, mock_llm, mock_agentic):
    from src.chatbox.agent import ChatboxAgent

    session = store.create_session(title="mixed batch")
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_stream([("tool_calls", {"tool_calls": [
            _tc_delta(0, "allowed", "delete_todo", '{"collection":"col-a","todo_id":"todo-a"}'),
            _tc_delta(1, "outside", "delete_todo", '{"collection":"col-b","todo_id":"todo-b"}'),
        ]})]),
        _fake_stream([("stop", {"content": "answer"})]),
    ]
    peek = Mock(return_value={"error": "unknown todo"})

    async def collect():
        with patch("src.chatbox.todo_delete_confirm.peek_todo_for_delete", peek):
            return [event async for event in ChatboxAgent(store, mock_llm, mock_agentic).chat_stream(
                session.id, "Remove these todos", collections=["col-a"], mode="agentic",
            )]

    asyncio.run(collect())
    peek.assert_called_once_with("col-a", "todo-a")


def test_nonstream_chat_enforces_stored_selection(store, mock_llm, mock_agentic):
    from src.chatbox.agent import ChatboxAgent

    session = store.create_session(title="stored scope", collections=["col-a"])
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_llm_response(tool_calls=[{"id": "outside", "type": "function", "function": {
            "name": "get_document_text", "arguments": '{"collection":"col-b","file_id":"file-1"}',
        }}]),
        _fake_llm_response(content="answer"),
    ]
    dispatch = AsyncMock(return_value='{"secret": "outside content"}')
    with patch("src.chatbox.query_tools._dispatch_structure_async", dispatch):
        ChatboxAgent(store, mock_llm, mock_agentic).chat(session.id, "Read my selected project")
    messages = mock_llm._client.chat.completions.create.call_args.kwargs["messages"]
    results = [m["content"] for m in messages if m["role"] == "tool"]
    assert "error" in json.loads(results[-1])
    dispatch.assert_not_awaited()


def test_stale_note_resolution_cannot_offer_outside_confirmation(store, mock_llm, mock_agentic):
    from src.chatbox.agent import ChatboxAgent

    session = store.create_session(title="stale note")
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_stream([("tool_calls", {"tool_calls": [
            _tc_delta(0, "note", "create_note", '{"collection":"col-a","title":"Note"}'),
        ]})]),
        _fake_stream([("stop", {"content": "answer"})]),
    ]
    peek = {"collection": "col-b", "collection_name": "Project Beta", "match": "fuzzy",
            "title": "Note", "preview": "body"}

    async def collect():
        with patch("src.chatbox.note_create_confirm.peek_note_for_create", return_value=peek), \
             patch("src.chatbox.note_create_confirm.note_create_confirm_store.wait_result",
                   AsyncMock(return_value=(False, None))), \
             patch("src.chatbox.collection_resolve.build_collection_directory", return_value=[DIRECTORY[1]]):
            return [event async for event in ChatboxAgent(store, mock_llm, mock_agentic).chat_stream(
                session.id, "Create a note", collections=["col-a"], mode="agentic",
            )]

    events = asyncio.run(collect())
    assert not any(e["type"] == "note_create_confirm" for e in events)
    assert "Project Beta" not in json.dumps(events)


def test_selected_fuzzy_note_name_still_requires_confirmation(store, mock_llm, mock_agentic):
    from src.chatbox.agent import ChatboxAgent

    session = store.create_session(title="note typo")
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_stream([("tool_calls", {"tool_calls": [
            _tc_delta(0, "note", "create_note", json.dumps({
                "collection": "Project Alpa", "title": "Note", "collection_from_user": True,
            })),
        ]})]),
        _fake_stream([("stop", {"content": "answer"})]),
    ]

    async def collect():
        with patch("src.chatbox.collection_resolve.build_collection_directory", return_value=DIRECTORY), \
             patch("src.chatbox.note_create_confirm.note_create_confirm_store.wait_result",
                   AsyncMock(return_value=(False, None))):
            return [event async for event in ChatboxAgent(store, mock_llm, mock_agentic).chat_stream(
                session.id, "Create a note in Project Alpa", collections=["col-a"], mode="agentic",
            )]

    events = asyncio.run(collect())
    cards = [e for e in events if e["type"] == "note_create_confirm"]
    assert len(cards) == 1 and cards[0]["collection"] == "col-a"
