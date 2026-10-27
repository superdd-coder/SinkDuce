"""Note-create HITL gate: decision rule, store, peek, HTTP route, MCP content."""

from __future__ import annotations

import asyncio

import pytest

from src.chatbox import collection_resolve
from src.chatbox.note_create_confirm import (
    note_create_needs_confirm,
    note_create_confirm_store,
    peek_note_for_create,
)


class TestNoteCreateNeedsConfirm:
    def test_quick_never_confirms(self):
        for match in ("exact", "id", "prefix", "fuzzy"):
            assert (
                note_create_needs_confirm(
                    is_quick=True, match=match, collection_from_user=False
                )
                is False
            )

    def test_user_named_exact_target_no_confirm(self):
        for match in ("exact", "id", "prefix"):
            assert (
                note_create_needs_confirm(
                    is_quick=False, match=match, collection_from_user=True
                )
                is False
            )

    def test_fuzzy_always_confirms(self):
        assert (
            note_create_needs_confirm(
                is_quick=False, match="fuzzy", collection_from_user=True
            )
            is True
        )

    def test_agent_chosen_target_confirms(self):
        for match in ("exact", "id", "prefix", "fuzzy"):
            assert (
                note_create_needs_confirm(
                    is_quick=False, match=match, collection_from_user=False
                )
                is True
            )


class TestNoteCreateConfirmStore:
    def test_approve(self):
        store = note_create_confirm_store
        cid = store.create({"title": "Kickoff", "collection_name": "Helios"})
        assert cid.startswith("ncc_")

        async def _run():
            wait_task = asyncio.create_task(store.wait(cid, timeout=5))
            await asyncio.sleep(0.05)
            assert store.resolve(cid, True)
            return await wait_task

        assert asyncio.run(_run()) is True

    def test_decline(self):
        store = note_create_confirm_store
        cid = store.create({"title": "X"})

        async def _run():
            wait_task = asyncio.create_task(store.wait(cid, timeout=5))
            await asyncio.sleep(0.05)
            store.resolve(cid, False)
            return await wait_task

        assert asyncio.run(_run()) is False

    def test_unknown_id(self):
        assert note_create_confirm_store.resolve("ncc_missing", True) is False

    def test_timeout_declines(self):
        store = note_create_confirm_store
        cid = store.create({"title": "X"})

        async def _run():
            return await store.wait(cid, timeout=0.05)

        assert asyncio.run(_run()) is False

    def test_wait_result_carries_override(self):
        store = note_create_confirm_store
        cid = store.create({"title": "X"})

        async def _run():
            wait_task = asyncio.create_task(store.wait_result(cid, timeout=5))
            await asyncio.sleep(0.05)
            assert store.resolve(cid, True, override="col_mine")
            return await wait_task

        approved, override = asyncio.run(_run())
        assert approved is True
        assert override == "col_mine"

    def test_wait_result_without_override_is_none(self):
        store = note_create_confirm_store
        cid = store.create({"title": "X"})

        async def _run():
            wait_task = asyncio.create_task(store.wait_result(cid, timeout=5))
            await asyncio.sleep(0.05)
            store.resolve(cid, True)
            return await wait_task

        approved, override = asyncio.run(_run())
        assert approved is True
        assert override is None

    def test_wait_still_returns_bool(self):
        store = note_create_confirm_store
        cid = store.create({"title": "X"})

        async def _run():
            wait_task = asyncio.create_task(store.wait(cid, timeout=5))
            await asyncio.sleep(0.05)
            store.resolve(cid, True, override="col_mine")
            return await wait_task

        assert asyncio.run(_run()) is True


class TestPeekNoteForCreate:
    DIRECTORY = [
        {"id": "col_helios", "name": "Helios"},
        {"id": "default", "name": "Default"},
    ]

    def test_ok_payload(self, monkeypatch):
        monkeypatch.setattr(
            collection_resolve,
            "build_collection_directory",
            lambda: list(self.DIRECTORY),
        )
        out = peek_note_for_create("helios", "Kickoff", "## Decisions\n- ship")
        assert "error" not in out
        assert out["collection"] == "col_helios"
        assert out["collection_name"] == "Helios"
        assert out["title"] == "Kickoff"
        assert "Decisions" in out["preview"]

    def test_error_payload(self, monkeypatch):
        monkeypatch.setattr(
            collection_resolve,
            "build_collection_directory",
            lambda: list(self.DIRECTORY),
        )
        out = peek_note_for_create("Atlantis", "T", "c")
        assert "error" in out
        assert "collections" in out


