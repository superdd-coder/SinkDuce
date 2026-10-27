"""Rollback to historical version hard-deletes later versions."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.file_mgmt.store import COLLECTIONS_DIR, get_db, init_collection_db


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


def _setup_collection(coll: str) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll, f"Test {coll}")
    init_collection_db(coll)


def _folder(client: TestClient, coll: str) -> str:
    r = client.post(
        f"/api/file-mgmt/{coll}/folders",
        json={"name": f"Docs-{uuid.uuid4().hex[:6]}", "kind": "plain"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["folder_id"]


def test_rollback_makes_target_current_and_hard_deletes_later():
    from src.main import app

    coll = f"rb-{uuid.uuid4().hex[:8]}"
    _setup_collection(coll)
    client = TestClient(app)
    folder_id = _folder(client, coll)

    # v1
    r1 = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("a.txt", b"version one", "text/plain")},
        data={"folder_id": folder_id},
    )
    assert r1.status_code == 201, r1.text
    file_id = r1.json()["file_id"]
    v1 = r1.json().get("version_id") or r1.json().get("current_version_id")
    assert v1

    # v2
    with patch("src.file_mgmt.service._mark_qdrant_chunks_archived", return_value=0):
        r2 = client.post(
            f"/api/file-mgmt/{coll}/files/{file_id}/versions",
            files={"file": ("a.txt", b"version two", "text/plain")},
            data={"commit_message": "v2"},
        )
    assert r2.status_code == 201, r2.text
    v2 = r2.json().get("version_id") or r2.json().get("current_version_id")
    assert v2 and v2 != v1

    # v3
    with patch("src.file_mgmt.service._mark_qdrant_chunks_archived", return_value=0):
        r3 = client.post(
            f"/api/file-mgmt/{coll}/files/{file_id}/versions",
            files={"file": ("a.txt", b"version three", "text/plain")},
            data={"commit_message": "v3"},
        )
    assert r3.status_code == 201, r3.text
    v3 = r3.json().get("version_id") or r3.json().get("current_version_id")
    assert v3

    root = Path("data/collections") / coll / "files" / file_id
    assert (root / v1 / "a.txt").is_file()
    assert (root / v2 / "a.txt").is_file()
    assert (root / v3 / "a.txt").is_file()

    with patch(
        "src.file_mgmt.service._restore_qdrant_version_as_current", return_value=0
    ), patch(
        "src.file_mgmt.service._delete_qdrant_chunks_by_version_id", return_value=0
    ):
        rb = client.post(
            f"/api/file-mgmt/{coll}/files/{file_id}/versions/{v1}/rollback"
        )
    assert rb.status_code == 200, rb.text
    body = rb.json()
    assert body["version_id"] == v1
    assert body["current"] is True
    assert set(body["deleted_version_ids"]) == {v2, v3}
    assert body["deleted_count"] == 2

    # DB: only v1 remains, current
    conn = get_db(coll)
    try:
        f = conn.execute(
            "SELECT current_version_id FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        assert f["current_version_id"] == v1
        rows = conn.execute(
            "SELECT version_id, archived, version_no FROM file_versions WHERE file_id=? ORDER BY version_no",
            (file_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["version_id"] == v1
        assert int(rows[0]["archived"] or 0) == 0
    finally:
        conn.close()

    # Disk: later blobs gone; v1 remains
    assert (root / v1 / "a.txt").is_file()
    assert (root / v1 / "a.txt").read_bytes() == b"version one"
    assert not (root / v2).exists()
    assert not (root / v3).exists()


def test_rollback_refuses_current_version():
    from src.main import app

    coll = f"rb-cur-{uuid.uuid4().hex[:8]}"
    _setup_collection(coll)
    client = TestClient(app)
    folder_id = _folder(client, coll)

    r1 = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("a.txt", b"only", "text/plain")},
        data={"folder_id": folder_id},
    )
    assert r1.status_code == 201
    file_id = r1.json()["file_id"]
    v1 = r1.json().get("version_id") or r1.json().get("current_version_id")

    rb = client.post(
        f"/api/file-mgmt/{coll}/files/{file_id}/versions/{v1}/rollback"
    )
    assert rb.status_code == 400
