"""Phase 4 tests: file upload pipeline, versions, delete, Qdrant payload, retriever filter.

Run: pytest tests/test_file_mgmt_phase4.py -v --tb=short
"""

from __future__ import annotations

import io
import os
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db, get_db


# ── Fixtures ─────────────────────────────────────────────────────


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
    """Create a collection with meta.json + meta.db for testing.

    Also creates a Qdrant collection so ingest doesn't crash.
    """
    from src.collections.store import create_collection_meta
    from src.services import services

    create_collection_meta(coll_id, f"Test {coll_id}")
    init_collection_db(coll_id)

    # Ensure Qdrant collection exists for ingest tests
    if services.db and not services.db.collection_exists(coll_id):
        try:
            services.db.create_collection(coll_id, vector_size=384)
        except Exception:
            pass  # may already exist or Qdrant unavailable


def _get_main_chain_id(client: TestClient, coll: str) -> str:
    resp = client.get(f"/api/file-mgmt/{coll}/chains")
    assert resp.status_code == 200
    chains = resp.json()
    main = [c for c in chains if c["is_main"]]
    assert len(main) == 1
    return main[0]["chain_id"]


def _create_node(client, coll, chain_id, title, order=1,
                 group_id=None, node_type="event"):
    body = {"node_type": node_type, "title": title, "order": order}
    if group_id:
        body["group_id"] = group_id
    resp = client.post(f"/api/file-mgmt/{coll}/chains/{chain_id}/nodes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fake_pdf_bytes() -> bytes:
    """Create a minimal fake PDF for upload testing."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length 44 >>\nstream\n"
        b"BT /F1 24 Tf 100 700 Td (Test PDF content) Tj ET\n"
        b"endstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \n0000000115 00000 n \n0000000266 00000 n \n"
        b"0000000360 00000 n \ntrailer\n<< /Size 6 /Root 1 0 R >>\n"
        b"startxref\n419\n%%EOF"
    )


def _fake_txt_bytes(content: str = "This is a test document for Phase 4 upload testing.") -> bytes:
    return content.encode("utf-8")


def _create_file_record(coll_id: str, file_id: str, archived: int = 0) -> None:
    """Manually insert a file record + file_version into meta.db."""
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
               VALUES (?, ?, 0, ?, 0, 'local', 1)""",
            (file_id, version_id, archived),
        )
        conn.commit()
    finally:
        conn.close()


def _get_plain_folder_id(client: TestClient, coll: str) -> str:
    """Create and return a plain folder ID for upload testing."""
    resp = client.post(
        f"/api/file-mgmt/{coll}/folders",
        json={"name": "UploadFolder", "kind": "plain"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["folder_id"]


# ════════════════════════════════════════════════════════════════
# 1. upload_file_to_folder
# ════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    os.environ.get("SKIP_QDRANT_TESTS") == "1",
    reason="Qdrant not available"
)
def test_upload_to_folder_txt():
    """Upload a .txt file to a plain folder, verify DB records and Qdrant chunks."""
    from src.main import app

    coll = "p4-upload-txt"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    # Upload .txt file
    resp = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("test.txt", _fake_txt_bytes(), "text/plain")},
        data={"folder_id": folder_id},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "file_id" in data
    file_id = data["file_id"]
    assert data["unsupported"] is False

    # Verify files table
    conn = get_db(coll)
    try:
        f_row = conn.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
        assert f_row is not None
        assert f_row["unsupported"] == 0
        assert f_row["archived"] == 0

        # Verify file_versions
        v_row = conn.execute(
            "SELECT * FROM file_versions WHERE file_id=?", (file_id,)
        ).fetchone()
        assert v_row is not None
        assert v_row["version_no"] == 1

        # Verify file_paths
        p_row = conn.execute(
            "SELECT * FROM file_paths WHERE file_id=? AND folder_id=?",
            (file_id, folder_id),
        ).fetchone()
        assert p_row is not None
        assert p_row["source_node_id"] is None  # persistent path

        # Verify system version message
        m_row = conn.execute(
            "SELECT * FROM messages WHERE owner_type='system_version' AND owner_id=?",
            (file_id,),
        ).fetchone()
        assert m_row is not None

        # Verify disk file exists under files/{file_id}/{version_id}/
        from src.file_mgmt.storage_paths import resolve_version_blob

        blob = resolve_version_blob(
            coll, file_id, v_row["version_id"], v_row["storage_file_id"]
        )
        assert blob is not None and blob.is_file()
    finally:
        conn.close()