class TestNoteCreateConfirmHttp:
    def test_post_resolves_pending(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.routes.sessions import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app)
        cid = note_create_confirm_store.create({"title": "X"})
        resp = client.post(
            "/api/chat/note-create-confirm",
            json={"confirm_id": cid, "approved": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["approved"] is True

    def test_post_unknown_404(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.routes.sessions import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app)
        resp = client.post(
            "/api/chat/note-create-confirm",
            json={"confirm_id": "ncc_missing", "approved": True},
        )
        assert resp.status_code == 404
        assert "note-create" in str(resp.json()).lower()

    def test_post_carries_collection_override_to_store(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.routes.sessions import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app)
        store = note_create_confirm_store
        cid = store.create({"title": "X"})

        async def _run():
            wait_task = asyncio.create_task(store.wait_result(cid, timeout=5))
            await asyncio.sleep(0.05)
            resp = client.post(
                "/api/chat/note-create-confirm",
                json={
                    "confirm_id": cid,
                    "approved": True,
                    "collection": "col_mine",
                },
            )
            assert resp.status_code == 200, resp.text
            return await wait_task

        approved, override = asyncio.run(_run())
        assert approved is True
        assert override == "col_mine"


# ── MCP create_note with content ───────────────────────────────


def _distill_block(source_note_id: str) -> str:
    return (
        ':::distill-block{"id":"b1","source":"' + source_note_id + '"}\n'
        "Key points.\n"
        ":::"
    )


class TestMcpCreateNoteContent:
    @pytest.fixture(autouse=True)
    def cleanup_notes(self, monkeypatch):
        from unittest.mock import MagicMock

        import src.services as services_mod

        mock_db = MagicMock()
        mock_db.collection_exists.return_value = True
        monkeypatch.setattr(
            services_mod.services, "db", mock_db, raising=False
        )

        from src.notes.store import NOTES_DIR

        existing: set[str] = set()
        if NOTES_DIR.exists():
            existing = {d.name for d in NOTES_DIR.iterdir() if d.is_dir()}
        yield
        if NOTES_DIR.exists():
            for d in NOTES_DIR.iterdir():
                if d.is_dir() and d.name not in existing:
                    import shutil

                    shutil.rmtree(d, ignore_errors=True)

    def test_create_with_content_and_refs(self):
        import asyncio
        import json

        from src.notes import store as nstore
        from src.mcp.tools.notes import create_note, get_note

        async def _run():
            empty = await create_note("ncc-content-col", "Target")
            target_id = json.loads(empty)["id"]
            made = await create_note(
                "ncc-content-col",
                title="Source",
                content=_distill_block(target_id),
            )
            full = await get_note("ncc-content-col", json.loads(made)["id"])
            return target_id, json.loads(full)

        target_id, payload = asyncio.run(_run())
        assert payload["content"] == _distill_block(target_id)
        assert payload["references"][0]["source_note_id"] == target_id
        assert nstore.get_referenced_by(target_id)  # backlink recorded

    def test_create_without_content_stays_empty(self):
        import asyncio
        import json

        from src.mcp.tools.notes import create_note, get_note

        async def _run():
            made = await create_note("ncc-content-col", title="Empty one")
            return await get_note("ncc-content-col", json.loads(made)["id"])

        payload = json.loads(asyncio.run(_run()))
        assert payload["content"] == ""


# ── Stream-path gate integration ──────────────────────────────


def _ns(**kw):
    from types import SimpleNamespace

    return SimpleNamespace(**kw)


def _stream_chunks(chunks):
    yield from chunks


def _text_round(text: str):
    return _stream_chunks(
        [
            _ns(choices=[_ns(delta=_ns(content=text, tool_calls=None, reasoning_content=None), finish_reason=None)]),
            _ns(choices=[_ns(delta=_ns(content=None, tool_calls=None, reasoning_content=None), finish_reason="stop")]),
        ]
    )


def _tool_round(name: str, arguments: str):
    tc = _ns(index=0, id="call_note_1", function=_ns(name=name, arguments=arguments))
    return _stream_chunks(
        [
            _ns(choices=[_ns(delta=_ns(content=None, tool_calls=[tc], reasoning_content=None), finish_reason=None)]),
            _ns(choices=[_ns(delta=_ns(content=None, tool_calls=None, reasoning_content=None), finish_reason="tool_calls")]),
        ]
    )


def _make_agent():
    from unittest.mock import MagicMock

    from src.chatbox.agent import ChatboxAgent

    store = MagicMock()
    store.count_dialogue_turns.return_value = 0
    store.get_session.return_value.collections = []
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()
    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())
    return agent, llm


DIRECTORY = [
    {"id": "col_helios", "name": "Helios"},
    {"id": "col_mine", "name": "Mine"},
    {"id": "default", "name": "Default"},
]


def _patch_directory(monkeypatch):
    from src.chatbox import collection_resolve

    monkeypatch.setattr(
        collection_resolve,
        "build_collection_directory",
        lambda: list(DIRECTORY),
    )


class TestNoteCreateStreamGate:
    def test_meeting_user_named_collection_creates_directly(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            _tool_round(
                "create_note",
                json.dumps(
                    {
                        "collection": "Helios",
                        "title": "Kickoff",
                        "content": "decisions",
                        "collection_from_user": True,
                    }
                ),
            ),
            _text_round("Noted."),
        ]

        captured: dict = {}

        async def _fake_dispatch(name, args, *, selected_collections=None):
            captured["call"] = (name, dict(args))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            return [
                ev
                async for ev in agent.chat_stream(
                    "meeting_m1", "记到 Helios", mode="direct", collections=[]
                )
            ]

        events = asyncio.run(_run())
        kinds = {e["type"] for e in events}
        assert "note_create_confirm" not in kinds
        assert captured["call"][0] == "create_note"
        assert captured["call"][1]["collection"] == "col_helios"

    def test_agentic_agent_chosen_target_confirms_then_decline(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            _tool_round(
                "create_note",
                json.dumps(
                    {"collection": "Helios", "title": "T", "content": "c"}
                ),
            ),
            _text_round("Card declined, ok."),
        ]

        dispatch_calls: list[tuple] = []

        async def _fake_dispatch(name, args, *, selected_collections=None):
            dispatch_calls.append((name, dict(args)))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            out = []
            agen = agent.chat_stream("sess1", "记个笔记")
            try:
                async for ev in agen:
                    out.append(ev)
                    if ev["type"] == "note_create_confirm":
                        note_create_confirm_store.resolve(ev["confirm_id"], False)
            finally:
                await agen.aclose()
            return out

        events = asyncio.run(_run())
        assert any(e["type"] == "note_create_confirm" for e in events)
        card = next(e for e in events if e["type"] == "note_create_confirm")
        assert card["collection_name"] == "Helios"
        assert dispatch_calls == []

    def test_agentic_approve_creates_with_resolved_id(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            _tool_round(
                "create_note",
                json.dumps(
                    {"collection": "Helios", "title": "T", "content": "c"}
                ),
            ),
            _text_round("Created."),
        ]

        captured: dict = {}

        async def _fake_dispatch(name, args, *, selected_collections=None):
            captured["call"] = (name, dict(args))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            out = []
            agen = agent.chat_stream("sess2", "记个笔记")
            try:
                async for ev in agen:
                    out.append(ev)
                    if ev["type"] == "note_create_confirm":
                        note_create_confirm_store.resolve(ev["confirm_id"], True)
            finally:
                await agen.aclose()
            return out

        events = asyncio.run(_run())
        assert any(e["type"] == "note_create_confirm" for e in events)
        assert captured["call"][1]["collection"] == "col_helios"

    def test_quick_chat_locks_and_skips_card(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            _tool_round(
                "create_note",
                json.dumps(
                    {
                        "collection": "evil_col",
                        "title": "T",
                        "content": "c",
                        "collection_from_user": True,
                    }
                ),
            ),
            _text_round("Noted."),
        ]

        captured: dict = {}

        async def _fake_dispatch(name, args, *, selected_collections=None):
            captured["call"] = (name, dict(args))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            return [
                ev
                async for ev in agent.chat_stream(
                    "quick_s1", "记个笔记", mode="direct", collections=["col_mine"]
                )
            ]

        events = asyncio.run(_run())
        kinds = {e["type"] for e in events}
        assert "note_create_confirm" not in kinds
        assert captured["call"][1]["collection"] == "col_mine"


class TestNoteCreateOverrideOnApprove:
    """Approve + dropdown override: create in the user-picked collection."""

    def _agent_with_note_round(self, llm):
        import json

        seen_tool_contents: list[str] = []

        def _side_effect(**kwargs):
            msgs = kwargs.get("messages") or []
            tool_msgs = [m for m in msgs if m.get("role") == "tool"]
            if tool_msgs:
                seen_tool_contents.append(str(tool_msgs[-1].get("content")))
                return _text_round("Done.")
            return _tool_round(
                "create_note",
                json.dumps(
                    {"collection": "Helios", "title": "T", "content": "c"}
                ),
            )

        llm._client.chat.completions.create.side_effect = _side_effect
        return seen_tool_contents

    def _run_stream(self, monkeypatch, agent, session_id: str, override):
        import src.chatbox.agent as agent_mod

        captured: dict = {}
        dispatch_calls: list[tuple] = []

        async def _fake_dispatch(name, args, *, selected_collections=None):
            dispatch_calls.append((name, dict(args)))
            captured["call"] = (name, dict(args))
            return '{"status": "ok"}'

        monkeypatch.setattr(
            agent_mod, "dispatch_structure_tool_async", _fake_dispatch
        )

        async def _run():
            out = []
            agen = agent.chat_stream(session_id, "记个笔记")
            try:
                async for ev in agen:
                    out.append(ev)
                    if ev["type"] == "note_create_confirm":
                        note_create_confirm_store.resolve(
                            ev["confirm_id"], True, override=override
                        )
            finally:
                await agen.aclose()
            return out

        return _run, captured, dispatch_calls

    def test_override_creates_in_picked_collection_and_annotates(
        self, monkeypatch
    ):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        seen = self._agent_with_note_round(llm)

        _run, captured, _ = self._run_stream(
            monkeypatch, agent, "sess_ov1", "col_mine"
        )
        asyncio.run(_run())

        assert captured["call"][1]["collection"] == "col_mine"
        assert seen, "round 2 must carry the tool result"
        payload = json.loads(seen[-1])
        # Factual, model-readable result only — no cryptic flags.
        assert payload.get("collection_name") == "Mine"
        assert "user_confirmed_collection" not in payload
        assert "user_changed_collection" not in payload
        assert "message" not in payload

    def test_plain_approve_marks_target_as_user_confirmed(self, monkeypatch):
        """Allow without re-picking: the card approval IS the user
        confirming the agent's target — the model must never re-ask."""
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        seen = self._agent_with_note_round(llm)

        _run, captured, _ = self._run_stream(
            monkeypatch, agent, "sess_ov4", None
        )
        asyncio.run(_run())

        assert captured["call"][1]["collection"] == "col_helios"
        assert seen
        payload = json.loads(seen[-1])
        assert payload.get("collection_name") == "Helios"
        assert "user_confirmed_collection" not in payload
        assert "user_changed_collection" not in payload
        assert "message" not in payload

    def test_unknown_override_returns_error_without_dispatch(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        seen = self._agent_with_note_round(llm)

        _run, _captured, dispatch_calls = self._run_stream(
            monkeypatch, agent, "sess_ov2", "col_ghost"
        )
        asyncio.run(_run())

        assert dispatch_calls == []
        assert seen
        assert "error" in json.loads(seen[-1])

    def test_override_same_as_original_skips_annotation(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        seen = self._agent_with_note_round(llm)

        _run, captured, _ = self._run_stream(
            monkeypatch, agent, "sess_ov3", "col_helios"
        )
        asyncio.run(_run())

        assert captured["call"][1]["collection"] == "col_helios"
        assert seen
        assert "user_changed_collection" not in json.loads(seen[-1])


class TestMeetingListCollections:
    def test_meeting_session_routes_list_collections_through_free_dispatch(
        self, monkeypatch
    ):
        """Meeting chat must bypass the Quick-Chat lock for list_collections.

        The routing seam is the forcing-free dispatch — execute_structure_tool
        re-applies force_collection_args and would reject the call with
        "list_collections is not available in Quick Chat".
        """
        import asyncio

        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            _tool_round("list_collections", "{}"),
            _text_round("Here are the collections."),
        ]

        import src.chatbox.agent as agent_mod

        dispatch_calls: list[tuple] = []
        exec_calls: list[tuple] = []
        selected_scopes: list = []

        async def _fake_dispatch(name, args, *, selected_collections=None):
            dispatch_calls.append((name, dict(args)))
            selected_scopes.append(selected_collections)
            return '{"collections": [{"id": "col_helios", "name": "Helios"}]}'

        async def _fake_exec(name, args, *, mode, forced_collection):
            exec_calls.append((name,))
            return "{}"

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)
        monkeypatch.setattr(agent_mod, "execute_structure_tool_async", _fake_exec)

        async def _run():
            return [
                ev
                async for ev in agent.chat_stream(
                    "meeting_m1", "有哪些 collection？", mode="direct", collections=[]
                )
            ]

        events = asyncio.run(_run())
        assert dispatch_calls and dispatch_calls[0][0] == "list_collections"
        assert selected_scopes == [[]]
        assert exec_calls == [], "meeting sessions must not re-force collections"
        assert any(e.get("type") == "done" for e in events)


class TestUserNamedCollection:
    def test_name_in_message(self):
        from src.chatbox.note_create_confirm import user_named_collection

        assert user_named_collection("UI测试", "帮我记到UI测试里") is True
        assert user_named_collection("Helios", "记到 helios 那个 collection") is True

    def test_name_absent_downgrades_flag(self):
        from src.chatbox.note_create_confirm import user_named_collection

        # Model guessed "default" — user never said it.
        assert user_named_collection("Default", "列一个 todo list") is False

    def test_empty_inputs(self):
        from src.chatbox.note_create_confirm import user_named_collection

        assert user_named_collection("", "anything") is False
        assert user_named_collection("X", "") is False


class TestMeetingGuessedCollectionStillConfirms:
    def test_flag_true_but_name_absent_fires_card(self, monkeypatch):
        """The default-collection bug: model lies with from_user=true."""
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            _tool_round(
                "create_note",
                json.dumps(
                    {
                        "collection": "default",
                        "title": "Todo list",
                        "content": "…",
                        "collection_from_user": True,
                    }
                ),
            ),
            _text_round("Here is your list."),
        ]

        dispatch_calls: list[tuple] = []

        async def _fake_dispatch(name, args, *, selected_collections=None):
            dispatch_calls.append((name, dict(args)))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            out = []
            agen = agent.chat_stream(
                "meeting_m1",
                "帮我列出一个todo list 把所有要做的功能列出来",
                mode="direct",
                collections=[],
            )
            try:
                async for ev in agen:
                    out.append(ev)
                    if ev["type"] == "note_create_confirm":
                        note_create_confirm_store.resolve(ev["confirm_id"], False)
            finally:
                await agen.aclose()
            return out

        events = asyncio.run(_run())
        card = next(
            (e for e in events if e["type"] == "note_create_confirm"), None
        )
        assert card is not None, "guessed target must fire the card"
        assert card["collection_name"] == "Default"
        assert dispatch_calls == []

    def test_flag_true_and_name_present_skips_card(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            _tool_round(
                "create_note",
                json.dumps(
                    {
                        "collection": "Helios",
                        "title": "T",
                        "content": "c",
                        "collection_from_user": True,
                    }
                ),
            ),
            _text_round("Noted."),
        ]

        captured: dict = {}

        async def _fake_dispatch(name, args, *, selected_collections=None):
            captured["call"] = (name, dict(args))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            return [
                ev
                async for ev in agent.chat_stream(
                    "meeting_m1", "把这事记到 Helios 的笔记里", mode="direct", collections=[]
                )
            ]

        events = asyncio.run(_run())
        kinds = {e["type"] for e in events}
        assert "note_create_confirm" not in kinds
        assert captured["call"][1]["collection"] == "col_helios"


