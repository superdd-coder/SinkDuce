"""Version-dir layout: files/{file_id}/{version_id}/{basename}."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

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


def _fake_txt_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _setup_collection(coll: str) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll, f"Test {coll}")
    init_collection_db(coll)


def _get_plain_folder_id(client: TestClient, coll: str) -> str:
    create = client.post(
        f"/api/file-mgmt/{coll}/folders",
        json={"name": f"Docs-{uuid.uuid4().hex[:6]}", "kind": "plain"},
    )
    assert create.status_code in (200, 201), create.text
    return create.json()["folder_id"]


def test_upload_and_version_use_version_id_subdirs():
    from src.main import app

    coll = f"layout-ver-{uuid.uuid4().hex[:8]}"
    _setup_collection(coll)
    client = TestClient(app)
    folder_id = _get_plain_folder_id(client, coll)

    resp1 = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("note.txt", _fake_txt_bytes("v1 body"), "text/plain")},
        data={"folder_id": folder_id},
    )
    assert resp1.status_code == 201, resp1.text
    body1 = resp1.json()
    file_id = body1["file_id"]
    v1_id = body1.get("version_id") or body1.get("current_version_id")
    assert v1_id

    root = Path("data/collections") / coll / "files" / file_id
    v1_blob = root / v1_id / "note.txt"
    assert v1_blob.is_file(), f"expected {v1_blob}"
    assert v1_blob.read_text(encoding="utf-8") == "v1 body"
    # No flat blob at file root with same name
    assert not (root / "note.txt").is_file()

    resp2 = client.post(
        f"/api/file-mgmt/{coll}/files/{file_id}/versions",
        files={"file": ("note.txt", _fake_txt_bytes("v2 body"), "text/plain")},
        data={"commit_message": "bump"},
    )
    assert resp2.status_code == 201, resp2.text
    body2 = resp2.json()
    v2_id = body2.get("version_id") or body2.get("current_version_id")
    assert v2_id

    v2_blob = root / v2_id / "note.txt"
    assert v2_blob.is_file()
    assert v2_blob.read_text(encoding="utf-8") == "v2 body"
    # v1 still intact (same basename, different dirs)
    assert v1_blob.is_file()
    assert v1_blob.read_text(encoding="utf-8") == "v1 body"

    conn = get_db(coll)
    try:
        rows = conn.execute(
            "SELECT version_id, version_no, storage_file_id FROM file_versions "
            "WHERE file_id=? ORDER BY version_no",
            (file_id,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["storage_file_id"] == "note.txt"
        assert rows[1]["storage_file_id"] == "note.txt"
        assert rows[0]["version_id"] == v1_id
        assert rows[1]["version_id"] == v2_id
    finally:
        conn.close()

    # Preview by version_id
    prev = client.get(
        f"/api/documents/preview/__file__:{file_id}",
        params={
            "collection": coll,
            "storage_file": "note.txt",
            "version_id": v1_id,
        },
    )
    assert prev.status_code == 200, prev.text
    assert prev.content == b"v1 body"

    prev2 = client.get(
        f"/api/documents/preview/__file__:{file_id}",
        params={"collection": coll, "version_id": v2_id},
    )
    assert prev2.status_code == 200, prev2.text
    assert prev2.content == b"v2 body"


def test_resolve_note_label_storage_finds_flat_md():
    """Note ingest writes Title.md but migration may store 'Note: Title' as storage_file_id."""
    from src.file_mgmt.storage_paths import resolve_version_blob

    coll = f"layout-note-{uuid.uuid4().hex[:8]}"
    _setup_collection(coll)
    file_id = uuid.uuid4().hex
    version_id = uuid.uuid4().hex
    root = Path("data/collections") / coll / "files" / file_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "My_Note.md").write_text("# hello note", encoding="utf-8")

    # Wrong storage name (display label) — must still find the .md
    found = resolve_version_blob(
        coll, file_id, version_id, "Note: My Note"
    )
    assert found is not None
    assert found.name == "My_Note.md"
    assert found.read_text(encoding="utf-8") == "# hello note"


def test_migrate_flat_layout_to_version_dirs():
    from src.file_mgmt.storage_paths import (
        _layout_migrated,
        migrate_collection_layout,
        resolve_version_blob,
    )

    coll = f"layout-mig-{uuid.uuid4().hex[:8]}"
    _setup_collection(coll)
    _layout_migrated.discard(coll)

    file_id = uuid.uuid4().hex
    v1 = uuid.uuid4().hex
    v2 = uuid.uuid4().hex
    root = Path("data/collections") / coll / "files" / file_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.txt").write_text("only-latest", encoding="utf-8")
    (root / "parsed.txt").write_text("parsed-current", encoding="utf-8")

    conn = get_db(coll)
    try:
        with conn:
            conn.execute(
                """INSERT INTO files
                   (file_id, current_version_id, is_definitive, archived,
                    unsupported, created_by, version)
                   VALUES (?, NULL, 0, 0, 0, 'local', 1)""",
                (file_id,),
            )
            conn.execute(
                """INSERT INTO file_versions
                   (version_id, file_id, version_no, storage_file_id,
                    archived, commit_message, created_by, created_at)
                   VALUES (?, ?, 1, 'report.txt', 1, NULL, 'local', '2020-01-01T00:00:00')""",
                (v1, file_id),
            )
            conn.execute(
                """INSERT INTO file_versions
                   (version_id, file_id, version_no, storage_file_id,
                    archived, commit_message, created_by, created_at)
                   VALUES (?, ?, 2, 'report.txt', 0, NULL, 'local', '2020-01-02T00:00:00')""",
                (v2, file_id),
            )
            conn.execute(
                "UPDATE files SET current_version_id=? WHERE file_id=?",
                (v2, file_id),
            )
    finally:
        conn.close()

    result = migrate_collection_layout(coll)
    assert result.get("moved", 0) >= 1

    # Current (winner of shared basename) owns the blob
    cur = resolve_version_blob(coll, file_id, v2, "report.txt")
    assert cur is not None
    assert cur.read_text(encoding="utf-8") == "only-latest"
    assert cur.parent.name == v2

    # Loser has no unique blob (legacy overwrite)
    old = resolve_version_blob(coll, file_id, v1, "report.txt")
    assert old is None

    # parsed.txt moved to current version dir
    assert (root / v2 / "parsed.txt").is_file()
    assert not (root / "parsed.txt").is_file()