def test_upload_unsupported():
    """Upload a .zip file — verify unsupported=1, no Qdrant chunks."""
    from src.main import app

    coll = "p4-upload-zip"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    # Upload .zip file (not in supported_file_types)
    zip_bytes = b"PK\x03\x04\x14\x00\x00\x00\x00\x00fake zip content"
    resp = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("archive.zip", zip_bytes, "application/zip")},
        data={"folder_id": folder_id},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    file_id = data["file_id"]
    assert data["unsupported"] is True

    # Verify DB
    conn = get_db(coll)
    try:
        f_row = conn.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
        assert f_row is not None
        assert f_row["unsupported"] == 1
    finally:
        conn.close()


def test_upload_folder():
    """Upload folder with multiple files, verify virtual folder structure."""
    from src.main import app

    coll = "p4-upload-dir"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    # Upload 3 "files" in a folder
    files_data = [
        ("subdir/a.txt", _fake_txt_bytes("File A content")),
        ("subdir/b.txt", _fake_txt_bytes("File B content")),
        ("subdir/subsub/c.txt", _fake_txt_bytes("File C content")),
    ]
    files = [
        ("files", (path, io.BytesIO(content), "text/plain"))
        for path, content in files_data
    ]
    resp = client.post(
        f"/api/file-mgmt/{coll}/files/upload-folder",
        files=files,
        data={"parent_folder_id": folder_id},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert len(data) == 3

    # Verify virtual folder structure
    conn = get_db(coll)
    try:
        subdirs = conn.execute(
            "SELECT * FROM folders WHERE parent_folder_id=? AND kind='plain'",
            (folder_id,),
        ).fetchall()
        assert len(subdirs) == 1
        assert subdirs[0]["name"] == "subdir"
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# 2. upload_file_version
# ════════════════════════════════════════════════════════════════


def test_upload_version():
    """Upload file → upload new version → verify old archived, new current."""
    from src.main import app

    coll = "p4-version"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    # First upload
    resp1 = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("v1.txt", _fake_txt_bytes("Version 1"), "text/plain")},
        data={"folder_id": folder_id},
    )
    assert resp1.status_code == 201
    file_id = resp1.json()["file_id"]

    # Upload new version
    resp2 = client.post(
        f"/api/file-mgmt/{coll}/files/{file_id}/versions",
        files={"file": ("v2.txt", _fake_txt_bytes("Version 2"), "text/plain")},
        data={"commit_message": "Updated to v2"},
    )
    assert resp2.status_code == 201, resp2.text
    data2 = resp2.json()

    # Verify DB state
    conn = get_db(coll)
    try:
        # Old version archived
        old_vers = conn.execute(
            "SELECT * FROM file_versions WHERE file_id=? AND version_no=1",
            (file_id,),
        ).fetchone()
        assert old_vers is not None
        assert old_vers["archived"] == 1

        # New version
        new_vers = conn.execute(
            "SELECT * FROM file_versions WHERE file_id=? AND version_no=2",
            (file_id,),
        ).fetchone()
        assert new_vers is not None
        assert new_vers["archived"] == 0
        assert new_vers["commit_message"] == "Updated to v2"

        # current_version_id updated
        f_row = conn.execute(
            "SELECT * FROM files WHERE file_id=?", (file_id,),
        ).fetchone()
        assert f_row["current_version_id"] == new_vers["version_id"]

        # Version message created — exactly one system_version for this upload,
        # body = commit_message (not a separate owner_type=file message).
        m_rows = conn.execute(
            """SELECT * FROM messages
               WHERE owner_type='system_version' AND owner_id=?
               ORDER BY created_at DESC""",
            (file_id,),
        ).fetchall()
        # v1 "Initial upload" + v2 "Updated to v2"
        assert len(m_rows) == 2
        assert "Updated to v2" in (m_rows[0]["body"] or "")
        file_msgs = conn.execute(
            """SELECT * FROM messages
               WHERE owner_type='file' AND owner_id=?""",
            (file_id,),
        ).fetchall()
        assert len(file_msgs) == 0

        # Old version blob still on disk (version-level archive, not deleted)
        from src.file_mgmt.storage_paths import resolve_version_blob

        old_blob = resolve_version_blob(
            coll,
            file_id,
            old_vers["version_id"],
            old_vers["storage_file_id"],
        )
        assert old_blob is not None and old_blob.is_file()

        # File-level archive flag stays off — not moved into Archive folder
        assert f_row["archived"] == 0
    finally:
        conn.close()