class TestNoteCreateLoopGuard:
    def _create_call(self, title: str) -> str:
        import json

        return _tool_round(
            "create_note",
            json.dumps(
                {
                    "collection": "Helios",
                    "title": title,
                    "content": "body",
                    "collection_from_user": True,
                }
            ),
        )

    def test_duplicate_title_rejected_without_dispatch(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            self._create_call("Todo List"),
            self._create_call("Todo List"),
            _text_round("Done."),
        ]

        dispatch_calls: list[tuple] = []

        async def _fake_dispatch(name, args, *, selected_collections=None):
            dispatch_calls.append((name, dict(args)))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            return [
                ev
                async for ev in agent.chat_stream(
                    "meeting_m1",
                    "把这事记到 Helios",
                    mode="direct",
                    collections=[],
                )
            ]

        asyncio.run(_run())
        assert len(dispatch_calls) == 1, "duplicate create must not dispatch"

    def test_declined_title_blocks_identical_retry(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()

        def _self_chosen_call(title: str) -> str:
            # No collection_from_user → agent-chosen target → card fires.
            return _tool_round(
                "create_note",
                json.dumps(
                    {
                        "collection": "Helios",
                        "title": title,
                        "content": "body",
                    }
                ),
            )

        llm._client.chat.completions.create.side_effect = [
            _self_chosen_call("Todo List"),
            _self_chosen_call("Todo List"),
            _text_round("OK, stopped."),
        ]

        dispatch_calls: list[tuple] = []

        async def _fake_dispatch(name, args, *, selected_collections=None):
            dispatch_calls.append((name, dict(args)))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            out = []
            agen = agent.chat_stream(
                "meeting_m1", "把这事记到 Helios", mode="direct", collections=[]
            )
            try:
                async for ev in agen:
                    out.append(ev)
                    if ev["type"] == "note_create_confirm":
                        note_create_confirm_store.resolve(ev["confirm_id"], False)
            finally:
                await agen.aclose()
            return out

        events = asyncio.run(_run())
        cards = [e for e in events if e["type"] == "note_create_confirm"]
        assert len(cards) == 1, "identical retry must not re-show the card"
        assert dispatch_calls == []

    def test_hard_cap_three_creates_per_turn(self, monkeypatch):
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        llm._client.chat.completions.create.side_effect = [
            self._create_call("T1"),
            self._create_call("T2"),
            self._create_call("T3"),
            self._create_call("T4"),
            _text_round("All done."),
        ]

        dispatch_calls: list[tuple] = []

        async def _fake_dispatch(name, args, *, selected_collections=None):
            dispatch_calls.append((name, dict(args)))
            return '{"status": "ok"}'

        import src.chatbox.agent as agent_mod

        monkeypatch.setattr(agent_mod, "dispatch_structure_tool_async", _fake_dispatch)

        async def _run():
            return [
                ev
                async for ev in agent.chat_stream(
                    "meeting_m1",
                    "把四件事分别记到 Helios",
                    mode="direct",
                    collections=[],
                )
            ]

        asyncio.run(_run())
        assert len(dispatch_calls) == 3, "cap = 3 creates per turn"


class TestToolRoundMessageOrder:
    def test_user_message_precedes_trajectory_and_result_is_last(
        self, monkeypatch
    ):
        """Regression: the user request used to be re-appended AFTER the
        tool trajectory, re-arming the task at the end of every round
        (duplicate create_note writes, endless lookups)."""
        import asyncio
        import json

        _patch_directory(monkeypatch)
        agent, llm = _make_agent()
        rounds: list[list[dict]] = []

        def _side_effect(**kwargs):
            msgs = kwargs.get("messages") or []
            rounds.append(msgs)
            n = len(rounds)
            args = json.dumps(
                {
                    "collection": "Helios",
                    "title": "T",
                    "content": "c",
                    "collection_from_user": True,
                }
            )
            if n in (1, 2):
                return _tool_round("create_note", args)
            return _text_round("done")

        llm._client.chat.completions.create.side_effect = _side_effect

        import src.chatbox.agent as agent_mod

        async def _fake_dispatch(name, args, *, selected_collections=None):
            return '{"status": "ok"}'

        monkeypatch.setattr(
            agent_mod, "dispatch_structure_tool_async", _fake_dispatch
        )

        async def _run():
            return [
                ev
                async for ev in agent.chat_stream(
                    "meeting_m1",
                    "把这事记到 Helios",
                    mode="direct",
                    collections=[],
                )
            ]

        asyncio.run(_run())
        assert len(rounds) >= 2
        round2 = rounds[1]
        roles = [m.get("role") for m in round2]
        # Exactly one user message, and it comes BEFORE the trajectory.
        assert roles.count("user") == 1
        assert roles.index("user") < roles.index("assistant")
        # The newest tool result ends the sequence — not the user request.
        assert roles[-1] == "tool"
