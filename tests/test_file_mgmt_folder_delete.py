"""Folder delete (path/global scope), Unassigned system folder, ingest-task cancel.

Design: development_doc/2026-09-03-library-folder-delete-unassigned-design.md

Run: pytest tests/test_file_mgmt_folder_delete.py -v --tb=short
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import patch

import pytest

from src.file_mgmt import service, store
from src.file_mgmt.models import (
    ChainCreate,
    FolderCreate,
    GroupCreate,
    NodeCreate,
)


@pytest.fixture()
def collection_id(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "COLLECTIONS_DIR", tmp_path)
    cid = "col_" + uuid.uuid4().hex[:8]
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        store.init_collection_db(cid)
        yield cid
    shutil.rmtree(tmp_path / cid, ignore_errors=True)


def _plain(cid: str, name: str, parent: str | None = None):
    return service.create_folder(
        cid, FolderCreate(name=name, parent_folder_id=parent)
    )


def _unassigned_fid(cid: str) -> str:
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT folder_id FROM folders WHERE name='Unassigned' AND is_system=1 LIMIT 1"
    ).fetchone()
    conn.close()
    assert row, "Unassigned system folder missing"
    return row["folder_id"]


def _seed_file(cid: str, folder_id: str, extra_folder_id: str | None = None):
    conn = store.get_db(cid)
    store._ensure_file_paths_archived(conn)
    conn.execute("PRAGMA foreign_keys=OFF")
    fid, vid, pid = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    now = "2026-01-01T00:00:00"
    conn.execute(
        "INSERT INTO files (file_id, current_version_id, is_definitive, archived, "
        "unsupported, created_by, version) VALUES (?,?,0,0,0,'local',1)",
        (fid, vid),
    )
    conn.execute(
        "INSERT INTO file_versions (version_id, file_id, version_no, storage_file_id, "
        "archived, commit_message, created_by, created_at) "
        "VALUES (?,?,1,'a.txt',0,NULL,'local',?)",
        (vid, fid, now),
    )
    conn.execute(
        "INSERT INTO file_paths (path_id, file_id, folder_id, is_primary, "
        "source_node_id, created_by, archived) VALUES (?,?,?,1,NULL,'local',0)",
        (pid, fid, folder_id),
    )
    extra_pid = None
    if extra_folder_id:
        extra_pid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO file_paths (path_id, file_id, folder_id, is_primary, "
            "source_node_id, created_by, archived) VALUES (?,?,?,0,NULL,'local',0)",
            (extra_pid, fid, extra_folder_id),
        )
    conn.commit()
    conn.close()
    return fid, pid, extra_pid


def _path_folder_ids(cid: str, file_id: str) -> list[str]:
    conn = store.get_db(cid)
    rows = conn.execute(
        "SELECT folder_id FROM file_paths WHERE file_id=?", (file_id,)
    ).fetchall()
    conn.close()
    return [r["folder_id"] for r in rows]


def _file_exists(cid: str, file_id: str) -> bool:
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT 1 FROM files WHERE file_id=?", (file_id,)
    ).fetchone()
    conn.close()
    return row is not None


def _seed_zero_path_file(cid: str) -> tuple[str, None, None]:
    """A files+versions row with no mount (like a legacy orphan)."""
    conn = store.get_db(cid)
    conn.execute("PRAGMA foreign_keys=OFF")
    fid, vid = uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute(
        "INSERT INTO files (file_id, current_version_id, is_definitive, "
        "archived, unsupported, created_by, version) VALUES (?,?,0,0,0,'local',1)",
        (fid, vid),
    )
    conn.execute(
        "INSERT INTO file_versions (version_id, file_id, version_no, "
        "storage_file_id, archived, commit_message, created_by, created_at) "
        "VALUES (?,?,1,'a.txt',0,NULL,'local','2026-01-01T00:00:00')",
        (vid, fid),
    )
    conn.commit()
    conn.close()
    return fid, None, None


def _branch(cid: str):
    chains = service.list_chains(cid)
    main = next(c for c in chains if c.is_main)
    parent = service.create_node(
        cid, main.chain_id, NodeCreate(node_type="event", title="P", order=1)
    )
    return service.create_chain(
        cid,
        ChainCreate(parent_chain_id=main.chain_id, parent_node_id=parent.node_id, title="B1"),
    )


# ── Unassigned system folder ─────────────────────────────────────


def test_unassigned_folder_backfilled(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        fid = _unassigned_fid(collection_id)
        # Idempotent re-init must not duplicate it
        store.init_collection_db(collection_id)
        conn = store.get_db(collection_id)
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM folders WHERE name='Unassigned' AND is_system=1"
        ).fetchone()["c"]
        conn.close()
        assert n == 1
        assert fid


# ── path scope + Unassigned routing ──────────────────────────────


def test_path_delete_routes_pathless_file_to_unassigned(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        fid, _, _ = _seed_file(collection_id, a.folder_id)
        out = service.delete_folder(collection_id, a.folder_id)
        assert out == {"deleted_files": 0, "folders_deleted": 1}
        assert _file_exists(collection_id, fid)
        assert _path_folder_ids(collection_id, fid) == [_unassigned_fid(collection_id)]


def test_path_delete_keeps_mirrored_file_mounted_elsewhere(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        b = _plain(collection_id, "B")
        fid, _, _ = _seed_file(
            collection_id, a.folder_id, extra_folder_id=b.folder_id
        )
        service.delete_folder(collection_id, a.folder_id)
        assert _file_exists(collection_id, fid)
        assert _path_folder_ids(collection_id, fid) == [b.folder_id]


def test_remove_last_path_routes_to_unassigned(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        fid, pid, _ = _seed_file(collection_id, a.folder_id)
        service.remove_file_path(collection_id, fid, pid)
        assert _file_exists(collection_id, fid)
        assert _path_folder_ids(collection_id, fid) == [_unassigned_fid(collection_id)]


def test_move_add_then_remove_leaves_no_unassigned(collection_id):
    """Move = add target path first, then remove source path — file never hits zero."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        b = _plain(collection_id, "B")
        fid, pid_a, _ = _seed_file(collection_id, a.folder_id)
        service.add_file_path(collection_id, fid, b.folder_id)
        service.remove_file_path(collection_id, fid, pid_a)
        assert _path_folder_ids(collection_id, fid) == [b.folder_id]


