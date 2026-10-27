"""Chat/Quick Chat HITL gate for delete_todo."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db


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


def _setup(coll: str) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll, f"Test {coll}")
    init_collection_db(coll)


class TestTodoDeleteConfirmStore:
    def test_approve(self):
        from src.chatbox.todo_delete_confirm import TodoDeleteConfirmStore

        store = TodoDeleteConfirmStore()
        cid = store.create(
            title="Ship docs",
            todo_id="t1",
            collection="col_a",
            collection_name="Alpha",
        )
        assert cid.startswith("tdc_")

        async def _run():
            wait_task = asyncio.create_task(store.wait(cid, timeout=5))
            await asyncio.sleep(0.05)
            assert store.resolve(cid, True)
            return await wait_task

        assert asyncio.run(_run()) is True

    def test_decline(self):
        from src.chatbox.todo_delete_confirm import TodoDeleteConfirmStore

        store = TodoDeleteConfirmStore()
        cid = store.create(
            title="X", todo_id="t1", collection="c", collection_name="C"
        )

        async def _run():
            wait_task = asyncio.create_task(store.wait(cid, timeout=5))
            await asyncio.sleep(0.05)
            store.resolve(cid, False)
            return await wait_task

        assert asyncio.run(_run()) is False

    def test_unknown_id(self):
        from src.chatbox.todo_delete_confirm import TodoDeleteConfirmStore

        store = TodoDeleteConfirmStore()
        assert store.resolve("missing", True) is False


class TestPeekTodoForDelete:
    def test_returns_title(self):
        from src.file_mgmt.models import TodoCreate
        from src.file_mgmt import service as fm
        from src.chatbox.todo_delete_confirm import peek_todo_for_delete

        coll = "tdc-peek-1"
        _setup(coll)
        todo = fm.create_todo(coll, TodoCreate(title="Weekly report"))
        out = peek_todo_for_delete(coll, todo.todo_id)
        assert "error" not in out
        assert out["title"] == "Weekly report"
        assert out["todo_id"] == todo.todo_id
        assert out["collection"] == coll
        assert out["collection_name"] == f"Test {coll}"

    def test_missing_todo(self):
        from src.chatbox.todo_delete_confirm import peek_todo_for_delete

        coll = "tdc-peek-2"
        _setup(coll)
        out = peek_todo_for_delete(coll, "no-such-todo")
        assert "error" in out


class TestTodoDeleteConfirmHttp:
    def test_post_resolves_pending(self):
        from src.api.routes.sessions import router
        from src.chatbox.todo_delete_confirm import todo_delete_confirm_store

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app)
        cid = todo_delete_confirm_store.create(
            title="X", todo_id="t1", collection="c", collection_name="C"
        )
        resp = client.post(
            "/api/chat/todo-delete-confirm",
            json={"confirm_id": cid, "approved": False},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["approved"] is False

    def test_post_unknown_404(self):
        from src.api.routes.sessions import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app)
        resp = client.post(
            "/api/chat/todo-delete-confirm",
            json={"confirm_id": "tdc_missing", "approved": True},
        )
        assert resp.status_code == 404
        assert "todo-delete" in str(resp.json()).lower()
