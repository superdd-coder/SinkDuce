"""Root path 化 (v2 R1): Root is a real hidden system folder; zero-path is transient.

Run: pytest tests/test_file_mgmt_root_path.py -v --tb=short
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.file_mgmt import service, store


@pytest.fixture()
def collection_id(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "COLLECTIONS_DIR", tmp_path)
    cid = "col_" + uuid.uuid4().hex[:8]
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        store.init_collection_db(cid)
        yield cid
    shutil.rmtree(tmp_path / cid, ignore_errors=True)


def _root_fid(cid: str) -> str:
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT folder_id FROM folders WHERE name='Root' AND is_system=1 LIMIT 1"
    ).fetchone()
    conn.close()
    assert row, "Root system folder missing"
    return row["folder_id"]


def _unassigned_fid(cid: str) -> str:
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT folder_id FROM folders WHERE name='Unassigned' AND is_system=1 LIMIT 1"
    ).fetchone()
    conn.close()
    assert row, "Unassigned system folder missing"
    return row["folder_id"]


def _paths(cid: str, fid: str) -> list[str]:
    conn = store.get_db(cid)
    rows = conn.execute(
        "SELECT folder_id FROM file_paths WHERE file_id=?", (fid,)
    ).fetchall()
    conn.close()
    return [r["folder_id"] for r in rows]


def _upload(cid: str, filename: str = "a.txt", folder_id: str | None = None) -> str:
    out = service.upload_file_to_folder(
        cid, folder_id, b"hello root", filename
    )
    return out.file_id


def _plain(cid: str, name: str):
    from src.file_mgmt.models import FolderCreate

    return service.create_folder(cid, FolderCreate(name=name))


# ── seeding & visibility ─────────────────────────────────────────


def test_root_folder_seeded_and_in_tree_as_move_dest(collection_id):
    """Root lives in the folder tree (move-to-root destination, MCP browse).
    The UI grid filters its tile; it is never navigable as a folder view."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        root_fid = _root_fid(collection_id)
        tree = service.get_folder_tree(collection_id)
        assert any(n.folder_id == root_fid for n in tree)


# ── upload mounts Root path ──────────────────────────────────────


def test_upload_to_root_mounts_root_path(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        fid = _upload(collection_id, "a.txt", folder_id=None)
        assert _paths(collection_id, fid) == [_root_fid(collection_id)]
        # Root view (list_files with no folder) lists it
        root_files = service.list_files(collection_id)
        assert any(f.file_id == fid for f in root_files)


def test_legacy_zero_path_files_backfilled_to_root(collection_id):
    """files.json migration and legacy DBs create zero-path files — they must
    land on the Root folder, not stay invisible. Covers multiple files at once
    (each needs its own generated path_id)."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        # Mimic legacy orphans: files rows with no path
        conn = store.get_db(collection_id)
        conn.execute("PRAGMA foreign_keys=OFF")
        fids = []
        for _ in range(2):
            fid, vid = uuid.uuid4().hex, uuid.uuid4().hex
            fids.append(fid)
            conn.execute(
                "INSERT INTO files (file_id, current_version_id, is_definitive, "
                "archived, unsupported, created_by, version) VALUES (?,?,0,0,0,'local',1)",
                (fid, vid),
            )
            conn.execute(
                "INSERT INTO file_versions (version_id, file_id, version_no, "
                "storage_file_id, archived, commit_message, created_by, created_at) "
                "VALUES (?,?,1,'legacy.txt',0,NULL,'local','2026-01-01T00:00:00')",
                (vid, fid),
            )
        conn.commit()
        conn.close()

        from src.file_mgmt.store import _ensure_root_mounts

        conn = store.get_db(collection_id)
        root_fid = _ensure_root_mounts(conn)
        conn.close()
        for fid in fids:
            assert _paths(collection_id, fid) == [root_fid]


# ── delete at root falls to Unassigned ───────────────────────────


def test_root_delete_routes_to_unassigned(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        fid = _upload(collection_id, "a.txt", folder_id=None)
        root_fid = _root_fid(collection_id)
        conn = store.get_db(collection_id)
        pid = conn.execute(
            "SELECT path_id FROM file_paths WHERE file_id=? AND folder_id=?",
            (fid, root_fid),
        ).fetchone()["path_id"]
        conn.close()
        service.remove_file_path(collection_id, fid, pid)
        assert _paths(collection_id, fid) == [_unassigned_fid(collection_id)]


# ── add_file_path: Root allowed, other system folders still blocked ──


def test_add_file_path_allows_root_blocks_other_system(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        fid = _upload(collection_id, "a.txt", folder_id=a.folder_id)
        # Move semantics use add_file_path on the Root folder — must work
        out = service.add_file_path(collection_id, fid, _root_fid(collection_id))
        assert out.folder_id == _root_fid(collection_id)
        # Other system folders stay blocked
        with pytest.raises(HTTPException) as ei:
            service.add_file_path(collection_id, fid, _unassigned_fid(collection_id))
        assert ei.value.status_code == 403


# ── scope rename: orphans → root ─────────────────────────────────


def test_list_files_with_mounts_scope_root(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        fid = _upload(collection_id, "a.txt", folder_id=None)
        rows = service.list_files_with_mounts(collection_id, scope="root")
        assert any(r["file_id"] == fid for r in rows)
        with pytest.raises(HTTPException):
            service.list_files_with_mounts(collection_id, scope="orphans")