# ── v2 R2/R3/DD: folder deletion is structure-only; burn is per-file ──


def test_folder_delete_never_destroys_files(collection_id):
    """v2: folder delete removes structure + mounts only. Files land in
    Unassigned; even archived files survive. scope=global is gone."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        child = _plain(collection_id, "Child", a.folder_id)
        f1, _, _ = _seed_file(collection_id, a.folder_id)
        f2, _, _ = _seed_file(collection_id, a.folder_id)
        f3, _, _ = _seed_file(collection_id, child.folder_id)
        conn = store.get_db(collection_id)
        conn.execute("UPDATE files SET archived=1 WHERE file_id=?", (f3,))
        conn.commit()
        conn.close()

        out = service.delete_folder(collection_id, a.folder_id)
        assert out == {"deleted_files": 0, "folders_deleted": 2}
        for fid in (f1, f2, f3):
            assert _file_exists(collection_id, fid)
            assert _path_folder_ids(collection_id, fid) == [
                _unassigned_fid(collection_id)
            ]


def test_folder_delete_scope_global_removed(collection_id):
    """DD: the scope=global API no longer exists."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        with pytest.raises(TypeError):
            service.delete_folder(collection_id, a.folder_id, scope="global")


# ── deletion guards (D4) ─────────────────────────────────────────


def test_delete_folder_rejects_branch_group_system(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        ch = _branch(collection_id)
        grp = service.create_group(collection_id, GroupCreate(name="G1"))
        conn = store.get_db(collection_id)
        sys_fid = conn.execute(
            "SELECT folder_id FROM folders WHERE name='Meeting' AND is_system=1"
        ).fetchone()["folder_id"]
        conn.close()
        for fid in (ch.folder_id, grp.folder_id, sys_fid):
            with pytest.raises(Exception) as ei:
                service.delete_folder(collection_id, fid)
            assert getattr(ei.value, "status_code", None) == 403, fid
        # Nothing was deleted
        assert _file_exists.__name__  # noop guard
        conn = store.get_db(collection_id)
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM folders WHERE folder_id=?",
            (ch.folder_id,),
        ).fetchone()["c"]
        conn.close()
        assert n == 1


