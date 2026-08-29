"""MCP collection todo tools (list / create / update / delete)."""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import patch

import pytest

from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db


def _run(coro):
    return asyncio.run(coro)


def _unwrap(out):
    sc = getattr(out, "structured_content", None)
    if sc is not None:
        return sc
    return out


@pytest.fixture(autouse=True)
def cleanup_test_collections():
    existing: set[str] = set()
    if COLLECTIONS_DIR.exists():
        existing = {d.name for d in COLLECTIONS_DIR.iterdir() if d.is_dir()}
    yield
    if COLLECTIONS_DIR.exists():
        for d in COLLECTIONS_DIR.iterdir():
            if d.is_dir() and d.name not in existing:
                shutil.rmtree(d, ignore_errors=True)


def _setup(coll_id: str, name: str | None = None) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll_id, name or f"Test {coll_id}")
    init_collection_db(coll_id)


@pytest.fixture
def speakers_dir(tmp_path):
    root = tmp_path / "speakers"
    root.mkdir()
    with patch("src.speakers.store.SPEAKERS_DIR", root):
        yield root


def test_list_todos_defaults_to_open_only():
    from src.file_mgmt.models import TodoCreate
    from src.file_mgmt import service as fm
    from src.mcp.tools import file_mgmt as mod

    coll = "mcp-todo-list-1"
    _setup(coll)
    from src.file_mgmt.models import TodoUpdate

    fm.create_todo(coll, TodoCreate(title="Open A"))
    done = fm.create_todo(coll, TodoCreate(title="Done B"))
    fm.update_todo(coll, done.todo_id, TodoUpdate(done=True))

    with patch.object(mod, "_require_fm_collection", return_value=None):
        out = _unwrap(_run(mod.list_todos(coll)))
    assert "error" not in out
    titles = [t["title"] for t in out["todos"]]
    assert titles == ["Open A"]
    assert out["total"] == 1
    assert out["todos"][0]["collection_id"] == coll
    assert out["todos"][0]["collection_name"] == f"Test {coll}"


def test_list_todos_include_done_and_done_filter():
    from src.file_mgmt.models import TodoCreate, TodoUpdate
    from src.file_mgmt import service as fm
    from src.mcp.tools import file_mgmt as mod

    coll = "mcp-todo-list-2"
    _setup(coll)
    fm.create_todo(coll, TodoCreate(title="Open"))
    done = fm.create_todo(coll, TodoCreate(title="Done"))
    fm.update_todo(coll, done.todo_id, TodoUpdate(done=True))

    with patch.object(mod, "_require_fm_collection", return_value=None):
        all_out = _unwrap(_run(mod.list_todos(coll, include_done=True)))
        only_done = _unwrap(_run(mod.list_todos(coll, done=True)))
    assert [t["title"] for t in all_out["todos"]] == ["Open", "Done"]
    assert [t["title"] for t in only_done["todos"]] == ["Done"]


def test_list_todos_mine_requires_me(speakers_dir):
    from src.mcp.tools import file_mgmt as mod

    coll = "mcp-todo-mine-1"
    _setup(coll)
    with patch.object(mod, "_require_fm_collection", return_value=None):
        out = _unwrap(_run(mod.list_todos(coll, mine=True)))
    assert "error" in out
    assert "Me person" in out["error"]


def test_list_todos_mine_filters_assignee(speakers_dir):
    from src.file_mgmt.models import TodoCreate
    from src.file_mgmt import service as fm
    from src.mcp.tools import file_mgmt as mod
    from src.speakers.store import create_person, set_me_person_id

    coll = "mcp-todo-mine-2"
    _setup(coll)
    me = create_person("Me User")
    other = create_person("Other")
    set_me_person_id(me.id)
    fm.create_todo(coll, TodoCreate(title="Mine", assignee_person_id=me.id))
    fm.create_todo(coll, TodoCreate(title="Theirs", assignee_person_id=other.id))
    fm.create_todo(coll, TodoCreate(title="Unassigned"))

    with patch.object(mod, "_require_fm_collection", return_value=None):
        out = _unwrap(_run(mod.list_todos(coll, mine=True)))
    assert "error" not in out
    titles = [t["title"] for t in out["todos"]]
    assert titles == ["Mine"]
    assert out["todos"][0]["assignee_name"] == "Me User"


def test_list_todos_all_collections_skips_missing_db():
    from src.file_mgmt.models import TodoCreate
    from src.file_mgmt import service as fm
    from src.mcp.tools import file_mgmt as mod

    a = "mcp-todo-all-a"
    b = "mcp-todo-all-b"
    _setup(a, "Alpha")
    _setup(b, "Beta")
    fm.create_todo(a, TodoCreate(title="A1"))
    fm.create_todo(b, TodoCreate(title="B1"))

    metas = [
        {"id": a, "name": "Alpha", "created_at": "1"},
        {"id": b, "name": "Beta", "created_at": "2"},
        {"id": "mcp-todo-ghost", "name": "Ghost", "created_at": "3"},
    ]
    with patch("src.collections.store.list_collections_meta", return_value=metas):
        out = _unwrap(_run(mod.list_todos()))
    assert "error" not in out
    titles = {(t["collection_id"], t["title"]) for t in out["todos"]}
    assert titles == {(a, "A1"), (b, "B1")}