def test_list_old_versions_and_delete_non_current():
    """Old versions list excludes current; delete removes blob + row, not whole file."""
    from src.main import app

    coll = "p4-old-ver-del"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    resp1 = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("a.txt", _fake_txt_bytes("A"), "text/plain")},
        data={"folder_id": folder_id},
    )
    assert resp1.status_code == 201
    file_id = resp1.json()["file_id"]

    resp2 = client.post(
        f"/api/file-mgmt/{coll}/files/{file_id}/versions",
        files={"file": ("b.txt", _fake_txt_bytes("B"), "text/plain")},
        data={"commit_message": "to b"},
    )
    assert resp2.status_code == 201, resp2.text

    # List old versions — only non-current
    list_resp = client.get(f"/api/file-mgmt/{coll}/old-versions")
    assert list_resp.status_code == 200, list_resp.text
    olds = list_resp.json()
    assert isinstance(olds, list)
    assert len(olds) == 1
    assert olds[0]["file_id"] == file_id
    assert olds[0]["version_no"] == 1
    old_version_id = olds[0]["version_id"]

    conn = get_db(coll)
    try:
        current_id = conn.execute(
            "SELECT current_version_id FROM files WHERE file_id=?", (file_id,)
        ).fetchone()["current_version_id"]
        assert old_version_id != current_id
        # files.archived still 0 — old version is not Archive-folder material
        assert (
            conn.execute(
                "SELECT archived FROM files WHERE file_id=?", (file_id,)
            ).fetchone()["archived"]
            == 0
        )
    finally:
        conn.close()

    # Cannot delete current version
    bad = client.delete(
        f"/api/file-mgmt/{coll}/files/{file_id}/versions/{current_id}"
    )
    assert bad.status_code == 400

    # Delete old version
    old_storage = olds[0]["storage_file_id"]
    del_resp = client.delete(
        f"/api/file-mgmt/{coll}/files/{file_id}/versions/{old_version_id}"
    )
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json().get("deleted") is True

    # Version row gone; file + current version remain
    conn = get_db(coll)
    try:
        assert not conn.execute(
            "SELECT 1 FROM file_versions WHERE version_id=?", (old_version_id,)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM file_versions WHERE version_id=?", (current_id,)
        ).fetchone()
    finally:
        conn.close()

    from src.file_mgmt.storage_paths import resolve_version_blob

    old_blob = resolve_version_blob(coll, file_id, old_version_id, old_storage)
    assert old_blob is None

    # List is empty after delete
    list2 = client.get(f"/api/file-mgmt/{coll}/old-versions")
    assert list2.status_code == 200
    assert list2.json() == []


def test_version_limit_warning():
    """Upload 21 versions → verify file still created but warning in result."""
    from src.main import app

    coll = "p4-version-limit"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    # First upload
    resp1 = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("v1.txt", _fake_txt_bytes("Version 1"), "text/plain")},
        data={"folder_id": folder_id},
    )
    assert resp1.status_code == 201
    file_id = resp1.json()["file_id"]

    # Upload 20 more versions (total 21)
    last_resp = None
    for i in range(20):
        last_resp = client.post(
            f"/api/file-mgmt/{coll}/files/{file_id}/versions",
            files={"file": (f"v{i+2}.txt", _fake_txt_bytes(f"Version {i+2}"), "text/plain")},
            data={"commit_message": f"v{i+2}"},
        )
        assert last_resp.status_code == 201

    # Total should be 21 versions
    conn = get_db(coll)
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM file_versions WHERE file_id=?", (file_id,),
        ).fetchone()
        assert count["c"] == 21
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# 3. delete_file
# ════════════════════════════════════════════════════════════════