def test_delete_folder_rejects_non_plain_subtree(collection_id):
    """SQL-planted group under a plain folder must block Library deletion."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        parent = _plain(collection_id, "P")
        grp = service.create_group(collection_id, GroupCreate(name="G1"))
        conn = store.get_db(collection_id)
        conn.execute(
            "UPDATE folders SET parent_folder_id=? WHERE folder_id=?",
            (parent.folder_id, grp.folder_id),
        )
        conn.commit()
        conn.close()
        with pytest.raises(Exception) as ei:
            service.delete_folder(collection_id, parent.folder_id)
        assert getattr(ei.value, "status_code", None) == 409


# ── timeline cascade routing (D3) ────────────────────────────────


def test_chain_delete_routes_pathless_file_to_unassigned(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        ch = _branch(collection_id)
        # Window contents are timeline-derived: attach a file to the anchor —
        # its derived path mounts the file inside the branch folder
        fid, _, _ = _seed_zero_path_file(collection_id)
        service.attach_file_to_node(collection_id, ch.parent_node_id, fid)
        assert ch.folder_id in _path_folder_ids(collection_id, fid)
        service.delete_chain(collection_id, ch.chain_id)
        assert _file_exists(collection_id, fid)
        assert _path_folder_ids(collection_id, fid) == [_unassigned_fid(collection_id)]


# ── promotion on mount loss (delete 触发的文件级晋升) ─────────────


def _seed_file_with_archived_mount(cid: str, active_folder_id: str, archived_folder_id: str):
    """File with an active path in active_folder_id and a greyed path in archived_folder_id."""
    fid, pid_active, pid_arch = _seed_file(
        cid, active_folder_id, extra_folder_id=archived_folder_id
    )
    conn = store.get_db(cid)
    conn.execute("UPDATE file_paths SET archived=1 WHERE path_id=?", (pid_arch,))
    conn.commit()
    conn.close()
    return fid, pid_active, pid_arch


def _file_archived(cid: str, file_id: str) -> int:
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT archived FROM files WHERE file_id=?", (file_id,)
    ).fetchone()
    conn.close()
    return int(row["archived"] or 0)


def test_delete_folder_promotes_archived_only_files(collection_id):
    """Deleting the last active mount promotes the file to file-level archive
    (it still has a greyed path in B — searchable would be inconsistent)."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        b = _plain(collection_id, "B")
        fid, _, _ = _seed_file_with_archived_mount(
            collection_id, a.folder_id, b.folder_id
        )
        service.delete_folder(collection_id, a.folder_id)
        assert _file_exists(collection_id, fid)
        assert _path_folder_ids(collection_id, fid) == [b.folder_id]
        assert _file_archived(collection_id, fid) == 1


