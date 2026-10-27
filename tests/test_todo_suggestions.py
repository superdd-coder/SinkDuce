"""Timeline smart to-do suggestions (per-chain, debounced LLM).

Run: pytest tests/test_todo_suggestions.py -v --tb=short
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _main_chain(client: TestClient, coll: str) -> str:
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


def test_todo_suggestion_state_table_in_schema():
    assert "todo_suggestion_state" in EXPECTED_TABLES
    _setup_collection("sug-schema-1")
    conn = sqlite3.connect(str(_db_path("sug-schema-1")))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "todo_suggestion_state" in tables


def test_get_suggestions_empty_ready_or_idle():
    coll = "sug-get-empty"
    _setup_collection(coll)
    client = _client()
    chain_id = _main_chain(client, coll)
    resp = client.get(
        f"/api/file-mgmt/{coll}/chains/{chain_id}/todo-suggestions"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chain_id"] == chain_id
    assert body["status"] in ("idle", "ready")
    assert body["suggestions"] == []


def test_build_chain_context_ordered_nodes_and_messages():
    from src.file_mgmt.todo_suggestions import build_chain_context

    coll = "sug-ctx-1"
    _setup_collection(coll)
    client = _client()
    chain_id = _main_chain(client, coll)
    n1 = _create_node(client, coll, chain_id, "Kickoff", order=1)
    n2 = _create_node(client, coll, chain_id, "Review docs", order=2)
    client.post(
        f"/api/file-mgmt/{coll}/nodes/{n1['node_id']}/messages",
        json={"body": "First note", "owner_type": "node", "owner_id": n1["node_id"]},
    )
    client.post(
        f"/api/file-mgmt/{coll}/nodes/{n2['node_id']}/messages",
        json={"body": "Need checklist", "owner_type": "node", "owner_id": n2["node_id"]},
    )

    ctx = build_chain_context(coll, chain_id)
    assert [n["title"] for n in ctx["nodes"]] == ["Kickoff", "Review docs"]
    assert "First note" in (ctx["nodes"][0]["messages"][0] if ctx["nodes"][0]["messages"] else "")
    assert isinstance(ctx["open_todo_titles"], list)


def test_generate_stores_ready_suggestions_from_llm():
    from src.file_mgmt import todo_suggestions as ts

    coll = "sug-gen-1"
    _setup_collection(coll)
    client = _client()
    chain_id = _main_chain(client, coll)
    _create_node(client, coll, chain_id, "Plan sprint", order=1)

    fake_llm = MagicMock()
    fake_llm.generate.return_value = json.dumps(
        [
            {"title": "Draft plan", "body": "Write sprint goals"},
            {"title": "Assign owners", "body": "- Alice\n- Bob"},
        ]
    )

    with patch.object(ts, "_get_enrichment_llm", return_value=fake_llm):
        with patch.object(ts, "TODO_SUGGEST_DEBOUNCE_SEC", 0):
            ts.generate_todo_suggestions_now(coll, chain_id)

    resp = client.get(
        f"/api/file-mgmt/{coll}/chains/{chain_id}/todo-suggestions"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert len(body["suggestions"]) == 2
    assert body["suggestions"][0]["title"] == "Draft plan"
    assert body["suggestions"][0]["suggestion_id"]
    assert body["suggestions"][1]["body"] == "- Alice\n- Bob"
    fake_llm.generate.assert_called_once()


def test_fingerprint_skip_does_not_recall_llm():
    from src.file_mgmt import todo_suggestions as ts

    coll = "sug-fp-1"
    _setup_collection(coll)
    client = _client()
    chain_id = _main_chain(client, coll)
    _create_node(client, coll, chain_id, "Only node", order=1)

    fake_llm = MagicMock()
    fake_llm.generate.return_value = json.dumps(
        [{"title": "Next", "body": "Do it"}]
    )

    with patch.object(ts, "_get_enrichment_llm", return_value=fake_llm):
        ts.generate_todo_suggestions_now(coll, chain_id)
        ts.generate_todo_suggestions_now(coll, chain_id)

    assert fake_llm.generate.call_count == 1


def test_create_todo_with_suggestion_id_consumes_item():
    from src.file_mgmt import todo_suggestions as ts

    coll = "sug-consume-1"
    _setup_collection(coll)
    client = _client()
    chain_id = _main_chain(client, coll)
    _create_node(client, coll, chain_id, "Work", order=1)

    fake_llm = MagicMock()
    fake_llm.generate.return_value = json.dumps(
        [
            {"title": "Keep me", "body": "a"},
            {"title": "Use me", "body": "b"},
        ]
    )
    with patch.object(ts, "_get_enrichment_llm", return_value=fake_llm):
        ts.generate_todo_suggestions_now(coll, chain_id)

    sug = client.get(
        f"/api/file-mgmt/{coll}/chains/{chain_id}/todo-suggestions"
    ).json()["suggestions"]
    use_id = next(s["suggestion_id"] for s in sug if s["title"] == "Use me")

    resp = client.post(
        f"/api/file-mgmt/{coll}/todos",
        json={
            "title": "Use me",
            "body": "b",
            "target_chain_id": chain_id,
            "suggestion_id": use_id,
        },
    )
    assert resp.status_code == 201, resp.text

    left = client.get(
        f"/api/file-mgmt/{coll}/chains/{chain_id}/todo-suggestions"
    ).json()["suggestions"]
    assert len(left) == 1
    assert left[0]["title"] == "Keep me"


def test_schedule_marks_pending_without_blocking():
    from src.file_mgmt import todo_suggestions as ts

    coll = "sug-sched-1"
    _setup_collection(coll)
    client = _client()
    chain_id = _main_chain(client, coll)

    timers: list = []

    class FakeTimer:
        def __init__(self, interval, fn, args=None, kwargs=None):
            self.interval = interval
            self.fn = fn
            self.args = args or ()
            self.kwargs = kwargs or {}
            self.daemon = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            return None

    with patch.object(ts.threading, "Timer", FakeTimer):
        ts.schedule_todo_suggestion_refresh(coll, chain_id)

    body = client.get(
        f"/api/file-mgmt/{coll}/chains/{chain_id}/todo-suggestions"
    ).json()
    assert body["status"] == "pending"
    assert len(timers) == 1
    assert timers[0].interval == ts.TODO_SUGGEST_DEBOUNCE_SEC
