"""v2 R4/R8/R6: read-only windows, unbind conversion, Unassigned search flag.

Run: pytest tests/test_file_mgmt_v2_windows.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.file_mgmt import service, store
from src.file_mgmt.models import (
    ChainCreate,
    FolderCreate,
    EndChainRequest,
    GroupCreate,
    NodeCreate,
    NodeUpdate,
)


@pytest.fixture()
def collection_id(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "COLLECTIONS_DIR", tmp_path)
    cid = "col_" + uuid.uuid4().hex[:8]
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        store.init_collection_db(cid)
        yield cid
    shutil.rmtree(tmp_path / cid, ignore_errors=True)


def _seed_file(cid: str, folder_id: str):
    conn = store.get_db(cid)
    store._ensure_file_paths_archived(conn)
    conn.execute("PRAGMA foreign_keys=OFF")
    fid, vid, pid = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
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
    conn.execute(
        "INSERT INTO file_paths (path_id, file_id, folder_id, is_primary, "
        "source_node_id, created_by, archived) VALUES (?,?,?,1,NULL,'local',0)",
        (pid, fid, folder_id),
    )
    conn.commit()
    conn.close()
    return fid, pid


def _path_folder_ids(cid: str, fid: str) -> list[str]:
    conn = store.get_db(cid)
    rows = conn.execute(
        "SELECT folder_id FROM file_paths WHERE file_id=?", (fid,)
    ).fetchall()
    conn.close()
    return [r["folder_id"] for r in rows]


def _root_fid(cid: str) -> str:
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT folder_id FROM folders WHERE name='Root' AND is_system=1 LIMIT 1"
    ).fetchone()
    conn.close()
    assert row
    return row["folder_id"]


def _unassigned_fid(cid: str) -> str:
    conn = store.get_db(cid)
    row = conn.execute(
        "SELECT folder_id FROM folders WHERE name='Unassigned' AND is_system=1 LIMIT 1"
    ).fetchone()
    conn.close()
    assert row
    return row["folder_id"]


def _main_chain_id(cid: str) -> str:
    main = next(c for c in service.list_chains(cid) if c.is_main)
    return main.chain_id


def _branch(collection_id: str):
    chains = service.list_chains(collection_id)
    main = next(c for c in chains if c.is_main)
    parent = service.create_node(
        collection_id, main.chain_id, NodeCreate(node_type="event", title="P", order=1)
    )
    return service.create_chain(
        collection_id,
        ChainCreate(parent_chain_id=main.chain_id, parent_node_id=parent.node_id, title="B1"),
    )


# ── R4: read-only observation windows ────────────────────────────


def _run_async(coro):
    """asyncio.run() closes the main-thread loop and poisons later sync tests
    (asyncio.get_event_loop raises). Manage our own loop instead."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_upload_into_branch_or_group_rejected(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        ch = _branch(collection_id)
        grp = service.create_group(collection_id, GroupCreate(name="G1"))
        for folder_id in (ch.folder_id, grp.folder_id):
            with pytest.raises(HTTPException) as ei:
                service.upload_file_to_folder(
                    collection_id, folder_id, b"x", "f.txt"
                )
            assert ei.value.status_code == 403