def test_create_update_delete_todo():
    from src.mcp.tools import file_mgmt as mod

    coll = "mcp-todo-crud-1"
    _setup(coll)
    with patch.object(mod, "_require_fm_collection", return_value=None):
        created = _unwrap(
            _run(
                mod.create_todo(
                    coll,
                    title="Ship docs",
                    body="Write the note",
                    ddl="2026-09-01",
                )
            )
        )
        assert "error" not in created
        tid = created["todo"]["todo_id"]
        assert created["todo"]["title"] == "Ship docs"
        assert created["todo"]["body"] == "Write the note"
        assert created["todo"]["ddl"] == "2026-09-01"
        assert created["todo"]["assignee_person_id"] is None

        patched = _unwrap(
            _run(
                mod.update_todo(
                    coll,
                    tid,
                    title="Ship v2",
                    body="New desc",
                    ddl="2026-09-08",
                )
            )
        )
        assert patched["todo"]["title"] == "Ship v2"
        assert patched["todo"]["body"] == "New desc"
        assert patched["todo"]["ddl"] == "2026-09-08"

        deleted = _unwrap(_run(mod.delete_todo(coll, tid)))
        assert "error" not in deleted
        listed = _unwrap(_run(mod.list_todos(coll, include_done=True)))
        assert listed["todos"] == []


def test_delete_todo_accepts_todo_ids_batch():
    from src.file_mgmt.models import TodoCreate
    from src.file_mgmt import service as fm
    from src.mcp.tools import file_mgmt as mod

    coll = "mcp-todo-del-batch"
    _setup(coll)
    a = fm.create_todo(coll, TodoCreate(title="A"))
    b = fm.create_todo(coll, TodoCreate(title="B"))
    with patch.object(mod, "_require_fm_collection", return_value=None):
        out = _unwrap(
            _run(mod.delete_todo(coll, todo_ids=[a.todo_id, b.todo_id]))
        )
        listed = _unwrap(_run(mod.list_todos(coll, include_done=True)))
    assert "error" not in out
    assert set(out["todo_ids"]) == {a.todo_id, b.todo_id}
    assert listed["todos"] == []


def test_create_todo_batch_todos_array():
    from src.mcp.tools import file_mgmt as mod

    coll = "mcp-todo-create-batch"
    _setup(coll)
    with patch.object(mod, "_require_fm_collection", return_value=None):
        out = _unwrap(
            _run(
                mod.create_todo(
                    coll,
                    todos=[
                        {"title": "One", "ddl": "2026-09-01"},
                        {"title": "Two", "body": "desc"},
                    ],
                )
            )
        )
        listed = _unwrap(_run(mod.list_todos(coll)))
    assert "error" not in out
    assert out["total"] == 2
    assert {t["title"] for t in out["todos"]} == {"One", "Two"}
    assert {t["title"] for t in listed["todos"]} == {"One", "Two"}


def test_update_todo_batch_updates_array():
    from src.file_mgmt.models import TodoCreate
    from src.file_mgmt import service as fm
    from src.mcp.tools import file_mgmt as mod

    coll = "mcp-todo-upd-batch"
    _setup(coll)
    a = fm.create_todo(coll, TodoCreate(title="A"))
    b = fm.create_todo(coll, TodoCreate(title="B"))
    with patch.object(mod, "_require_fm_collection", return_value=None):
        out = _unwrap(
            _run(
                mod.update_todo(
                    coll,
                    updates=[
                        {"todo_id": a.todo_id, "done": True},
                        {"todo_id": b.todo_id, "title": "B2"},
                    ],
                )
            )
        )
    assert "error" not in out
    titles = {t["todo_id"]: t for t in out["todos"]}
    assert titles[a.todo_id]["done"] is True
    assert titles[b.todo_id]["title"] == "B2"


def test_create_todo_requires_collection():
    from src.mcp.tools import file_mgmt as mod

    out = _unwrap(_run(mod.create_todo("", title="Nope")))
    assert "error" in out
    assert "collection is required" in out["error"]


def test_create_todo_assign_to_me(speakers_dir):
    from src.mcp.tools import file_mgmt as mod
    from src.speakers.store import create_person, set_me_person_id

    coll = "mcp-todo-assign-me"
    _setup(coll)
    me = create_person("Jethro")
    set_me_person_id(me.id)
    with patch.object(mod, "_require_fm_collection", return_value=None):
        out = _unwrap(_run(mod.create_todo(coll, title="My task", assign_to_me=True)))
    assert "error" not in out
    assert out["todo"]["assignee_person_id"] == me.id
    assert out["todo"]["assignee_name"] == "Jethro"