def test_delete_file():
    """Upload → delete → verify all records gone."""
    from src.main import app

    coll = "p4-delete"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    # Upload
    resp = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("delete_me.txt", _fake_txt_bytes("Delete me"), "text/plain")},
        data={"folder_id": folder_id},
    )
    assert resp.status_code == 201
    file_id = resp.json()["file_id"]

    # Verify exists
    conn = get_db(coll)
    try:
        assert conn.execute("SELECT 1 FROM files WHERE file_id=?", (file_id,)).fetchone()
        assert conn.execute("SELECT 1 FROM file_versions WHERE file_id=?", (file_id,)).fetchone()
        assert conn.execute("SELECT 1 FROM file_paths WHERE file_id=?", (file_id,)).fetchone()
    finally:
        conn.close()

    # Delete
    resp_del = client.delete(f"/api/file-mgmt/{coll}/files/{file_id}")
    assert resp_del.status_code == 204

    # Verify all gone
    conn = get_db(coll)
    try:
        assert not conn.execute("SELECT 1 FROM files WHERE file_id=?", (file_id,)).fetchone()
        assert not conn.execute("SELECT 1 FROM file_versions WHERE file_id=?", (file_id,)).fetchone()
        assert not conn.execute("SELECT 1 FROM file_paths WHERE file_id=?", (file_id,)).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM messages WHERE owner_id=? AND owner_type IN ('file','system_version')",
            (file_id,),
        ).fetchone()
    finally:
        conn.close()

    # Verify disk deleted
    file_dir = Path("data/collections") / coll / "files" / file_id
    assert not file_dir.exists()


# ════════════════════════════════════════════════════════════════
# 4. update_file (is_definitive)
# ════════════════════════════════════════════════════════════════


def test_toggle_definitive():
    """Upload → set is_definitive=1 → verify."""
    from src.main import app

    coll = "p4-definitive"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    # Upload
    resp = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("def.txt", _fake_txt_bytes("Definitive test"), "text/plain")},
        data={"folder_id": folder_id},
    )
    assert resp.status_code == 201
    file_id = resp.json()["file_id"]
    version = resp.json()["version"]

    # Toggle definitive
    resp2 = client.patch(
        f"/api/file-mgmt/{coll}/files/{file_id}",
        json={"is_definitive": True, "version": version},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["is_definitive"] is True

    # Verify DB
    conn = get_db(coll)
    try:
        f_row = conn.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
        assert f_row["is_definitive"] == 1
    finally:
        conn.close()


def test_update_file_version_conflict():
    """Update with wrong version → 409 conflict."""
    from src.main import app

    coll = "p4-conflict"
    _setup_collection(coll)
    client = TestClient(app)

    folder_id = _get_plain_folder_id(client, coll)

    resp = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("conflict.txt", _fake_txt_bytes(), "text/plain")},
        data={"folder_id": folder_id},
    )
    assert resp.status_code == 201
    file_id = resp.json()["file_id"]

    # Wrong version
    resp2 = client.patch(
        f"/api/file-mgmt/{coll}/files/{file_id}",
        json={"is_definitive": True, "version": 999},
    )
    assert resp2.status_code == 409


# ════════════════════════════════════════════════════════════════
# 5. node upload (attach_file_to_node with upload)
# ════════════════════════════════════════════════════════════════


