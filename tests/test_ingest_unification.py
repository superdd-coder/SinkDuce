"""One ingest path + one definitive flag (cleanup module 5)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_file_mgmt_does_not_ship_a_second_ingest_pipeline():
    from pathlib import Path

    import src.file_mgmt.service as svc

    assert not hasattr(svc, "_ingest_file_to_qdrant")
    src = Path("src/file_mgmt/files.py").read_text()
    assert "upload_handler" in src or "create_task" in src


def test_set_document_definitive_file_source_uses_sqlite_flag():
    from src.mcp.tools.documents import set_document_definitive
    import asyncio

    row = {"version": 4, "is_definitive": 0}
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row
    updated = MagicMock()

    with patch("src.mcp.tools.documents.require_collection", return_value=None), \
         patch("src.file_mgmt.store.get_db", return_value=conn), \
         patch("src.file_mgmt.service.update_file", return_value=updated) as uf, \
         patch("src.api.routes.info._snapshot_includes", return_value={}), \
         patch("src.api.routes.info.schedule_debounced_consolidate"):
        raw = asyncio.run(set_document_definitive("col_1", "__file__:abc123", True))
    import json
    out = json.loads(raw) if isinstance(raw, str) else raw
    uf.assert_called_once_with(
        "col_1", "abc123", {"is_definitive": True, "version": 4}
    )
    assert out.get("definitive") is True


def test_source_is_definitive_accepts_file_alias(tmp_path, monkeypatch):
    from src.file_mgmt.store import get_db, init_collection_db
    from src.api.routes.info import source_is_definitive

    cid = "def-alias-col"
    coll_dir = tmp_path / "collections"
    coll_dir.mkdir()
    monkeypatch.setattr("src.file_mgmt.store.COLLECTIONS_DIR", coll_dir)
    init_collection_db(cid)
    fid = "aliasfile001" + "a" * 20
    conn = get_db(cid)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """INSERT INTO files
               (file_id, current_version_id, is_definitive, archived, unsupported,
                created_by, version)
               VALUES (?, NULL, 1, 0, 0, 'local', 1)""",
            (fid,),
        )
        conn.commit()
    finally:
        conn.close()

    assert source_is_definitive(cid, f"__file__:{fid}") is True
    assert source_is_definitive(cid, f"file:{fid}") is True