def test_create_folder_inside_branch_rejected(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        ch = _branch(collection_id)
        with pytest.raises(HTTPException) as ei:
            service.create_folder(
                collection_id, FolderCreate(name="Sub", parent_folder_id=ch.folder_id)
            )
        assert ei.value.status_code == 403


def test_move_folder_into_branch_rejected(collection_id):
    """Read-only window: plain folders can no longer be moved under a branch."""
    from src.file_mgmt.models import FolderUpdate

    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        folder = service.create_folder(collection_id, FolderCreate(name="Docs"))
        ch = _branch(collection_id)
        with pytest.raises(HTTPException) as ei:
            service.update_folder(
                collection_id,
                folder.folder_id,
                FolderUpdate(parent_folder_id=ch.folder_id, version=folder.version),
            )
        assert ei.value.status_code == 403


# ── R8: unbind converts derived mounts to real ones ──────────────


def test_delete_group_converts_derived_paths(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        grp = service.create_group(collection_id, GroupCreate(name="G2"))
        fid, pid = _seed_file(collection_id, grp.folder_id)
        conn = store.get_db(collection_id)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "UPDATE file_paths SET source_node_id='node-x' WHERE path_id=?", (pid,)
        )
        conn.commit()
        conn.close()

        service.delete_group(collection_id, grp.group_id)

        conn = store.get_db(collection_id)
        rows = conn.execute(
            "SELECT source_node_id FROM file_paths WHERE file_id=?", (fid,)
        ).fetchall()
        kind = conn.execute(
            "SELECT kind FROM folders WHERE folder_id=?", (grp.folder_id,)
        ).fetchone()["kind"]
        conn.close()
        assert kind == "plain"
        assert rows and all(r["source_node_id"] is None for r in rows), (
            "Derived mounts must become persistent on group unbind"
        )


# ── R6: Unassigned-only files excluded from search ───────────────


def test_unassigned_search_flag_synced_on_mount_loss(collection_id, monkeypatch):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = service.create_folder(
            collection_id, FolderCreate(name="A")
        ) if False else _plain(collection_id, "A")
        fid, pid = _seed_file(collection_id, a.folder_id)
        calls: list[tuple[str, bool]] = []

        from src.file_mgmt import files as fm_files

        monkeypatch.setattr(
            fm_files,
            "_set_qdrant_chunks_unassigned",
            lambda cid, f, flag: calls.append((f, flag)) or 0,
        )
        service.remove_file_path(collection_id, fid, pid)
        # settle and remove_file_path both sync — final state is what matters
        assert calls and calls[-1] == (fid, True)
        assert all(f == fid and flag is True for f, flag in calls)


def test_unassigned_search_flag_cleared_when_mounted_elsewhere(collection_id, monkeypatch):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        from src.file_mgmt.models import FolderCreate

        a = service.create_folder(collection_id, FolderCreate(name="A"))
        b = service.create_folder(collection_id, FolderCreate(name="B"))
        fid, pid = _seed_file(collection_id, a.folder_id)
        calls: list[tuple[str, bool]] = []

        from src.file_mgmt import files as fm_files

        monkeypatch.setattr(
            fm_files,
            "_set_qdrant_chunks_unassigned",
            lambda cid, f, flag: calls.append((f, flag)) or 0,
        )
        # Move A → B: add B path first, then remove A path — never Unassigned
        service.add_file_path(collection_id, fid, b.folder_id)
        service.remove_file_path(collection_id, fid, pid)
        assert calls[-1] == (fid, False)
        assert all(flag is False for _, flag in calls)


def _plain(cid: str, name: str):
    return service.create_folder(cid, FolderCreate(name=name))


# ── R6: library browsing hides the drain ─────────────────────────


def test_build_library_tree_hides_unassigned(collection_id):
    """MCP/agent library tree must not reveal Unassigned or its sole files."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        out = service.upload_file_to_folder(
            collection_id, a.folder_id, b"x", "gone.txt"
        )
        conn = store.get_db(collection_id)
        pid = conn.execute(
            "SELECT path_id FROM file_paths WHERE file_id=?", (out.file_id,)
        ).fetchone()["path_id"]
        conn.close()
        service.remove_file_path(collection_id, out.file_id, pid)

        tree = service.build_library_tree(collection_id)
        names = [f["name"] for f in tree["folders"]]
        assert "Unassigned" not in names
        all_ids = [
            ref["file_id"] for f in tree["folders"] for ref in f.get("files", [])
        ]
        assert out.file_id not in all_ids
        # UI tree keeps Unassigned visible, keeps Root hidden
        ui_tree = service.get_folder_tree(collection_id)
        ui_names = [n.name for n in ui_tree]
        # Unassigned stays visible as the drain view; Root is a move destination
        assert "Unassigned" in ui_names and "Root" in ui_names


def test_retriever_exclusion_filter_keys():
    from src.rag.retriever import Retriever

    keys = [c.key for c in Retriever.exclusion_filter(None).must_not]
    assert keys == ["chunk_type", "archived", "is_current", "unassigned"]
    assert Retriever.exclusion_filter(None).must_not[0].match.value == "__config__"


# ── R6: AI-facing document tools gate on Unassigned-only files ───


def test_document_tools_gate_unassigned_only(collection_id, monkeypatch):
    from unittest.mock import MagicMock

    from src.mcp.tools.documents import (
        get_document_text,
        get_file_chunks,
        list_documents,
    )
    from src.mcp.tools.file_mgmt import get_file, list_files
    from src.services import services

    # require_collection needs a services.db; Qdrant calls never happen
    # because the gates fire first. monkeypatch restores it after the test —
    # a raw assignment would leak the mock into every later test.
    db_mock = MagicMock()
    db_mock.collection_exists.return_value = True
    db_mock.scroll_points.return_value = ([], None)
    monkeypatch.setattr(services, "db", db_mock)

    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        visible = service.upload_file_to_folder(
            collection_id, a.folder_id, b"visible", "keep.txt"
        )
        doomed = service.upload_file_to_folder(
            collection_id, a.folder_id, b"hidden", "gone.txt"
        )
        conn = store.get_db(collection_id)
        pid = conn.execute(
            "SELECT path_id FROM file_paths WHERE file_id=?", (doomed.file_id,)
        ).fetchone()["path_id"]
        conn.close()
        service.remove_file_path(collection_id, doomed.file_id, pid)

        import json as _json

        def _text(out) -> str:
            sc = getattr(out, "structuredContent", None) or getattr(out, "structured_content", None)
            return _json.dumps(sc if sc is not None else out, default=str, ensure_ascii=False)

        async def run():
            lf = _text(await list_files(collection_id))
            assert '"keep.txt"' in lf
            assert "gone.txt" not in lf

            ld = _text(await list_documents(collection_id))
            assert "gone.txt" not in ld

            gf = _text(await get_file(collection_id, doomed.file_id))
            assert "Unassigned" in gf

            gdt = _text(await get_document_text(collection_id, file_id=doomed.file_id))
            assert "Unassigned" in gdt

            gfc = _text(await get_file_chunks(collection_id, file_id=doomed.file_id))
            assert "Unassigned" in gfc

        _run_async(run())

# ── R5: read tools also gate on file-level archived files ────────


def test_read_tools_gate_archived_files(collection_id, monkeypatch):
    from src.file_mgmt.models import ArchiveToggle
    from src.mcp.tools.documents import (
        get_document_text,
        get_file_chunks,
    )

    from unittest.mock import MagicMock

    from src.services import services

    services_db_mock = MagicMock()
    services_db_mock.collection_exists.return_value = True
    monkeypatch.setattr(services, "db", services_db_mock)

    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        a = _plain(collection_id, "A")
        fid = service.upload_file_to_folder(
            collection_id, a.folder_id, b"archived content", "hidden_by_archive.txt"
        ).file_id
        service.toggle_archive(
            collection_id,
            fid,
            ArchiveToggle(archived=True, scope="file", version=1),
        )

        import json as _json

        def _text(out) -> str:
            sc = getattr(out, "structuredContent", None) or getattr(out, "structured_content", None)
            return _json.dumps(sc if sc is not None else out, default=str, ensure_ascii=False)

        async def run():
            gdt = _text(await get_document_text(collection_id, file_id=fid))
            assert "archived" in gdt.lower()
            gfc = _text(await get_file_chunks(collection_id, file_id=fid))
            assert "archived" in gfc.lower()

        _run_async(run())


# ── attaching from Unassigned moves the file out of the drain ────


def test_attach_from_unassigned_moves_file_to_node_window(collection_id):
    from src.file_mgmt.models import NodeCreate, GroupCreate

    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        grp = service.create_group(collection_id, GroupCreate(name="G3"))
        node = service.create_node(
            collection_id,
            _main_chain_id(collection_id),
            NodeCreate(node_type="event", title="N", order=1, group_id=grp.group_id),
        )
        # File parked in Unassigned
        fid, _ = _seed_file(collection_id, _unassigned_fid(collection_id))

        service.attach_file_to_node(collection_id, node.node_id, fid)

        # Out of Unassigned, into the group window
        assert _path_folder_ids(collection_id, fid) == [grp.folder_id]


def test_attach_without_group_keeps_file_in_unassigned(collection_id):
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        chains = service.list_chains(collection_id)
        main = next(c for c in chains if c.is_main)
        node = service.create_node(
            collection_id,
            main.chain_id,
            NodeCreate(node_type="event", title="Free", order=1),
        )
        fid, _ = _seed_file(collection_id, _unassigned_fid(collection_id))

        service.attach_file_to_node(collection_id, node.node_id, fid)

        # No group folder to move into — file stays parked
        assert _path_folder_ids(collection_id, fid) == [_unassigned_fid(collection_id)]


def test_attach_then_delete_original_path_keeps_file_in_window(collection_id):
    """Attach to a grouped node, then delete the original mount: the file
    stays in the group window and never falls into Unassigned."""
    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        grp = service.create_group(collection_id, GroupCreate(name="GW"))
        node = service.create_node(
            collection_id,
            _main_chain_id(collection_id),
            NodeCreate(node_type="event", title="N", order=1, group_id=grp.group_id),
        )
        a = _plain(collection_id, "A")
        fid, pid_a = _seed_file(collection_id, a.folder_id)
        service.attach_file_to_node(collection_id, node.node_id, fid)

        # sanity: two mounts (A persistent + group derived)
        assert set(_path_folder_ids(collection_id, fid)) == {a.folder_id, grp.folder_id}

        # delete the original mount in A
        service.remove_file_path(collection_id, fid, pid_a)

        mounts = _path_folder_ids(collection_id, fid)
        assert grp.folder_id in mounts
        assert _unassigned_fid(collection_id) not in mounts


# ── end chain with empty group_id (No group from the dialog) ──────


def test_end_chain_with_empty_group_id_succeeds(collection_id):
    """The dialog sends group_id="" for "No group" — an empty string must
    not violate the nodes.group_id FK at merge-node INSERT (user bug)."""
    from src.file_mgmt.models import ChainCreate

    with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
        main = _main_chain_id(collection_id)
        fork = service.create_node(
            collection_id,
            main,
            NodeCreate(node_type="event", title="fork", order=1),
        )
        service.update_node(
            collection_id, fork.node_id, NodeUpdate(node_type="start", version=fork.version)
        )
        br = service.create_chain(
            collection_id,
            ChainCreate(parent_chain_id=main, parent_node_id=fork.node_id, title="B"),
        )
        service.create_node(
            collection_id, br.chain_id, NodeCreate(node_type="event", title="W", order=1)
        )
        end = service.create_node(
            collection_id,
            br.chain_id,
            NodeCreate(node_type="end", title="End", order=2, node_type2=None) if False else NodeCreate(node_type="end", title="End", order=2),
        )
        out = service.end_chain(
            collection_id,
            end.node_id,
            EndChainRequest(inherit_node_ids=[], message_body="done", title="Merged", group_id=""),
        )
        assert out.get("merged_node_id")

# ── end chain: inherit/非inherit × Group 窗口语义 ─────────────────


def test_end_chain_group_window_scenarios(collection_id):
    """节点1(Group1)+文件1 继承；节点2(Group2)+文件2 未继承；
    merge Group = Group 3；文件3 走结案附件。
    - 文件1：挂载不变（Root/Group1/支链）+ 文件节点挂到 merge
    - 文件2：Group 2 + 支线路径全部灰显 → 无活跃挂载 → 晋升全局归档
    - 文件3：文件节点挂到 merge 节点（Group 3 投影由前端 attach 补齐）
    """
    g1 = service.create_group(collection_id, GroupCreate(name="G1"))
    g2 = service.create_group(collection_id, GroupCreate(name="G2"))
    g3 = service.create_group(collection_id, GroupCreate(name="G3"))
    main = _main_chain_id(collection_id)
    fork = service.create_node(
        collection_id, main, NodeCreate(node_type="event", title="fork", order=1)
    )
    service.update_node(
        collection_id, fork.node_id, NodeUpdate(node_type="start", version=fork.version)
    )
    br = service.create_chain(
        collection_id,
        ChainCreate(parent_chain_id=main, parent_node_id=fork.node_id, title="B"),
    )
    n1 = service.create_node(
        collection_id,
        br.chain_id,
        NodeCreate(node_type="event", title="N1", order=1, group_id=g1.group_id),
    )
    n2 = service.create_node(
        collection_id,
        br.chain_id,
        NodeCreate(node_type="event", title="N2", order=2, group_id=g2.group_id),
    )
    end = service.create_node(
        collection_id,
        br.chain_id,
        NodeCreate(node_type="end", title="End", order=3),
    )
    f1 = service.upload_file_to_folder(collection_id, None, b"1", "f1.txt").file_id
    f2 = service.upload_file_to_folder(collection_id, None, b"2", "f2.txt").file_id
    f3 = service.upload_file_to_folder(collection_id, None, b"3", "f3.txt").file_id
    service.attach_file_to_node(collection_id, n1.node_id, f1)
    service.attach_file_to_node(collection_id, n2.node_id, f2)
    # 真实节点上传的文件只驻留在节点窗口（Root 挂载仅是上传落点，随即摘除）
    root_fid = _root_fid(collection_id)
    for fid in (f1, f2, f3):
        conn = store.get_db(collection_id)
        rp = conn.execute(
            "SELECT path_id FROM file_paths WHERE file_id=? AND folder_id=?",
            (fid, root_fid),
        ).fetchone()
        conn.close()
        if rp:
            service.remove_file_path(collection_id, fid, rp["path_id"])

    service.end_chain(
        collection_id,
        end.node_id,
        EndChainRequest(
            inherit_node_ids=[n1.node_id],
            group_id=g3.group_id,
            title="Merged",
            attachment_file_ids=[f3],
        ),
    )

    conn = store.get_db(collection_id)

    def mounts(fid):
        return sorted(
            r["folder_id"]
            for r in conn.execute(
                "SELECT folder_id FROM file_paths WHERE file_id=?", (fid,)
            ).fetchall()
        )

    def greyed(fid, folder):
        row = conn.execute(
            "SELECT archived FROM file_paths WHERE file_id=? AND folder_id=?",
            (fid, folder),
        ).fetchone()
        return bool(row and row["archived"])

    # 文件 1（继承）：挂载不变（Group1/支链）+ 新增 Group 3 投影，本体挂在 merge 节点上
    assert set(mounts(f1)) == {g1.folder_id, br.folder_id, g3.folder_id}
    # 文件 2（未继承）：所有投影灰显 + 晋升全局归档
    assert greyed(f2, g2.folder_id) is True
    assert greyed(f2, br.folder_id) is True
    f2row = conn.execute(
        "SELECT archived FROM files WHERE file_id=?", (f2,)
    ).fetchone()
    assert f2row["archived"] == 1
    # 文件节点：文件 1 与文件 3 挂到 merge 节点
    merge_id = conn.execute(
        "SELECT merge_node_id FROM chains WHERE chain_id=?", (br.chain_id,)
    ).fetchone()["merge_node_id"]
    assert merge_id
    fn1 = conn.execute(
        "SELECT 1 FROM file_nodes WHERE file_id=? AND node_id=?", (f1, merge_id)
    ).fetchone()
    fn3 = conn.execute(
        "SELECT 1 FROM file_nodes WHERE file_id=? AND node_id=?", (f3, merge_id)
    ).fetchone()
    conn.close()
    assert fn1 and fn3
