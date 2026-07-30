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
