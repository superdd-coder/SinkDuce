"""Collection To-do list API tests.

Run: pytest tests/test_file_mgmt_todos.py -v --tb=short
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.file_mgmt.routes import router as file_mgmt_router
from src.file_mgmt.store import COLLECTIONS_DIR, EXPECTED_TABLES, init_collection_db


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


def _setup_collection(coll_id: str) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll_id, f"Test {coll_id}")
    init_collection_db(coll_id)


def _db_path(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "meta.db"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(file_mgmt_router, prefix="/api/file-mgmt")
    return TestClient(app)


def _get_main_chain_id(client: TestClient, coll: str) -> str:
    resp = client.get(f"/api/file-mgmt/{coll}/chains")
    assert resp.status_code == 200
    main = [c for c in resp.json() if c["is_main"]]
    assert len(main) == 1
    return main[0]["chain_id"]


def _create_node(client, coll, chain_id, title, order=1):
    resp = client.post(
        f"/api/file-mgmt/{coll}/chains/{chain_id}/nodes",
        json={"node_type": "event", "title": title, "order": order},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_todos_table_in_expected_and_created():
    assert "todos" in EXPECTED_TABLES
    _setup_collection("todo-schema-1")
    conn = sqlite3.connect(str(_db_path("todo-schema-1")))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "todos" in tables


def test_create_todo_defaults_to_main_chain():
    coll = "todo-create-1"
    _setup_collection(coll)
    client = _client()
    main_id = _get_main_chain_id(client, coll)

    resp = client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "Review budget", "body": "Check line items"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Review budget"
    assert body["body"] == "Check line items"
    assert body["done"] is False
    assert body["ddl"] is None
    assert body["target_chain_id"] is None
    assert body["chain_id"] == main_id
    assert body["is_main_chain"] is True
    assert body["completed_node_id"] is None


def test_completed_todo_cannot_edit_body():
    coll = "todo-ro-1"
    _setup_collection(coll)
    client = _client()
    tid = client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "Lock me", "body": "draft"},
    ).json()["todo_id"]
    client.patch(f"/api/file-mgmt/{coll}/todos/{tid}", json={"done": True})
    resp = client.patch(
        f"/api/file-mgmt/{coll}/todos/{tid}",
        json={"body": "hacked"},
    )
    assert resp.status_code == 400
    # reopen ok
    resp = client.patch(
        f"/api/file-mgmt/{coll}/todos/{tid}",
        json={"done": False},
    )
    assert resp.status_code == 200
    resp = client.patch(
        f"/api/file-mgmt/{coll}/todos/{tid}",
        json={"body": "ok after reopen"},
    )
    assert resp.status_code == 200
    assert resp.json()["body"] == "ok after reopen"


def test_list_todos_sort_ddl_then_no_ddl_by_created():
    coll = "todo-sort-1"
    _setup_collection(coll)
    client = _client()

    client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "NoDDL-old"},
    )
    time.sleep(0.02)
    client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "Far", "ddl": "2026-12-01"},
    )
    client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "Near", "ddl": "2026-08-10"},
    )
    time.sleep(0.02)
    client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "NoDDL-new"},
    )

    resp = client.get(f"/api/file-mgmt/{coll}/todos")
    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json() if not t["done"]]
    assert titles == ["Near", "Far", "NoDDL-new", "NoDDL-old"]


def test_complete_todo_sets_done_and_completed_at():
    coll = "todo-done-1"
    _setup_collection(coll)
    client = _client()

    created = client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "Ship it"},
    ).json()
    tid = created["todo_id"]

    resp = client.patch(
        f"/api/file-mgmt/{coll}/todos/{tid}",
        json={"done": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["done"] is True
    assert body["completed_at"] is not None


def test_link_node_on_completed_todo():
    coll = "todo-link-1"
    _setup_collection(coll)
    client = _client()
    main_id = _get_main_chain_id(client, coll)
    node = _create_node(client, coll, main_id, "Done work", order=1)

    todo = client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "Done work"},
    ).json()
    tid = todo["todo_id"]
    client.patch(f"/api/file-mgmt/{coll}/todos/{tid}", json={"done": True})

    resp = client.post(
        f"/api/file-mgmt/{coll}/todos/{tid}/link-node",
        json={"node_id": node["node_id"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_node_id"] == node["node_id"]


def test_create_todo_on_branch_chain():
    coll = "todo-branch-1"
    _setup_collection(coll)
    client = _client()
    main_id = _get_main_chain_id(client, coll)
    parent = _create_node(client, coll, main_id, "Fork", order=1)

    chain_resp = client.post(
        f"/api/file-mgmt/{coll}/chains",
        json={
            "parent_chain_id": main_id,
            "parent_node_id": parent["node_id"],
            "title": "Side work",
        },
    )
    assert chain_resp.status_code == 201, chain_resp.text
    branch_id = chain_resp.json()["chain_id"]

    resp = client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "Branch task", "target_chain_id": branch_id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["target_chain_id"] == branch_id
    assert body["chain_id"] == branch_id
    assert body["is_main_chain"] is False
    assert body["chain_title"] == "Side work"


def test_delete_todo():
    coll = "todo-del-1"
    _setup_collection(coll)
    client = _client()
    tid = client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "Temp"},
    ).json()["todo_id"]

    resp = client.delete(f"/api/file-mgmt/{coll}/todos/{tid}")
    assert resp.status_code == 204
    listed = client.get(f"/api/file-mgmt/{coll}/todos").json()
    assert all(t["todo_id"] != tid for t in listed)


def test_empty_title_rejected():
    coll = "todo-empty-1"
    _setup_collection(coll)
    client = _client()
    resp = client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={"title": "   "},
    )
    assert resp.status_code in (400, 422)
