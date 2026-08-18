"""Step 2: read index is SQLite-first with JSON fallback."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def isolated_col(tmp_path, monkeypatch):
    cid = "read-idx-col"
    coll_dir = tmp_path / "collections"
    coll_dir.mkdir(parents=True)
    monkeypatch.setattr("src.collections.file_index.COLLECTIONS_DIR", coll_dir)
    monkeypatch.setattr("src.file_mgmt.store.COLLECTIONS_DIR", coll_dir)
    from src.file_mgmt.store import init_collection_db

    init_collection_db(cid)
    return cid, coll_dir


def _insert_file(cid: str, file_id: str, storage_name: str) -> None:
    from src.file_mgmt.store import get_db

    conn = get_db(cid)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        vid = "v" + file_id[:8]
        conn.execute(
            """INSERT INTO files
               (file_id, current_version_id, is_definitive, archived, unsupported,
                created_by, version)
               VALUES (?, ?, 0, 0, 0, 'local', 1)""",
            (file_id, vid),
        )
        conn.execute(
            """INSERT INTO file_versions
               (version_id, file_id, version_no, storage_file_id, archived,
                commit_message, created_by, created_at)
               VALUES (?, ?, 1, ?, 0, 'init', 'local', '2026-08-13T00:00:00Z')""",
            (vid, file_id, storage_name),
        )
        conn.commit()
    finally:
        conn.close()


def test_migrate_files_json_import_handles_extensionless_label(isolated_col):
    """Legacy files.json may have source_label without a suffix + original_ext."""
    import json

    from src.file_mgmt.store import _migrate_files_json_import, get_db

    cid, coll_dir = isolated_col
    (coll_dir / cid).mkdir(parents=True, exist_ok=True)
    (coll_dir / cid / "files.json").write_text(
        json.dumps(
            {
                "file_legacy": {
                    "source": "__file__:old.pdf",
                    "source_label": "old-scan",
                    "original_ext": "pdf",
                }
            }
        ),
        encoding="utf-8",
    )
    _migrate_files_json_import(cid)
    conn = get_db(cid)
    try:
        row = conn.execute(
            "SELECT file_id FROM files WHERE file_id=?",
            ("file_legacy",),
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_load_for_read_sqlite_wins_over_stale_json(isolated_col):
    from src.collections.file_index import add, load_for_read

    cid, _ = isolated_col
    fid = "filebbbb222"
    _insert_file(cid, fid, "current.pdf")
    add(cid, fid, f"__file__:{fid}", "stale.pdf", "file", 2, "pdf")

    idx = load_for_read(cid)
    assert idx[fid]["source_label"] == "current.pdf"
    assert idx[fid]["source"] == f"__file__:{fid}"


def test_load_for_read_includes_sqlite_file_missing_from_json(isolated_col):
    from src.collections.file_index import load, load_for_read

    cid, _ = isolated_col
    fid = "filecccc333"
    _insert_file(cid, fid, "only-sqlite.md")

    assert fid not in load(cid)
    idx = load_for_read(cid)
    assert fid in idx
    assert idx[fid]["source_label"] == "only-sqlite.md"
    assert idx[fid]["source"] == f"__file__:{fid}"
    assert idx[fid]["original_ext"] == "md"


def test_load_for_read_keeps_note_source_from_json(isolated_col):
    from src.collections.file_index import add, load_for_read

    cid, _ = isolated_col
    fid = "filenote444"
    _insert_file(cid, fid, "note-blob.md")
    add(cid, fid, "__note__:note-abc", "Note: My title", "note", 4, "md")

    idx = load_for_read(cid)
    assert idx[fid]["source"] == "__note__:note-abc"
    assert idx[fid]["source_label"] == "Note: My title"
    assert idx[fid]["file_type"] == "note"


def test_load_for_read_uses_sqlite_meeting_label_without_json(isolated_col):
    """After files.json stop-write, meeting ingest stores source + label on files."""
    from src.collections.file_index import load, load_for_read
    from src.file_mgmt.store import get_db, init_collection_db

    cid, _ = isolated_col
    init_collection_db(cid)
    fid = "filemeet555"
    _insert_file(cid, fid, "tab_01.md")
    conn = get_db(cid)
    try:
        conn.execute(
            "UPDATE files SET source=?, source_label=? WHERE file_id=?",
            (f"__meeting__:abc:{fid}", "Standup / Action items", fid),
        )
        conn.commit()
    finally:
        conn.close()

    assert fid not in load(cid)
    idx = load_for_read(cid)
    assert idx[fid]["source"].startswith("__meeting__:")
    assert idx[fid]["source_label"] == "Standup / Action items"
    assert idx[fid]["file_type"] == "meeting"


def test_plain_load_stays_json_only(isolated_col):
    """Mutators still see JSON only — do not persist SQLite overlay."""
    from src.collections.file_index import load

    cid, _ = isolated_col
    _insert_file(cid, "filedddd555", "sqlite-only.txt")
    assert load(cid) == {}
