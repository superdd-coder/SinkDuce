"""Path-level archive + end_chain inherit_file_ids (no FastAPI app / MCP)."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from src.file_mgmt import service, store
from src.file_mgmt.models import (
    ChainCreate,
    EndChainRequest,
    GroupCreate,
    NodeCreate,
)


@pytest.fixture()
def collection_id(tmp_path, monkeypatch):
    """Isolated collection under a temp COLLECTIONS_DIR."""
    monkeypatch.setattr(store, "COLLECTIONS_DIR", tmp_path)
    cid = "col_" + uuid.uuid4().hex[:8]
    with patch.object(service, "_validate_collection", lambda _x: None):
        store.init_collection_db(cid)
        yield cid
    shutil.rmtree(tmp_path / cid, ignore_errors=True)


def _seed_branch_with_file(cid: str):
    g = service.create_group(cid, GroupCreate(name="G1"))
    chains = service.list_chains(cid)
    main = next(c for c in chains if c.is_main)
    parent = service.create_node(
        cid,
        main.chain_id,
        NodeCreate(group_id=g.group_id, node_type="event", title="P", order=1),
    )
    ch = service.create_chain(
        cid,
        ChainCreate(
            parent_chain_id=main.chain_id,
            parent_node_id=parent.node_id,
            title="B1",
        ),
    )
    bn = service.create_node(
        cid,
        ch.chain_id,
        NodeCreate(group_id=g.group_id, node_type="event", title="BE", order=2),
    )
    conn = store.get_db(cid)
    store._ensure_file_paths_archived(conn)
    conn.execute("PRAGMA foreign_keys=OFF")
    fid, vid = uuid.uuid4().hex, uuid.uuid4().hex
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
        "INSERT INTO file_nodes (file_id, node_id, version_id, greyed, added_by) "
        "VALUES (?,?,?,0,'local')",
        (fid, bn.node_id, vid),
    )
    pid_group, pid_branch = uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute(
        "INSERT INTO file_paths (path_id, file_id, folder_id, is_primary, "
        "source_node_id, created_by, archived) VALUES (?,?,?,1,?,?,0)",
        (pid_group, fid, g.folder_id, bn.node_id, "local"),
    )
    conn.execute(
        "INSERT INTO file_paths (path_id, file_id, folder_id, is_primary, "
        "source_node_id, created_by, archived) VALUES (?,?,?,0,?,?,0)",
        (pid_branch, fid, ch.folder_id, bn.node_id, "local"),
    )
    end_id = uuid.uuid4().hex
    conn.execute(
        'INSERT INTO nodes (node_id, chain_id, group_id, node_type, title, "order", '
        "event_time, created_by, created_at, version) "
        "VALUES (?,?,?,'end',NULL,99,NULL,'local',?,1)",
        (end_id, ch.chain_id, g.group_id, now),
    )
    conn.commit()
    conn.close()
    return g, ch, fid, pid_group, pid_branch, end_id


def test_end_chain_path_archives_branch_only(collection_id):
    with patch.object(service, "_validate_collection", lambda _x: None):
        g, ch, fid, pid_group, pid_branch, end_id = _seed_branch_with_file(
            collection_id
        )
        r = service.end_chain(
            collection_id,
            end_id,
            EndChainRequest(
                inherit_file_ids=[],
                title="Merge",
                group_id=g.group_id,
            ),
        )
        assert r.get("merged_node_id")
        conn = store.get_db(collection_id)
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_branch,)
            ).fetchone()["archived"]
            == 1
        )
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_group,)
            ).fetchone()["archived"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT archived FROM files WHERE file_id=?", (fid,)
            ).fetchone()["archived"]
            == 0
        )
        conn.close()


def test_end_chain_inherit_keeps_branch_path(collection_id):
    with patch.object(service, "_validate_collection", lambda _x: None):
        g, ch, fid, pid_group, pid_branch, end_id = _seed_branch_with_file(
            collection_id
        )
        service.end_chain(
            collection_id,
            end_id,
            EndChainRequest(
                inherit_file_ids=[fid],
                title="Merge",
                group_id=g.group_id,
            ),
        )
        conn = store.get_db(collection_id)
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_branch,)
            ).fetchone()["archived"]
            == 0
        )
        conn.close()


def test_file_paths_archived_column_migrates(collection_id):
    with patch.object(service, "_validate_collection", lambda _x: None):
        conn = store.get_db(collection_id)
        store._ensure_file_paths_archived(conn)
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(file_paths)").fetchall()}
        assert "archived" in cols
        conn.close()


def test_folder_unarchive_clears_path_archive(collection_id):
    """Folder-view unarchive with folder_id clears path-level archive greys."""
    from src.file_mgmt.models import ArchiveToggle

    with patch.object(service, "_validate_collection", lambda _x: None):
        g, ch, fid, pid_group, pid_branch, end_id = _seed_branch_with_file(
            collection_id
        )
        service.end_chain(
            collection_id,
            end_id,
            EndChainRequest(
                inherit_file_ids=[],
                title="Merge",
                group_id=g.group_id,
            ),
        )
        listed = service.list_files_in_folder(collection_id, ch.folder_id)
        grey = next(f for f in listed if f.file_id == fid)
        assert grey.is_greyed is True
        assert grey.archived is False

        out = service.toggle_archive(
            collection_id,
            fid,
            ArchiveToggle(
                archived=False,
                version=grey.version,
                folder_id=ch.folder_id,
            ),
        )
        assert out.archived is False

        conn = store.get_db(collection_id)
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_branch,)
            ).fetchone()["archived"]
            == 0
        )
        # Group path was never path-archived
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_group,)
            ).fetchone()["archived"]
            == 0
        )
        conn.close()

        listed2 = service.list_files_in_folder(collection_id, ch.folder_id)
        again = next(f for f in listed2 if f.file_id == fid)
        assert again.is_greyed is False


def test_path_and_file_scope_and_unarchive_recovers_all(collection_id):
    """path scope archives folder; file scope greys everywhere; unarchive recovers."""
    from src.file_mgmt.models import ArchiveToggle

    with patch.object(service, "_validate_collection", lambda _x: None):
        g, ch, fid, pid_group, pid_branch, end_id = _seed_branch_with_file(
            collection_id
        )
        # Path archive branch folder only
        r1 = service.toggle_archive(
            collection_id,
            fid,
            ArchiveToggle(
                archived=True,
                version=1,
                folder_id=ch.folder_id,
                scope="path",
            ),
        )
        assert r1.archived is False  # group path still active → no promote
        conn = store.get_db(collection_id)
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_branch,)
            ).fetchone()["archived"]
            == 1
        )
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_group,)
            ).fetchone()["archived"]
            == 0
        )
        ver = conn.execute(
            "SELECT version FROM files WHERE file_id=?", (fid,)
        ).fetchone()["version"]
        conn.close()

        # File-level exclude
        r2 = service.toggle_archive(
            collection_id,
            fid,
            ArchiveToggle(archived=True, version=ver, scope="file"),
        )
        assert r2.archived is True
        # Both folders list as greyed
        in_branch = service.list_files_in_folder(collection_id, ch.folder_id)
        in_group = service.list_files_in_folder(collection_id, g.folder_id)
        assert next(f for f in in_branch if f.file_id == fid).is_greyed
        assert next(f for f in in_group if f.file_id == fid).is_greyed

        ver2 = next(f for f in in_group if f.file_id == fid).version
        # Unarchive from group: clears file-level; group path never path-archived
        service.toggle_archive(
            collection_id,
            fid,
            ArchiveToggle(
                archived=False,
                version=ver2,
                folder_id=g.folder_id,
            ),
        )
        in_group2 = service.list_files_in_folder(collection_id, g.folder_id)
        gfile = next(f for f in in_group2 if f.file_id == fid)
        assert gfile.archived is False
        assert gfile.is_greyed is False
        # Branch path still path-archived until unarchive there
        in_branch2 = service.list_files_in_folder(collection_id, ch.folder_id)
        bfile = next(f for f in in_branch2 if f.file_id == fid)
        assert bfile.is_greyed is True

        # Unarchive without folder_id = file-level only; path archives stay
        ver3 = bfile.version
        # re-exclude so we can unarchive file-level
        service.toggle_archive(
            collection_id,
            fid,
            ArchiveToggle(archived=True, version=ver3, scope="file"),
        )
        ver4 = service.list_files_in_folder(collection_id, g.folder_id)
        ver4n = next(f for f in ver4 if f.file_id == fid).version
        service.toggle_archive(
            collection_id,
            fid,
            ArchiveToggle(archived=False, version=ver4n, folder_id=None),
        )
        # Branch path still path-archived
        in_branch3 = service.list_files_in_folder(collection_id, ch.folder_id)
        assert next(f for f in in_branch3 if f.file_id == fid).is_greyed is True
        # Group path was never path-archived → not grey
        in_group3 = service.list_files_in_folder(collection_id, g.folder_id)
        assert next(f for f in in_group3 if f.file_id == fid).is_greyed is False


def test_reopen_then_remerge_succeeds(collection_id):
    """After reopen, creating a new end marker and end_chain again must work.

    Regression: leftover/duplicate branch end nodes caused
    'This chain already has an end node. Use reopen_chain first'.
    """
    with patch.object(service, "_validate_collection", lambda _x: None):
        g, ch, fid, _pid_g, _pid_b, end_id = _seed_branch_with_file(collection_id)
        r1 = service.end_chain(
            collection_id,
            end_id,
            EndChainRequest(
                inherit_file_ids=[],
                title="Merge 1",
                group_id=g.group_id,
            ),
        )
        assert r1.get("merged_node_id")
        # Branch end placeholder must be gone after merge
        nodes_closed = service.list_nodes(collection_id, ch.chain_id)
        assert all(n.node_type != "end" for n in nodes_closed)

        reopened = service.reopen_chain(collection_id, ch.chain_id)
        assert reopened.merge_node_id is None
        assert reopened.has_end_node is False

        # Simulate UI: prepare a fresh end marker (and a stale extra end)
        end2 = service.create_node(
            collection_id,
            ch.chain_id,
            NodeCreate(
                group_id=g.group_id,
                node_type="end",
                title=None,
                order=50,
            ),
        )
        end_stale = service.create_node(
            collection_id,
            ch.chain_id,
            NodeCreate(
                group_id=g.group_id,
                node_type="end",
                title=None,
                order=51,
            ),
        )
        # Re-merge using end2; backend must purge the extra end and succeed
        r2 = service.end_chain(
            collection_id,
            end2.node_id,
            EndChainRequest(
                inherit_file_ids=[],
                title="Merge 2",
                group_id=g.group_id,
            ),
        )
        assert r2.get("merged_node_id")
        assert r2["merged_node_id"] != r1["merged_node_id"]
        chains = service.list_chains(collection_id)
        branch = next(c for c in chains if c.chain_id == ch.chain_id)
        assert branch.merge_node_id == r2["merged_node_id"]
        assert branch.has_end_node is True
        # Both end markers removed from branch
        nodes_after = service.list_nodes(collection_id, ch.chain_id)
        assert all(n.node_type != "end" for n in nodes_after)
        assert all(
            n.node_id not in (end2.node_id, end_stale.node_id) for n in nodes_after
        )


def test_reopen_restores_merge_path_archive_only(collection_id):
    """Reopen undoes end_chain path archives; leaves manual file archive alone."""
    with patch.object(service, "_validate_collection", lambda _x: None):
        g, ch, fid, pid_group, pid_branch, end_id = _seed_branch_with_file(
            collection_id
        )
        # Second file: only on branch path, will be file-promoted on merge
        conn = store.get_db(collection_id)
        store._ensure_file_paths_archived(conn)
        conn.execute("PRAGMA foreign_keys=OFF")
        fid2, vid2, pid2 = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        now = "2026-01-01T00:00:00"
        bn = conn.execute(
            "SELECT node_id FROM nodes WHERE chain_id=? AND node_type='event' LIMIT 1",
            (ch.chain_id,),
        ).fetchone()["node_id"]
        conn.execute(
            "INSERT INTO files (file_id, current_version_id, is_definitive, archived, "
            "unsupported, created_by, version) VALUES (?,?,0,0,0,'local',1)",
            (fid2, vid2),
        )
        conn.execute(
            "INSERT INTO file_versions (version_id, file_id, version_no, storage_file_id, "
            "archived, commit_message, created_by, created_at) "
            "VALUES (?,?,1,'b.txt',0,NULL,'local',?)",
            (vid2, fid2, now),
        )
        conn.execute(
            "INSERT INTO file_nodes (file_id, node_id, version_id, greyed, added_by) "
            "VALUES (?,?,?,0,'local')",
            (fid2, bn, vid2),
        )
        conn.execute(
            "INSERT INTO file_paths (path_id, file_id, folder_id, is_primary, "
            "source_node_id, created_by, archived) VALUES (?,?,?,1,?,?,0)",
            (pid2, fid2, ch.folder_id, bn, "local"),
        )
        # Manual file-level archive (must NOT be restored on reopen)
        fid_manual, vid_m = uuid.uuid4().hex, uuid.uuid4().hex
        conn.execute(
            "INSERT INTO files (file_id, current_version_id, is_definitive, archived, "
            "unsupported, created_by, version) VALUES (?,?,0,1,0,'local',1)",
            (fid_manual, vid_m),
        )
        conn.execute(
            "INSERT INTO file_versions (version_id, file_id, version_no, storage_file_id, "
            "archived, commit_message, created_by, created_at) "
            "VALUES (?,?,1,'manual.txt',0,NULL,'local',?)",
            (vid_m, fid_manual, now),
        )
        conn.commit()
        conn.close()

        service.end_chain(
            collection_id,
            end_id,
            EndChainRequest(
                inherit_file_ids=[],
                title="Merge",
                group_id=g.group_id,
            ),
        )

        conn = store.get_db(collection_id)
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_branch,)
            ).fetchone()["archived"]
            == 1
        )
        # fid has group path still active → not file-archived
        assert (
            conn.execute(
                "SELECT archived FROM files WHERE file_id=?", (fid,)
            ).fetchone()["archived"]
            == 0
        )
        # fid2 only branch path → promoted to file archive by merge
        assert (
            conn.execute(
                "SELECT archived FROM files WHERE file_id=?", (fid2,)
            ).fetchone()["archived"]
            == 1
        )
        assert (
            conn.execute(
                "SELECT archived FROM files WHERE file_id=?", (fid_manual,)
            ).fetchone()["archived"]
            == 1
        )
        conn.close()

        service.reopen_chain(collection_id, ch.chain_id)

        conn = store.get_db(collection_id)
        # Merge path archive restored
        assert (
            conn.execute(
                "SELECT archived FROM file_paths WHERE path_id=?", (pid_branch,)
            ).fetchone()["archived"]
            == 0
        )
        # Merge-promoted file archive restored
        assert (
            conn.execute(
                "SELECT archived FROM files WHERE file_id=?", (fid2,)
            ).fetchone()["archived"]
            == 0
        )
        # Manual file archive untouched
        assert (
            conn.execute(
                "SELECT archived FROM files WHERE file_id=?", (fid_manual,)
            ).fetchone()["archived"]
            == 1
        )
        # Snapshot cleared
        raw = conn.execute(
            "SELECT merge_archive_json FROM chains WHERE chain_id=?",
            (ch.chain_id,),
        ).fetchone()["merge_archive_json"]
        assert raw is None
        conn.close()