def test_node_upload():
    """Upload file via node attachment → verify file_nodes + derived paths."""
    from src.main import app

    coll = "p4-node-upload"
    _setup_collection(coll)
    client = TestClient(app)

    main_chain_id = _get_main_chain_id(client, coll)

    # Create a user group
    resp_g = client.post(
        f"/api/file-mgmt/{coll}/groups",
        json={"name": "Financial"},
    )
    assert resp_g.status_code == 201, resp_g.text
    group_id = resp_g.json()["group_id"]

    # Create a node with this group
    node = _create_node(client, coll, main_chain_id, "Q3 Report", order=1, group_id=group_id)
    node_id = node["node_id"]

    # Upload file to node
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{node_id}/files/upload",
        files={"file": ("report.txt", _fake_txt_bytes("Q3 Financial Report"), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    file_id = data["file_id"]
    # Must surface ingest task_id so frontend can poll / lock Preview-only tabs
    assert data.get("task_id"), f"expected task_id on node upload response, got {data!r}"

    # Verify file_nodes
    conn = get_db(coll)
    try:
        fn = conn.execute(
            "SELECT * FROM file_nodes WHERE file_id=? AND node_id=?",
            (file_id, node_id),
        ).fetchone()
        assert fn is not None

        # Verify derived paths
        paths = conn.execute(
            "SELECT * FROM file_paths WHERE file_id=? AND source_node_id=?",
            (file_id, node_id),
        ).fetchall()
        # At least group folder path
        assert len(paths) >= 1
        # One path should point to the group folder
        grp = conn.execute(
            "SELECT folder_id FROM node_groups WHERE group_id=?", (group_id,),
        ).fetchone()
        group_paths = [p for p in paths if p["folder_id"] == grp["folder_id"]]
        assert len(group_paths) == 1
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# 6. create_chain file check
# ════════════════════════════════════════════════════════════════


def test_create_chain_rejects_folder_with_files():
    """create_chain with bind_existing_folder_id that has files → 400."""
    from src.main import app

    coll = "p4-chain-file-check"
    _setup_collection(coll)
    client = TestClient(app)

    main_chain_id = _get_main_chain_id(client, coll)

    # Create a plain folder
    resp_f = client.post(
        f"/api/file-mgmt/{coll}/folders",
        json={"name": "HasFiles", "kind": "plain"},
    )
    assert resp_f.status_code == 201
    folder_id = resp_f.json()["folder_id"]

    # Manually upload a file to this folder (need file record)
    fid = uuid.uuid4().hex
    _create_file_record(coll, fid)

    # Add path to this folder
    conn = get_db(coll)
    try:
        with conn:
            conn.execute(
                """INSERT INTO file_paths
                   (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                   VALUES (?, ?, ?, 1, NULL, 'local')""",
                (uuid.uuid4().hex, fid, folder_id),
            )
    finally:
        conn.close()

    # Try to create a chain binding this folder
    node = _create_node(client, coll, main_chain_id, "Start Point", order=1)
    resp = client.post(
        f"/api/file-mgmt/{coll}/chains",
        json={
            "parent_chain_id": main_chain_id,
            "parent_node_id": node["node_id"],
            "title": "Should Fail",
            "bind_existing_folder_id": folder_id,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "files" in resp.json()["detail"].lower()


# ════════════════════════════════════════════════════════════════
# 7. end-to-end
# ════════════════════════════════════════════════════════════════


def test_end_to_end():
    """Upload → attach to node → update version → get detail → delete."""
    from src.main import app

    coll = "p4-e2e"
    _setup_collection(coll)
    client = TestClient(app)

    main_chain_id = _get_main_chain_id(client, coll)

    # Create user group
    resp_g = client.post(
        f"/api/file-mgmt/{coll}/groups",
        json={"name": "Legal"},
    )
    assert resp_g.status_code == 201
    group_id = resp_g.json()["group_id"]
    group_folder_id = resp_g.json()["folder_id"]

    # 1. Upload file to group folder
    resp1 = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("contract.pdf", _fake_pdf_bytes(), "application/pdf")},
        data={"folder_id": group_folder_id},
    )
    assert resp1.status_code == 201, resp1.text
    file_id = resp1.json()["file_id"]

    # 2. Get file detail
    resp2 = client.get(f"/api/file-mgmt/{coll}/files/{file_id}")
    assert resp2.status_code == 200
    detail = resp2.json()
    assert len(detail["paths"]) >= 1
    assert len(detail["versions"]) == 1

    # 3. Create node and attach file
    node = _create_node(client, coll, main_chain_id, "Contract Review", order=1, group_id=group_id)
    node_id = node["node_id"]
    resp3 = client.post(
        f"/api/file-mgmt/{coll}/nodes/{node_id}/files",
        json={"file_id": file_id},
    )
    assert resp3.status_code == 201

    # 4. Upload new version
    resp4 = client.post(
        f"/api/file-mgmt/{coll}/files/{file_id}/versions",
        files={"file": ("contract_v2.pdf", _fake_pdf_bytes(), "application/pdf")},
        data={"commit_message": "Updated terms"},
    )
    assert resp4.status_code == 201, resp4.text

    # 5. Get detail — should have 2 versions now
    resp5 = client.get(f"/api/file-mgmt/{coll}/files/{file_id}")
    assert resp5.status_code == 200
    detail5 = resp5.json()
    assert len(detail5["versions"]) == 2
    assert any(v["archived"] for v in detail5["versions"])
    assert any(not v["archived"] for v in detail5["versions"])

    # 6. Toggle definitive
    resp6 = client.patch(
        f"/api/file-mgmt/{coll}/files/{file_id}",
        json={"is_definitive": True, "version": detail5["version"]},
    )
    assert resp6.status_code == 200
    assert resp6.json()["is_definitive"] is True

    # 7. Delete
    resp7 = client.delete(f"/api/file-mgmt/{coll}/files/{file_id}")
    assert resp7.status_code == 204

    # 8. Verify gone
    resp8 = client.get(f"/api/file-mgmt/{coll}/files/{file_id}")
    assert resp8.status_code == 404
