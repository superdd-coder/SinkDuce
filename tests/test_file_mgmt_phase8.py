"""Phase 8 tests: file detail enrichment (nodes meta + system_version messages).

Run: pytest tests/test_file_mgmt_phase8.py -v --tb=short
"""

from __future__ import annotations

import shutil
import uuid

import pytest
from fastapi.testclient import TestClient

from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db, get_db


@pytest.fixture(autouse=True)
def cleanup_test_collections():
    """Remove collection directories created during tests."""
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


def _get_main_chain_id(client: TestClient, coll: str) -> str:
    resp = client.get(f"/api/file-mgmt/{coll}/chains")
    assert resp.status_code == 200
    chains = resp.json()
    main = [c for c in chains if c["is_main"]]
    assert len(main) == 1
    return main[0]["chain_id"]


def _create_file_record(coll_id: str, file_id: str) -> None:
    """Insert file + version + system_version message (mirrors upload pipeline)."""
    now = "2026-07-28T00:00:00+00:00"
    version_id = uuid.uuid4().hex
    conn = get_db(coll_id)
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO file_versions
               (version_id, file_id, version_no, storage_file_id,
                archived, commit_message, created_by, created_at)
               VALUES (?, ?, 1, ?, 0, 'initial', 'local', ?)""",
            (version_id, file_id, f"doc_{file_id[:6]}.pdf", now),
        )
        conn.execute(
            """INSERT INTO files
               (file_id, current_version_id, is_definitive, archived,
                unsupported, created_by, version)
               VALUES (?, ?, 0, 0, 0, 'local', 1)""",
            (file_id, version_id),
        )
        mid = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO messages
               (message_id, owner_type, owner_id, source_node_id, body,
                author_type, author_id, created_at, edited_at, edited_by, version)
               VALUES (?, 'system_version', ?, NULL, 'Initial upload',
                'system', 'local', ?, NULL, NULL, 1)""",
            (mid, file_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def test_file_detail_includes_system_version_messages_and_node_meta():
    from src.main import app

    coll = "p8-detail"
    _setup_collection(coll)
    client = TestClient(app)

    resp = client.post(f"/api/file-mgmt/{coll}/groups", json={"name": "Finance"})
    assert resp.status_code == 201, resp.text
    group = resp.json()
    gid = group["group_id"]

    main_id = _get_main_chain_id(client, coll)
    resp = client.post(
        f"/api/file-mgmt/{coll}/chains/{main_id}/nodes",
        json={
            "group_id": gid,
            "node_type": "event",
            "title": "Q1 Close",
            "order": 1,
        },
    )
    assert resp.status_code == 201, resp.text
    node = resp.json()
    nid = node["node_id"]

    fid = uuid.uuid4().hex
    _create_file_record(coll, fid)

    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{nid}/files",
        json={"file_id": fid},
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        f"/api/file-mgmt/{coll}/files/{fid}/messages",
        json={
            "owner_type": "file",
            "owner_id": fid,
            "body": "Needs review",
            "author_type": "user",
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/file-mgmt/{coll}/files/{fid}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()

    owner_types = {m["owner_type"] for m in detail["messages"]}
    assert "system_version" in owner_types
    assert "file" in owner_types
    bodies = [m["body"] for m in detail["messages"]]
    assert "Needs review" in bodies
    assert any("Initial" in (b or "") for b in bodies)

    assert len(detail["nodes"]) >= 1
    n = detail["nodes"][0]
    assert n["node_id"] == nid
    assert n["title"] == "Q1 Close"
    assert n["group_id"] == gid
    assert n.get("group_name") == "Finance"
    assert n.get("chain_id") == main_id
    assert n.get("chain_title") in ("Main", None) or isinstance(
        n.get("chain_title"), (str, type(None))
    )

    assert len(detail["versions"]) >= 1


def test_file_detail_404():
    from src.main import app

    coll = "p8-404"
    _setup_collection(coll)
    client = TestClient(app)
    resp = client.get(f"/api/file-mgmt/{coll}/files/{'a' * 32}")
    assert resp.status_code == 404
