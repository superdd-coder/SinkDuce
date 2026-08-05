"""Definitive is the sole switch for Collection Summary participation.

- Mark definitive + no summary → queue doc_summary
- Mark definitive + has summary → schedule debounce consolidate
- Clear definitive → keep summary, schedule consolidate
- consolidate filters by files.is_definitive
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db, get_db


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


def _setup(coll_id: str) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll_id, f"Test {coll_id}")
    init_collection_db(coll_id)


def _insert_file(coll_id: str, file_id: str, is_definitive: int = 0) -> None:
    now = "2026-08-01T00:00:00+00:00"
    vid = uuid.uuid4().hex
    conn = get_db(coll_id)
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO file_versions
               (version_id, file_id, version_no, storage_file_id,
                archived, commit_message, created_by, created_at)
               VALUES (?, ?, 1, ?, 0, 'initial', 'local', ?)""",
            (vid, file_id, f"doc_{file_id[:6]}.pdf", now),
        )
        conn.execute(
            """INSERT INTO files
               (file_id, current_version_id, is_definitive, archived,
                unsupported, created_by, version)
               VALUES (?, ?, ?, 0, 0, 'local', 1)""",
            (file_id, vid, is_definitive),
        )
        conn.commit()
    finally:
        conn.close()


def test_source_is_definitive_reads_file_flag():
    from src.api.routes.info import source_is_definitive

    coll = "def-src"
    _setup(coll)
    fid = uuid.uuid4().hex
    _insert_file(coll, fid, is_definitive=0)
    assert source_is_definitive(coll, f"__file__:{fid}") is False

    conn = get_db(coll)
    try:
        conn.execute(
            "UPDATE files SET is_definitive=1 WHERE file_id=?", (fid,)
        )
        conn.commit()
    finally:
        conn.close()
    assert source_is_definitive(coll, f"__file__:{fid}") is True


def test_mark_definitive_queues_summary_when_missing():
    from src.main import app

    coll = "def-gen"
    _setup(coll)
    client = TestClient(app)
    fid = uuid.uuid4().hex
    _insert_file(coll, fid, is_definitive=0)

    sm = MagicMock()
    sm.get_doc_summary.return_value = None
    mock_create = MagicMock()

    with (
        patch("src.api.routes.info._snapshot_includes", return_value={}),
        patch("src.api.routes.info._get_summary_manager", return_value=sm),
        patch(
            "src.api.routes.info.schedule_debounced_consolidate"
        ) as mock_sched,
        patch("src.tasks.task_manager.task_manager.create_task", mock_create),
    ):
        resp = client.patch(
            f"/api/file-mgmt/{coll}/files/{fid}",
            json={"is_definitive": True, "version": 1},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_definitive"] is True

        assert mock_create.called
        call_kw = mock_create.call_args.kwargs
        assert call_kw.get("task_type") == "doc_summary"
        assert call_kw.get("source") == f"__file__:{fid}"
        mock_sched.assert_called()


def test_clear_definitive_keeps_summary_and_schedules():
    from src.main import app

    coll = "def-clear"
    _setup(coll)
    client = TestClient(app)
    fid = uuid.uuid4().hex
    _insert_file(coll, fid, is_definitive=1)
    source = f"__file__:{fid}"

    sm = MagicMock()
    sm.get_doc_summary.return_value = {
        "source": source,
        "data": ["x"],
        "facts": [],
        "insights": [],
        "include_in_summary": True,
    }

    with (
        patch("src.api.routes.info._snapshot_includes", return_value={source: True}),
        patch("src.api.routes.info._get_summary_manager", return_value=sm),
        patch(
            "src.api.routes.info.schedule_debounced_consolidate"
        ) as mock_sched,
        patch("src.tasks.task_manager.task_manager.create_task") as mock_create,
    ):
        resp = client.patch(
            f"/api/file-mgmt/{coll}/files/{fid}",
            json={"is_definitive": False, "version": 1},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_definitive"] is False
        # Summary not deleted — only include flag flipped
        sm.set_doc_summary_include.assert_called()
        mock_sched.assert_called()
        # Should not queue a new summary generation
        for c in mock_create.call_args_list:
            assert c.kwargs.get("task_type") != "doc_summary"