def test_remove_path_promotes_when_only_archived_remains(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        b = _plain(collection_id, "B")
        fid, pid_a, _ = _seed_file_with_archived_mount(
            collection_id, a.folder_id, b.folder_id
        )
        service.remove_file_path(collection_id, fid, pid_a)
        assert _file_exists(collection_id, fid)
        assert _path_folder_ids(collection_id, fid) == [b.folder_id]
        assert _file_archived(collection_id, fid) == 1


# ── anchor node deletion (cascade + simplified contract) ─────────


def test_delete_anchor_node_cascades_branch_and_routes_files(collection_id):
    """Deleting an anchor node takes the branch + its folder with it; the
    node's own pathless attachment survives unmounted → Unassigned."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        chains = service.list_chains(collection_id)
        main = next(c for c in chains if c.is_main)
        p = service.create_node(
            collection_id,
            main.chain_id,
            NodeCreate(node_type="event", title="P", order=1),
        )
        ch = service.create_chain(
            collection_id,
            ChainCreate(
                parent_chain_id=main.chain_id, parent_node_id=p.node_id, title="B1"
            ),
        )
        fid, _, _ = _seed_file(collection_id, ch.folder_id)
        service.attach_file_to_node(collection_id, p.node_id, fid)

        out = service.delete_node(collection_id, p.node_id)
        # Simplified contract: plain success, no affected_files payload
        assert out is None

        # Branch chain + folder are gone
        assert all(c.chain_id != ch.chain_id for c in service.list_chains(collection_id))
        conn = store.get_db(collection_id)
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM folders WHERE folder_id=?", (ch.folder_id,)
        ).fetchone()["c"]
        conn.close()
        assert n == 0

        # The attachment survives, routed to Unassigned (not Root orphan)
        assert _file_exists(collection_id, fid)
        assert _path_folder_ids(collection_id, fid) == [_unassigned_fid(collection_id)]


def test_delete_node_never_returns_affected_files(collection_id):
    """Simplified contract: node delete never asks the UI about files —
    sole-attachment files survive unmounted and land in Unassigned."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        chains = service.list_chains(collection_id)
        main = next(c for c in chains if c.is_main)
        n1 = service.create_node(
            collection_id,
            main.chain_id,
            NodeCreate(node_type="event", title="N1", order=1),
        )
        fid, _, _ = _seed_file(collection_id, _unassigned_fid(collection_id))
        # Give it a derived path on n1 (as if uploaded via the node), then
        # remove the Unassigned row so the node's path is its ONLY one.
        service.attach_file_to_node(collection_id, n1.node_id, fid)
        conn = store.get_db(collection_id)
        conn.execute(
            "DELETE FROM file_paths WHERE file_id=? AND folder_id=?",
            (fid, _unassigned_fid(collection_id)),
        )
        conn.commit()
        conn.close()

        out = service.delete_node(collection_id, n1.node_id)
        assert out is None
        assert _file_exists(collection_id, fid)
        assert _path_folder_ids(collection_id, fid) == [_unassigned_fid(collection_id)]


# ── ingest-task cancellation (D6) ────────────────────────────────


def test_cancel_ingest_tasks_scans_and_cancels(collection_id):
    from src.tasks.task_manager import Task, TaskStatus, task_manager

    planted = []
    try:
        for i, (ttype, kwargs) in enumerate(
            [
                ("upload", {"collection": collection_id, "file_id": "f1"}),
                ("doc_summary", {"collection": collection_id, "source": "__file__:f1"}),
                ("upload", {"collection": "other", "file_id": "f1"}),  # other col
                ("upload", {"collection": collection_id, "file_id": "f2"}),  # other file
            ]
        ):
            tid = f"t{i}"
            task_manager.tasks[tid] = Task(id=tid, filename=tid, collection=kwargs["collection"])
            task_manager._task_args[tid] = (ttype, kwargs)
            planted.append(tid)
        n = task_manager.cancel_ingest_tasks_for_files(collection_id, ["f1"])
        assert n == 2
        assert task_manager.tasks["t0"].status == TaskStatus.FAILED
        assert task_manager.tasks["t1"].status == TaskStatus.FAILED
        assert task_manager.tasks["t2"].status == TaskStatus.PENDING
        assert task_manager.tasks["t3"].status == TaskStatus.PENDING
    finally:
        for tid in planted:
            task_manager.tasks.pop(tid, None)
            task_manager._task_args.pop(tid, None)


def test_delete_file_cancels_active_ingest_tasks(collection_id, monkeypatch):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        fid, pid, _ = _seed_file(collection_id, a.folder_id)
        calls: list[tuple[str, list[str]]] = []

        from src.tasks.task_manager import task_manager as _tm

        monkeypatch.setattr(
            _tm,
            "cancel_ingest_tasks_for_files",
            lambda col, fids, **kw: calls.append((col, list(fids))) or 0,
        )
        service.delete_file(collection_id, fid)
        assert calls == [(collection_id, [fid])]


# ── upload handler store guard (D6) ──────────────────────────────


def test_pre_store_guard_aborts_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "COLLECTIONS_DIR", tmp_path)
    cid = "col_" + uuid.uuid4().hex[:8]
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        store.init_collection_db(cid)
        try:
            from src.tasks.handlers import _file_row_exists

            # No row → missing
            assert _file_row_exists(cid, "nofile") is False
            # Seed a row → present
            _seed_file(cid, _unassigned_fid(cid))
            fid = _path_folder_ids.__name__  # noop guard
            conn = store.get_db(cid)
            row = conn.execute("SELECT file_id FROM files LIMIT 1").fetchone()
            conn.close()
            assert _file_row_exists(cid, row["file_id"]) is True
        finally:
            shutil.rmtree(tmp_path / cid, ignore_errors=True)
