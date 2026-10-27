"""Chat Note writes preserve identity and expose navigable, persisted changes."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from src.chatbox.agent import ChatboxAgent
from src.chatbox.query_tools import execute_structure_tool_async, tools_for_mode
from src.db.sessions import SessionStore
from src.notes import store as notes
from tests.test_note_create_confirm import _text_round, _tool_round


@pytest.fixture(autouse=True)
def isolated_notes(tmp_path, monkeypatch):
    from src.services import services
    from src.chatbox import collection_resolve

    monkeypatch.setattr(notes, "NOTES_DIR", tmp_path / "notes")
    db = MagicMock()
    db.collection_exists.return_value = True
    db.scroll_points.return_value = ([], None)
    monkeypatch.setattr(services, "db", db)
    monkeypatch.setattr(collection_resolve, "build_collection_directory", lambda: [
        {"id": "col_a", "name": "Alpha"}, {"id": "col_b", "name": "Beta"},
    ])


@pytest.mark.parametrize("mode", ["agentic", "direct"])
def test_chat_exposes_existing_note_tools(mode):
    names = {t["function"]["name"] for t in tools_for_mode(mode)}
    assert {"list_notes", "get_note", "update_note"} <= names


def run_tool(name, args, mode="agentic", selected=None):
    return json.loads(asyncio.run(execute_structure_tool_async(
        name, args, mode=mode,
        forced_collection="col_a" if mode == "direct" else None,
        selected_collections=selected,
    )))


def test_create_then_read_and_revise_preserves_note_id_and_other_content():
    created = run_tool("create_note", {"collection": "Alpha", "title": "Draft", "content": "Original"})
    listed = run_tool("list_notes", {"collection": "Alpha"}, selected=["col_a"])
    assert [n["id"] for n in listed["notes"]] == [created["id"]]
    read = run_tool("get_note", {"collection": "Alpha", "note_id": created["id"]}, selected=["col_a"])
    assert read["content"] == "Original"
    updated = run_tool("update_note", {"collection": "Alpha", "note_id": created["id"], "content": "Revised"}, selected=["col_a"])
    assert updated["id"] == created["id"]
    assert updated["title"] == "Draft"
    assert updated["collection"] == "col_a"
    assert notes.get_content(created["id"]) == "Revised"
    assert len(notes.list_notes("col_a")) == 1
    renamed = run_tool("update_note", {"collection": "col_a", "note_id": created["id"], "title": "Renamed"})
    assert renamed["title"] == "Renamed"
    assert notes.get_content(created["id"]) == "Revised"


@pytest.mark.parametrize("name", ["get_note", "update_note"])
def test_quick_collection_lock_cannot_access_a_note_from_another_collection(name):
    other = notes.create_note("col_b", "Private")
    notes.save_content(other.id, "Keep")
    result = run_tool(name, {"collection": "col_b", "note_id": other.id, "content": "Changed"}, mode="direct")
    assert "error" in result
    assert notes.get_content(other.id) == "Keep"


def test_update_outside_selected_scope_is_rejected():
    other = notes.create_note("col_b", "Other")
    result = run_tool("update_note", {"collection": "Beta", "note_id": other.id, "title": "Changed"}, selected=["col_a"])
    assert "error" in result
    assert notes.get_note(other.id).title == "Other"


def call_note_tool(entry, name, args):
    from src.chatbox.query_tools import execute_structure_tool
    from src.mcp.tools import notes as mcp_notes

    if entry == "mcp":
        return json.loads(asyncio.run(getattr(mcp_notes, name)(**args)))
    if entry == "chat_async":
        return run_tool(name, args)
    if entry == "chat_sync":
        return json.loads(execute_structure_tool(name, args, mode="agentic"))

    async def from_running_loop():
        return execute_structure_tool(name, args, mode="agentic")

    return json.loads(asyncio.run(from_running_loop()))


@pytest.mark.parametrize("entry", ["mcp", "chat_async", "chat_sync", "chat_sync_in_loop"])
@pytest.mark.parametrize("operation", ["create", "rename", "content"])
def test_read_only_actor_cannot_write_notes_through_any_tool_entry(monkeypatch, entry, operation):
    from fastapi import HTTPException
    from src.identity import Actor, reset_actor, set_actor
    from src.identity import acl

    original = notes.create_note("col_a", "Original")
    notes.save_content(original.id, "Keep this content")
    monkeypatch.setattr(acl, "collection_allows", lambda col, tier, role: col == "col_a" and tier == "read")
    name = "create_note" if operation == "create" else "update_note"
    args = {"collection": "col_a", "title": "Changed"} if operation == "create" else {
        "collection": "col_a", "note_id": original.id,
        **({"title": "Changed"} if operation == "rename" else {"content": "Changed"}),
    }
    token = set_actor(Actor(id="read-only-key", kind="key", role_id="member"))
    try:
        read = call_note_tool(entry, "get_note", {"collection": "col_a", "note_id": original.id})
        assert read["content"] == "Keep this content"
        listed = call_note_tool(entry, "list_notes", {"collection": "col_a"})
        assert [item["id"] for item in listed["notes"]] == [original.id]
        try:
            result = call_note_tool(entry, name, args)
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            assert "error" in result, "Read-only identities must never become owner in tool worker threads"
    finally:
        reset_actor(token)
    assert len(notes.list_notes("col_a")) == 1
    assert notes.get_note(original.id).title == "Original"
    assert notes.get_content(original.id) == "Keep this content"


@pytest.mark.parametrize("entry", ["mcp", "chat_async", "chat_sync", "chat_sync_in_loop"])
@pytest.mark.parametrize("name", ["list_notes", "get_note", "create_note", "update_note"])
def test_hidden_collection_is_unavailable_through_any_note_tool_entry(monkeypatch, entry, name):
    from src.identity import Actor, reset_actor, set_actor
    from src.identity import acl

    hidden = notes.create_note("col_b", "Hidden")
    notes.save_content(hidden.id, "Private content")
    monkeypatch.setattr(acl, "collection_allows", lambda col, tier, role: col == "col_a")
    args = {"collection": "col_b"}
    if name in {"get_note", "update_note"}:
        args["note_id"] = hidden.id
    if name in {"create_note", "update_note"}:
        args["title"] = "Changed"
    token = set_actor(Actor(id="limited-key", kind="key", role_id="member"))
    try:
        result = call_note_tool(entry, name, args)
        assert "error" in result
        assert "Private content" not in json.dumps(result)
    finally:
        reset_actor(token)
    assert len(notes.list_notes("col_b")) == 1
    assert notes.get_note(hidden.id).title == "Hidden"
    assert notes.get_content(hidden.id) == "Private content"


@pytest.mark.parametrize("entry", ["mcp", "chat_async", "chat_sync", "chat_sync_in_loop"])
def test_write_allowed_actor_can_create_read_and_revise_its_collection(monkeypatch, entry):
    from src.identity import Actor, reset_actor, set_actor
    from src.identity import acl

    monkeypatch.setattr(acl, "collection_allows", lambda col, tier, role: col == "col_a" and tier in {"read", "write"})
    token = set_actor(Actor(id="writer-key", kind="key", role_id="member"))
    try:
        created = call_note_tool(entry, "create_note", {"collection": "col_a", "title": "Draft", "content": "Original"})
        assert "error" not in created
        read = call_note_tool(entry, "get_note", {"collection": "col_a", "note_id": created["id"]})
        assert read["content"] == "Original"
        updated = call_note_tool(entry, "update_note", {"collection": "col_a", "note_id": created["id"], "content": "Revised"})
        assert "error" not in updated
        assert notes.get_content(created["id"]) == "Revised"
    finally:
        reset_actor(token)


@pytest.mark.parametrize("quick", [False, True])
def test_saved_note_references_are_internal_context_not_assistant_reply_examples(tmp_path, monkeypatch, quick):
    store = SessionStore(str(tmp_path / "references.db"))
    session = store.create_session("Notes", collections=["col_a"], session_id="quick_col_a" if quick else None)
    agent = ChatboxAgent(session_store=store, chat_llm=MagicMock(), agentic_service=MagicMock())
    monkeypatch.setattr(agent, "_build_catalog_text", lambda *a, **k: "")
    store.add_message(session.id, "user", "Save a Note")
    first = store.add_message(session.id, "assistant", "Saved the Note.", metadata={
        "note_changes": [
            {"id": "note-a", "collection": "col_a", "title": "Draft"},
            {"id": "note-b", "collection": "col_b", "title": "Outside scope"},
        ],
    })
    store.add_message(session.id, "user", "Rename it")
    store.add_message(session.id, "assistant", "Renamed it.", metadata={
        "note_changes": [{"id": "note-a", "collection": "col_a", "title": "Final", "change": "internal-change-detail"}],
    })
    store.add_message(session.id, "user", "Revise that Note")

    assert agent._store_message_to_llm_dict(first)["content"] == "Saved the Note."
    messages = agent._build_messages(session.id, "Revise that Note", collections=["col_a"], mode="direct" if quick else "agentic")
    assistant_texts = [m["content"] for m in messages if m["role"] == "assistant"]
    assert assistant_texts == ["Saved the Note.", "Renamed it."]
    references = [m["content"] for m in messages if m["role"] == "system" and "note-a" in m["content"]]
    assert len(references) == 1
    assert "col_a" in references[0] and "Final" in references[0]
    assert "note-b" not in references[0] and "Draft" not in references[0]
    assert "internal-change-detail" not in references[0]
    assert "internal" in references[0].lower()
    assert "user-facing" in references[0].lower()
    assert all(m.role != "system" for m in store.get_messages(session.id))


def test_interrupted_stream_keeps_successful_note_changes(tmp_path):
    from src.chatbox.stream_handler import ChatStreamHandler

    store = SessionStore(str(tmp_path / "interrupt.db"))
    session = store.create_session("Interrupted")
    store.add_message(session.id, "assistant", "", metadata={"tool_calls": [{"id": "write"}]})
    store.add_message(session.id, "tool", "Saved", metadata={"tool_call_id": "write"})
    agent = ChatboxAgent(session_store=store, chat_llm=MagicMock(), agentic_service=MagicMock())
    changes = [{"id": "saved-note", "collection": "col_a", "title": "Saved"}]

    async def stream(*a, **k):
        yield {"type": "tool_result", "tool": "create_note", "note_changes": changes}

    agent.chat_stream = stream

    async def consume():
        async for _ in ChatStreamHandler(agent).handle(session.id, "Save"):
            pass

    asyncio.run(consume())
    assert store.get_messages(session.id)[-1].metadata["note_changes"] == changes


def test_successful_write_survives_an_empty_forced_answer(tmp_path, monkeypatch):
    import src.chatbox.agent as agent_module

    store = SessionStore(str(tmp_path / "forced.db"))
    session = store.create_session("Forced", collections=["col_a"])
    llm = MagicMock()
    llm._model = "test-model"
    llm._client.chat.completions.create.side_effect = [
        _tool_round("create_note", '{"collection":"col_a","title":"Saved","content":"Body"}'),
    ]
    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())
    monkeypatch.setattr(agent, "_has_meetings", lambda *a: False)
    monkeypatch.setattr(agent_module, "_MAX_TOOL_ROUNDS", 1)
    monkeypatch.setattr(agent_module, "_force_generate_answer", lambda *a: "")

    async def collect():
        return [e async for e in agent.chat_stream(session.id, "Save", mode="direct", thinking=False)]

    events = asyncio.run(collect())
    assert store.get_messages(session.id)[-1].metadata["note_changes"] == events[-1]["note_changes"]


@pytest.mark.parametrize("mode", ["agentic", "direct"])
def test_stream_records_all_successful_note_writes_and_next_turn_can_revise(tmp_path, monkeypatch, mode):
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = store.create_session("Notes", collections=["col_a"])
    llm = MagicMock()
    llm._model = "test-model"
    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())
    monkeypatch.setattr(agent, "_has_meetings", lambda *a: False)
    monkeypatch.setattr(agent, "_build_catalog_text", lambda *a, **k: "")

    async def collect(text):
        return [e async for e in agent.chat_stream(session.id, text, mode=mode, thinking=False, collections=["col_a"])]

    llm._client.chat.completions.create.side_effect = [
        _tool_round("create_note", json.dumps({"collection": "Alpha", "title": "First", "content": "Original", "collection_from_user": True})),
        _tool_round("create_note", json.dumps({"collection": "Alpha", "title": "Second", "content": "Other", "collection_from_user": True})),
        _text_round("Saved"),
    ]
    events = asyncio.run(collect("Save two Notes in Alpha"))
    done = next(e for e in events if e["type"] == "done")
    expected = [{"id": n.id, "collection": n.collection, "title": n.title} for n in notes.list_notes("col_a")]
    changes = done.get("note_changes", [])
    assert len(changes) == 2
    assert sorted(changes, key=lambda n: n["id"]) == sorted(expected, key=lambda n: n["id"])
    writes = [e for e in events if e["type"] == "tool_result" and e["tool"] == "create_note"]
    assert all(len(e["note_changes"]) == 1 for e in writes)
    assert store.get_messages(session.id)[-1].metadata["note_changes"] == changes

    target = next(n for n in changes if n["title"] == "First")
    llm._client.chat.completions.create.side_effect = [
        _tool_round("get_note", json.dumps({"collection": "col_a", "note_id": target["id"]})),
        _tool_round("update_note", json.dumps({"collection": "col_a", "note_id": target["id"], "content": "Revised"})),
        _tool_round("update_note", json.dumps({"collection": "col_a", "note_id": target["id"], "title": "Final"})),
        _tool_round("update_note", json.dumps({"collection": "col_a", "note_id": "missing-note", "title": "Missing"})),
        _text_round("Updated"),
    ]
    revised = asyncio.run(collect("Revise that first Note and rename it"))
    assert len(notes.list_notes("col_a")) == 2
    assert notes.get_content(target["id"]) == "Revised"
    changes = [{**target, "title": "Final"}]
    assert next(e for e in revised if e["type"] == "done")["note_changes"] == changes
    assert store.get_messages(session.id)[-1].metadata["note_changes"] == changes
    failed = [e for e in revised if e["type"] == "tool_result" and "missing-note" in e.get("content", "")]
    assert failed and not failed[-1].get("note_changes")
    assert failed[-1]["status"] == "error"
    # Stable IDs live in internal context, rather than assistant reply examples.
    history_message = store.get_messages(session.id)[-1]
    assert agent._store_message_to_llm_dict(history_message)["content"] == "Updated"
    context = agent._build_messages(session.id, "Revise it again", collections=["col_a"], mode=mode)
    assert any(m["role"] == "system" and target["id"] in m["content"] for m in context)
