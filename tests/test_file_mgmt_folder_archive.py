"""Folder archive, move-source guards, and icon rules.

Run: pytest tests/test_file_mgmt_folder_archive.py -v --tb=short
"""

from __future__ import annotations

import json
import shutil
import uuid
from unittest.mock import patch

import pytest

from src.file_mgmt import service, store
from src.file_mgmt.models import (
    ChainCreate,
    FolderArchiveToggle,
    FolderCreate,
    FolderUpdate,
    GroupCreate,
    GroupUpdate,
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


def _file_row(cid: str, file_id: str):
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT archived FROM files WHERE file_id=?", (file_id,)
    ).fetchone()
    conn.close()
    return row


def _path_archived(cid: str, path_id: str) -> int:
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT archived FROM file_paths WHERE path_id=?", (path_id,)
    ).fetchone()
    conn.close()
    return int(row["archived"] or 0)


def test_archive_empty_plain_folder(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        f = _plain(collection_id, "Empty")
        out = service.toggle_folder_archive(
            collection_id,
            f.folder_id,
            FolderArchiveToggle(archived=True, version=f.version),
        )
        assert out.archived is True
        got = service.get_folder(collection_id, f.folder_id)
        assert got.archived is True


def test_archive_promotes_last_path_only(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        b = _plain(collection_id, "B")
        only_here, pid_only, _ = _seed_file(collection_id, a.folder_id)
        mirrored, pid_a, pid_b = _seed_file(
            collection_id, a.folder_id, extra_folder_id=b.folder_id
        )
        service.toggle_folder_archive(
            collection_id,
            a.folder_id,
            FolderArchiveToggle(archived=True, version=a.version),
        )
        assert _path_archived(collection_id, pid_only) == 1
        assert _path_archived(collection_id, pid_a) == 1
        assert _path_archived(collection_id, pid_b) == 0
        assert int(_file_row(collection_id, only_here)["archived"] or 0) == 1
        assert int(_file_row(collection_id, mirrored)["archived"] or 0) == 0


def test_archive_nested_plain_skips_group(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        parent = _plain(collection_id, "Parent")
        child = _plain(collection_id, "Child", parent.folder_id)
        grp = service.create_group(collection_id, GroupCreate(name="G1"))
        # Move group folder under parent via SQL (UI cannot, but cascade must skip)
        conn = store.get_db(collection_id)
        conn.execute(
            "UPDATE folders SET parent_folder_id=? WHERE folder_id=?",
            (parent.folder_id, grp.folder_id),
        )
        conn.commit()
        conn.close()
        fid, pid, _ = _seed_file(collection_id, child.folder_id)
        out = service.toggle_folder_archive(
            collection_id,
            parent.folder_id,
            FolderArchiveToggle(archived=True, version=parent.version),
        )
        assert out.archived is True
        assert service.get_folder(collection_id, child.folder_id).archived is True
        assert service.get_folder(collection_id, grp.folder_id).archived is False
        assert _path_archived(collection_id, pid) == 1
        assert int(_file_row(collection_id, fid)["archived"] or 0) == 1


def test_unarchive_restores_snapshot_keeps_independent_archives(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        b = _plain(collection_id, "B")
        promoted, pid_promoted, _ = _seed_file(collection_id, a.folder_id)
        independent, pid_ind, _ = _seed_file(collection_id, a.folder_id)
        mirrored, pid_a, pid_b = _seed_file(
            collection_id, a.folder_id, extra_folder_id=b.folder_id
        )
        # Independently file-archive one file before folder archive
        conn = store.get_db(collection_id)
        conn.execute(
            "UPDATE files SET archived=1 WHERE file_id=?", (independent,)
        )
        # Independently path-archive the mirrored mount in A
        conn.execute(
            "UPDATE file_paths SET archived=1 WHERE path_id=?", (pid_a,)
        )
        conn.commit()
        conn.close()

        archived = service.toggle_folder_archive(
            collection_id,
            a.folder_id,
            FolderArchiveToggle(archived=True, version=a.version),
        )
        restored = service.toggle_folder_archive(
            collection_id,
            a.folder_id,
            FolderArchiveToggle(archived=False, version=archived.version),
        )
        assert restored.archived is False
        assert _path_archived(collection_id, pid_promoted) == 0
        assert int(_file_row(collection_id, promoted)["archived"] or 0) == 0
        # Independent file-level archive kept
        assert int(_file_row(collection_id, independent)["archived"] or 0) == 1
        # Independent path archive kept
        assert _path_archived(collection_id, pid_a) == 1
        assert _path_archived(collection_id, pid_b) == 0
        assert int(_file_row(collection_id, mirrored)["archived"] or 0) == 0


def test_restore_folder_clears_auto_global_after_all_paths_archived(
    collection_id,
):
    """Archive A then B (B promotes). Restore A must revive path A and clear file archive."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        b = _plain(collection_id, "B")
        fid, pid_a, pid_b = _seed_file(
            collection_id, a.folder_id, extra_folder_id=b.folder_id
        )
        a_arch = service.toggle_folder_archive(
            collection_id,
            a.folder_id,
            FolderArchiveToggle(archived=True, version=a.version),
        )
        assert _path_archived(collection_id, pid_a) == 1
        assert int(_file_row(collection_id, fid)["archived"] or 0) == 0
        service.toggle_folder_archive(
            collection_id,
            b.folder_id,
            FolderArchiveToggle(archived=True, version=b.version),
        )
        assert _path_archived(collection_id, pid_b) == 1
        assert int(_file_row(collection_id, fid)["archived"] or 0) == 1

        service.toggle_folder_archive(
            collection_id,
            a.folder_id,
            FolderArchiveToggle(archived=False, version=a_arch.version),
        )
        assert _path_archived(collection_id, pid_a) == 0
        assert _path_archived(collection_id, pid_b) == 1
        assert int(_file_row(collection_id, fid)["archived"] or 0) == 0


def test_parent_archive_skips_already_archived_child(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        parent = _plain(collection_id, "P")
        child = _plain(collection_id, "C", parent.folder_id)
        child_arch = service.toggle_folder_archive(
            collection_id,
            child.folder_id,
            FolderArchiveToggle(archived=True, version=child.version),
        )
        parent_arch = service.toggle_folder_archive(
            collection_id,
            parent.folder_id,
            FolderArchiveToggle(archived=True, version=parent.version),
        )
        service.toggle_folder_archive(
            collection_id,
            parent.folder_id,
            FolderArchiveToggle(archived=False, version=parent_arch.version),
        )
        still = service.get_folder(collection_id, child.folder_id)
        assert still.archived is True
        assert still.version == child_arch.version


def test_archive_rejects_non_plain(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        grp = service.create_group(collection_id, GroupCreate(name="G1"))
        with pytest.raises(Exception) as ei:
            service.toggle_folder_archive(
                collection_id,
                grp.folder_id,
                FolderArchiveToggle(archived=True, version=1),
            )
        assert getattr(ei.value, "status_code", None) in (400, 403)


def test_move_rejects_group_and_branch_as_source(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        dest = _plain(collection_id, "Dest")
        grp = service.create_group(collection_id, GroupCreate(name="G1"))
        with pytest.raises(Exception) as ei:
            service.update_folder(
                collection_id,
                grp.folder_id,
                FolderUpdate(parent_folder_id=dest.folder_id, version=1),
            )
        assert getattr(ei.value, "status_code", None) == 403

        chains = service.list_chains(collection_id)
        main = next(c for c in chains if c.is_main)
        parent = service.create_node(
            collection_id,
            main.chain_id,
            NodeCreate(node_type="event", title="P", order=1),
        )
        ch = service.create_chain(
            collection_id,
            ChainCreate(
                parent_chain_id=main.chain_id,
                parent_node_id=parent.node_id,
                title="B1",
            ),
        )
        with pytest.raises(Exception) as ei2:
            service.update_folder(
                collection_id,
                ch.folder_id,
                FolderUpdate(parent_folder_id=dest.folder_id, version=1),
            )
        assert getattr(ei2.value, "status_code", None) == 403


def test_move_plain_into_branch_allowed(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        folder = _plain(collection_id, "Docs")
        chains = service.list_chains(collection_id)
        main = next(c for c in chains if c.is_main)
        parent = service.create_node(
            collection_id,
            main.chain_id,
            NodeCreate(node_type="event", title="P", order=1),
        )
        ch = service.create_chain(
            collection_id,
            ChainCreate(
                parent_chain_id=main.chain_id,
                parent_node_id=parent.node_id,
                title="B1",
            ),
        )
        moved = service.update_folder(
            collection_id,
            folder.folder_id,
            FolderUpdate(parent_folder_id=ch.folder_id, version=folder.version),
        )
        assert moved.parent_folder_id == ch.folder_id


def test_move_plain_into_group_rejected(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        folder = _plain(collection_id, "Docs")
        grp = service.create_group(collection_id, GroupCreate(name="G1"))
        with pytest.raises(Exception) as ei:
            service.update_folder(
                collection_id,
                folder.folder_id,
                FolderUpdate(parent_folder_id=grp.folder_id, version=folder.version),
            )
        assert getattr(ei.value, "status_code", None) == 400


def test_plain_folder_icon_coerced_to_folder(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        f = service.create_folder(
            collection_id,
            FolderCreate(
                name="Docs",
                icon_type="emoji",
                icon_value="🔥",
                icon_color="#3D8B5A",
            ),
        )
        assert f.icon_type == "lucide"
        assert f.icon_value == "folder"
        assert f.icon_color == "#3D8B5A"
        updated = service.update_folder(
            collection_id,
            f.folder_id,
            FolderUpdate(
                icon_type="lucide",
                icon_value="star",
                icon_color="#C45A6A",
                version=f.version,
            ),
        )
        assert updated.icon_type == "lucide"
        assert updated.icon_value == "folder"
        assert updated.icon_color == "#C45A6A"


def test_branch_folder_icon_coerced_to_git_branch(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        chains = service.list_chains(collection_id)
        main = next(c for c in chains if c.is_main)
        parent = service.create_node(
            collection_id,
            main.chain_id,
            NodeCreate(node_type="event", title="P", order=1),
        )
        ch = service.create_chain(
            collection_id,
            ChainCreate(
                parent_chain_id=main.chain_id,
                parent_node_id=parent.node_id,
                title="B1",
            ),
        )
        fld = service.get_folder(collection_id, ch.folder_id)
        updated = service.update_folder(
            collection_id,
            fld.folder_id,
            FolderUpdate(
                icon_type="emoji",
                icon_value="🔥",
                icon_color="#C45A6A",
                version=fld.version,
            ),
        )
        assert updated.icon_type == "lucide"
        assert updated.icon_value == "git-branch"
        assert updated.icon_color == "#C45A6A"


def test_group_rejects_folder_icon(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        grp = service.create_group(collection_id, GroupCreate(name="G1"))
        with pytest.raises(Exception) as ei:
            service.update_group(
                collection_id,
                grp.group_id,
                GroupUpdate(icon_type="lucide", icon_value="folder"),
            )
        assert getattr(ei.value, "status_code", None) == 400
        with pytest.raises(Exception) as ei2:
            service.update_folder(
                collection_id,
                grp.folder_id,
                FolderUpdate(
                    icon_type="lucide",
                    icon_value="folder",
                    version=1,
                ),
            )
        assert getattr(ei2.value, "status_code", None) == 400
        with pytest.raises(Exception) as ei3:
            service.update_group(
                collection_id,
                grp.group_id,
                GroupUpdate(icon_type="lucide", icon_value="git-branch"),
            )
        assert getattr(ei3.value, "status_code", None) == 400


def test_archive_snapshot_is_json_not_leaked_on_out(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        f = _plain(collection_id, "A")
        fid, pid, _ = _seed_file(collection_id, f.folder_id)
        out = service.toggle_folder_archive(
            collection_id,
            f.folder_id,
            FolderArchiveToggle(archived=True, version=f.version),
        )
        assert not hasattr(out, "archive_snapshot") or getattr(
            out, "archive_snapshot", None
        ) in (None, "")
        dumped = out.model_dump()
        assert "archive_snapshot" not in dumped
        conn = store.get_db(collection_id)
        snap = conn.execute(
            "SELECT archive_snapshot FROM folders WHERE folder_id=?",
            (f.folder_id,),
        ).fetchone()["archive_snapshot"]
        conn.close()
        data = json.loads(snap)
        assert pid in data["path_ids"]
        assert fid in data["promoted_file_ids"]
