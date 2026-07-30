"""Phase 5 tests: end_chain, toggle_archive, archived virtual view, is_greyed,
enhanced delete_node, promote protects from grey, end-to-end.

Run: pytest tests/test_file_mgmt_phase5.py -v --tb=short
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

    Cleans up any previous test data for this collection first.
    """
    from src.collections.store import create_collection_meta
    from src.services import services

    # Clean up any previous run's data
    coll_dir = COLLECTIONS_DIR / coll_id
    if coll_dir.exists():
        shutil.rmtree(coll_dir, ignore_errors=True)

    create_collection_meta(coll_id, f"Test {coll_id}")
    init_collection_db(coll_id)

    # Ensure Qdrant collection exists
    if services.db and not services.db.collection_exists(coll_id):
        try:
            services.db.create_collection(coll_id, vector_size=384)
        except Exception:
            pass


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


def _fake_txt_bytes(content: str = "Test document content for Phase 5 testing.") -> bytes:
    return content.encode("utf-8")


def _upload_file_to_folder(client, coll, folder_id, filename="test.txt",
                           content=b"Test content", source_node_id=None) -> dict:
    data = {"folder_id": folder_id}
    if source_node_id:
        data["source_node_id"] = source_node_id
    resp = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        data=data,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_group(client, coll, name="TestGroup") -> dict:
    resp = client.post(f"/api/file-mgmt/{coll}/groups", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_chain(client, coll, parent_chain_id, parent_node_id, title) -> dict:
    resp = client.post(
        f"/api/file-mgmt/{coll}/chains",
        json={
            "parent_chain_id": parent_chain_id,
            "parent_node_id": parent_node_id,
            "title": title,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ════════════════════════════════════════════════════════════════════
# 1. test_end_chain_greyed
# ════════════════════════════════════════════════════════════════════


def test_end_chain_greyed():
    """end_chain inheriting N1 → N2/N3 attachments greyed=1, N1's greyed=0."""
    from src.main import app

    coll = "p5-greyed"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    group = _create_group(client, coll, "Financial")
    group_id = group["group_id"]
    group_folder_id = group["folder_id"]

    # Create start node on main chain, then branch chain
    start_node = _create_node(client, coll, main_id, "Start", order=1, group_id=group_id, node_type="start")
    branch = _create_chain(client, coll, main_id, start_node["node_id"], "Q3 DD")
    branch_id = branch["chain_id"]
    branch_folder_id = branch["folder_id"]

    # Create 3 event nodes on branch chain, each with a file attached
    n1 = _create_node(client, coll, branch_id, "N1 Research", order=1, group_id=group_id)
    n2 = _create_node(client, coll, branch_id, "N2 Analysis", order=2, group_id=group_id)
    n3 = _create_node(client, coll, branch_id, "N3 Report", order=3, group_id=group_id)

    # Upload and attach file to each node
    f1 = _upload_file_to_folder(client, coll, group_folder_id, "f1.txt", _fake_txt_bytes("File 1"))
    f2 = _upload_file_to_folder(client, coll, group_folder_id, "f2.txt", _fake_txt_bytes("File 2"))
    f3 = _upload_file_to_folder(client, coll, group_folder_id, "f3.txt", _fake_txt_bytes("File 3"))

    # Attach files to nodes
    for fid, nid in [(f1["file_id"], n1["node_id"]), (f2["file_id"], n2["node_id"]), (f3["file_id"], n3["node_id"])]:
        resp = client.post(
            f"/api/file-mgmt/{coll}/nodes/{nid}/files",
            json={"file_id": fid},
        )
        assert resp.status_code == 201, resp.text

    # Create end node
    end_node = _create_node(client, coll, branch_id, "End", order=4, group_id=group_id, node_type="end")

    # End chain, inherit N1 only (N2/N3 greyed). Creates merge node on parent chain.
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{end_node['node_id']}/end-chain",
        json={"inherit_node_ids": [n1["node_id"]], "title": "Merged"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Merge node is a NEW node on the parent chain (not moving N3)
    merge_id = data.get("merged_node_id")
    assert merge_id
    assert merge_id != n3["node_id"]

    # Verify greyed_files — N2 and N3 (N1 inherited)
    assert f2["file_id"] in data["greyed_files"]
    assert f3["file_id"] in data["greyed_files"]
    assert f1["file_id"] in data["inherited_files"]

    conn = get_db(coll)
    try:
        for fid, nid, expected_greyed in [
            (f1["file_id"], n1["node_id"], 0),
            (f2["file_id"], n2["node_id"], 1),
            (f3["file_id"], n3["node_id"], 1),
        ]:
            fn = conn.execute(
                "SELECT greyed FROM file_nodes WHERE file_id=? AND node_id=?",
                (fid, nid),
            ).fetchone()
            assert fn is not None, f"file_nodes entry missing for {fid}/{nid}"
            assert fn["greyed"] == expected_greyed, (
                f"Expected greyed={expected_greyed} for {fid}/{nid}, got {fn['greyed']}"
            )
        # Branch events stay on the branch
        for nid in (n1["node_id"], n2["node_id"], n3["node_id"]):
            row = conn.execute(
                "SELECT chain_id FROM nodes WHERE node_id=?", (nid,)
            ).fetchone()
            assert row["chain_id"] == branch_id
        # Merge node lives on parent (main) chain
        mrow = conn.execute(
            "SELECT chain_id, node_type FROM nodes WHERE node_id=?", (merge_id,)
        ).fetchone()
        assert mrow["chain_id"] == main_id
        assert mrow["node_type"] == "end"
        crow = conn.execute(
            "SELECT merge_node_id FROM chains WHERE chain_id=?", (branch_id,)
        ).fetchone()
        assert crow["merge_node_id"] == merge_id
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# 2. test_end_chain_archive_candidate
# ════════════════════════════════════════════════════════════════════


def test_end_chain_archive_candidate():
    """File only on branch chain → archive_candidate. File also on main chain → not candidate.

    Strategy:
    - F1: uploaded to folder (persistent path) + attached to main AND branch nodes
    - F2: created only via node attachment on branch node (no persistent path)
    """
    from src.main import app

    coll = "p5-archive-cand"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    group = _create_group(client, coll, "Legal")
    group_id = group["group_id"]
    group_folder_id = group["folder_id"]

    # F1: upload to folder (gets persistent path) + attach to main node
    f1 = _upload_file_to_folder(client, coll, group_folder_id, "f1.txt", _fake_txt_bytes("Shared"))

    # Attach F1 to a main chain node
    main_node = _create_node(client, coll, main_id, "Main N", order=1, group_id=group_id)
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{main_node['node_id']}/files",
        json={"file_id": f1["file_id"]},
    )
    assert resp.status_code == 201

    # Create branch chain
    start_node = _create_node(client, coll, main_id, "Branch Start", order=2, group_id=group_id, node_type="start")
    branch = _create_chain(client, coll, main_id, start_node["node_id"], "DD Branch")
    branch_id = branch["chain_id"]

    # Non-terminal event: holds F1 (shared) + F2 (branch-only) → will be greyed
    b_mid = _create_node(client, coll, branch_id, "Branch Mid", order=1, group_id=group_id)
    # Terminal event: merges onto parent (auto-inherited, not greyed)
    b_term = _create_node(client, coll, branch_id, "Branch Term", order=2, group_id=group_id)

    # Attach F1 to mid branch node (F1 now has both main and branch attachment + persistent path)
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{b_mid['node_id']}/files",
        json={"file_id": f1["file_id"]},
    )
    assert resp.status_code == 201

    # Upload F2 directly to mid branch node (no persistent path, only derived paths)
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{b_mid['node_id']}/files/upload",
        files={"file": ("f2.txt", io.BytesIO(_fake_txt_bytes("Branch only")), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    f2 = resp.json()

    # Verify F2 has NO persistent path
    conn = get_db(coll)
    try:
        persistent = conn.execute(
            "SELECT path_id FROM file_paths WHERE file_id=? AND source_node_id IS NULL",
            (f2["file_id"],),
        ).fetchall()
        assert len(persistent) == 0, f"F2 should have no persistent path, got {persistent}"
    finally:
        conn.close()

    # End branch chain (don't inherit mid; terminal is auto-inherited + merged)
    end_node = _create_node(client, coll, branch_id, "End", order=3, group_id=group_id, node_type="end")
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{end_node['node_id']}/end-chain",
        json={"inherit_node_ids": [], "title": "Merged"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Merge node is created on parent (not the terminal event itself)
    assert data.get("merged_node_id")
    assert data.get("merged_node_id") != b_term["node_id"]

    # F2 is branch-only on greyed mid node, no persistent path → archive_candidate
    # (mid is not inherited; terminal may still be greyed if not selected)
    assert f2["file_id"] in data["archive_candidates"], (
        f"F2 should be archive candidate, got: {data['archive_candidates']}"
    )
    # F1 is also on main chain + has persistent path → not a candidate
    assert f1["file_id"] not in data["archive_candidates"], (
        f"F1 should NOT be archive candidate, got: {data['archive_candidates']}"
    )
    # Both files should be greyed
    assert f1["file_id"] in data["greyed_files"]
    assert f2["file_id"] in data["greyed_files"]


# ════════════════════════════════════════════════════════════════════
# 3. test_toggle_archive
# ════════════════════════════════════════════════════════════════════


def test_toggle_archive():
    """Manual archive F1 → files.archived=1 → Qdrant payload archived=true."""
    from src.main import app

    coll = "p5-toggle-arch"
    _setup_collection(coll)
    client = TestClient(app)

    group = _create_group(client, coll, "TestGroup")
    group_folder_id = group["folder_id"]

    f1 = _upload_file_to_folder(client, coll, group_folder_id, "archive_me.txt", _fake_txt_bytes("To archive"))

    # Archive
    resp = client.patch(
        f"/api/file-mgmt/{coll}/files/{f1['file_id']}/archive",
        json={"archived": True, "version": f1["version"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["archived"] is True

    # Verify DB
    conn = get_db(coll)
    try:
        f_row = conn.execute(
            "SELECT archived FROM files WHERE file_id=?", (f1["file_id"],)
        ).fetchone()
        assert f_row["archived"] == 1
    finally:
        conn.close()

    # Verify retrieval skips it (via /Archived view)
    resp_arch = client.get(f"/api/file-mgmt/{coll}/archived")
    assert resp_arch.status_code == 200
    archived_ids = [a["file_id"] for a in resp_arch.json()]
    assert f1["file_id"] in archived_ids


# ════════════════════════════════════════════════════════════════════
# 4. test_unarchive
# ════════════════════════════════════════════════════════════════════


def test_unarchive():
    """Archive then restore → files.archived=0 → no longer in /Archived."""
    from src.main import app

    coll = "p5-unarchive"
    _setup_collection(coll)
    client = TestClient(app)

    group = _create_group(client, coll, "TestGroup")
    group_folder_id = group["folder_id"]

    f1 = _upload_file_to_folder(client, coll, group_folder_id, "restore_me.txt", _fake_txt_bytes("To restore"))

    # Archive
    resp = client.patch(
        f"/api/file-mgmt/{coll}/files/{f1['file_id']}/archive",
        json={"archived": True, "version": f1["version"]},
    )
    assert resp.status_code == 200
    assert resp.json()["archived"] is True

    # Get new version after archive
    detail = client.get(f"/api/file-mgmt/{coll}/files/{f1['file_id']}").json()
    new_version = detail["version"]

    # Unarchive
    resp2 = client.patch(
        f"/api/file-mgmt/{coll}/files/{f1['file_id']}/archive",
        json={"archived": False, "version": new_version},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["archived"] is False

    # Verify DB
    conn = get_db(coll)
    try:
        f_row = conn.execute(
            "SELECT archived FROM files WHERE file_id=?", (f1["file_id"],)
        ).fetchone()
        assert f_row["archived"] == 0
    finally:
        conn.close()

    # No longer in /Archived
    resp_arch = client.get(f"/api/file-mgmt/{coll}/archived")
    archived_ids = [a["file_id"] for a in resp_arch.json()]
    assert f1["file_id"] not in archived_ids


# ════════════════════════════════════════════════════════════════════
# 5. test_archived_virtual_view
# ════════════════════════════════════════════════════════════════════


def test_archived_virtual_view():
    """Archive 2 files → GET /archived returns 2."""
    from src.main import app

    coll = "p5-archived-view"
    _setup_collection(coll)
    client = TestClient(app)

    group = _create_group(client, coll, "TestGroup")
    group_folder_id = group["folder_id"]

    f1 = _upload_file_to_folder(client, coll, group_folder_id, "a.txt", _fake_txt_bytes("A"))
    f2 = _upload_file_to_folder(client, coll, group_folder_id, "b.txt", _fake_txt_bytes("B"))
    f3 = _upload_file_to_folder(client, coll, group_folder_id, "c.txt", _fake_txt_bytes("C"))

    # Archive f1 and f2
    for f in [f1, f2]:
        resp = client.patch(
            f"/api/file-mgmt/{coll}/files/{f['file_id']}/archive",
            json={"archived": True, "version": f["version"]},
        )
        assert resp.status_code == 200

    # Archived view should have 2 files
    resp = client.get(f"/api/file-mgmt/{coll}/archived")
    assert resp.status_code == 200
    archived = resp.json()
    assert len(archived) == 2
    archived_ids = {a["file_id"] for a in archived}
    assert f1["file_id"] in archived_ids
    assert f2["file_id"] in archived_ids
    assert f3["file_id"] not in archived_ids


# ════════════════════════════════════════════════════════════════════
# 6. test_is_greyed_after_end_chain
# ════════════════════════════════════════════════════════════════════


def test_is_greyed_after_end_chain():
    """After end_chain, non-inherited files show is_greyed=True in list_files_in_folder.

    Strategy: files created via node upload (derived paths only, no persistent paths).
    """
    from src.main import app

    coll = "p5-greyed-list"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    group = _create_group(client, coll, "Financial")
    group_id = group["group_id"]
    group_folder_id = group["folder_id"]

    # Create branch chain
    start_node = _create_node(client, coll, main_id, "Start", order=1, group_id=group_id, node_type="start")
    branch = _create_chain(client, coll, main_id, start_node["node_id"], "Greyed Test")
    branch_id = branch["chain_id"]
    branch_folder_id = branch["folder_id"]

    # 2 nodes: one inherited, one not
    n1 = _create_node(client, coll, branch_id, "N1 Keep", order=1, group_id=group_id)
    n2 = _create_node(client, coll, branch_id, "N2 Discard", order=2, group_id=group_id)

    # Create files via node upload (derived paths only, no persistent paths)
    resp_f1 = client.post(
        f"/api/file-mgmt/{coll}/nodes/{n1['node_id']}/files/upload",
        files={"file": ("keep.txt", io.BytesIO(_fake_txt_bytes("Keep")), "text/plain")},
    )
    assert resp_f1.status_code == 201, resp_f1.text
    f1 = resp_f1.json()

    resp_f2 = client.post(
        f"/api/file-mgmt/{coll}/nodes/{n2['node_id']}/files/upload",
        files={"file": ("discard.txt", io.BytesIO(_fake_txt_bytes("Discard")), "text/plain")},
    )
    assert resp_f2.status_code == 201, resp_f2.text
    f2 = resp_f2.json()

    # Verify files have derived paths in group_folder
    conn = get_db(coll)
    try:
        for fid in [f1["file_id"], f2["file_id"]]:
            paths = conn.execute(
                "SELECT * FROM file_paths WHERE file_id=?", (fid,)
            ).fetchall()
            # Should have derived path (source_node_id not null)
            derived = [p for p in paths if p["source_node_id"] is not None]
            assert len(derived) >= 1, f"File {fid} should have derived paths"
    finally:
        conn.close()

    # End chain, inherit N1 only
    end_node = _create_node(client, coll, branch_id, "End", order=3, group_id=group_id, node_type="end")
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{end_node['node_id']}/end-chain",
        json={"inherit_node_ids": [n1["node_id"]], "title": "Merged"},
    )
    assert resp.status_code == 200

    # Check is_greyed in group folder
    resp_files = client.get(f"/api/file-mgmt/{coll}/folders/{group_folder_id}/files")
    assert resp_files.status_code == 200
    files_list = resp_files.json()

    # F1 (inherited) should NOT be greyed
    f1_in_list = [x for x in files_list if x["file_id"] == f1["file_id"]]
    assert len(f1_in_list) == 1, f"F1 not found in folder files: {[x['file_id'] for x in files_list]}"
    assert f1_in_list[0]["is_greyed"] is False, f"F1 should NOT be greyed, got {f1_in_list[0]['is_greyed']}"

    # F2 (not inherited) should be greyed
    f2_in_list = [x for x in files_list if x["file_id"] == f2["file_id"]]
    assert len(f2_in_list) == 1, f"F2 not found in folder files: {[x['file_id'] for x in files_list]}"
    assert f2_in_list[0]["is_greyed"] is True, f"F2 should be greyed, got {f2_in_list[0]['is_greyed']}"


# ════════════════════════════════════════════════════════════════════
# 7. test_promote_protects_from_grey
# ════════════════════════════════════════════════════════════════════


def test_promote_protects_from_grey():
    """Promote derived path → persistent → end_chain doesn't grey it.

    Strategy: file created via node upload (derived path only), then promoted.
    """
    from src.main import app

    coll = "p5-promote"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    group = _create_group(client, coll, "Legal")
    group_id = group["group_id"]
    group_folder_id = group["folder_id"]

    # Create branch chain
    start_node = _create_node(client, coll, main_id, "Start", order=1, group_id=group_id, node_type="start")
    branch = _create_chain(client, coll, main_id, start_node["node_id"], "Promote Test")
    branch_id = branch["chain_id"]

    # Node with file (upload via node → derived path only)
    n1 = _create_node(client, coll, branch_id, "N1", order=1, group_id=group_id)

    resp_f1 = client.post(
        f"/api/file-mgmt/{coll}/nodes/{n1['node_id']}/files/upload",
        files={"file": ("promote_me.txt", io.BytesIO(_fake_txt_bytes("Promote me")), "text/plain")},
    )
    assert resp_f1.status_code == 201, resp_f1.text
    f1 = resp_f1.json()

    # Find the derived path for this file
    detail = client.get(f"/api/file-mgmt/{coll}/files/{f1['file_id']}").json()
    derived_paths = [p for p in detail["paths"] if p["source_node_id"] is not None]
    assert len(derived_paths) >= 1
    path_to_promote = derived_paths[0]

    # Promote it
    resp = client.post(
        f"/api/file-mgmt/{coll}/files/{f1['file_id']}/promote-path",
        json={"path_id": path_to_promote["path_id"]},
    )
    assert resp.status_code == 200

    # End chain (don't inherit anything)
    end_node = _create_node(client, coll, branch_id, "End", order=2, group_id=group_id, node_type="end")
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{end_node['node_id']}/end-chain",
        json={"inherit_node_ids": [], "title": "Merged"},
    )
    assert resp.status_code == 200

    # Check the promoted path is NOT greyed
    detail2 = client.get(f"/api/file-mgmt/{coll}/files/{f1['file_id']}").json()
    promoted_paths = [p for p in detail2["paths"] if p["path_id"] == path_to_promote["path_id"]]
    assert len(promoted_paths) == 1
    assert promoted_paths[0]["is_greyed"] is False, (
        f"Promoted path should NOT be greyed: {promoted_paths[0]}"
    )
    assert promoted_paths[0]["source_node_id"] is None, "Promoted path should have source_node_id=NULL"


# ════════════════════════════════════════════════════════════════════
# 8. test_delete_node_with_file
# ════════════════════════════════════════════════════════════════════


def test_delete_node_with_file():
    """Node with only file attachment → delete_node returns affected_files.

    Strategy: file created via node upload (only derived path, no persistent path).
    """
    from src.main import app

    coll = "p5-del-node-file"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    group = _create_group(client, coll, "Financial")
    group_id = group["group_id"]
    group_folder_id = group["folder_id"]

    # Node on main chain
    n1 = _create_node(client, coll, main_id, "Solo Node", order=1, group_id=group_id)

    # Upload file via node (derived path only, no persistent path)
    resp_f1 = client.post(
        f"/api/file-mgmt/{coll}/nodes/{n1['node_id']}/files/upload",
        files={"file": ("solo.txt", io.BytesIO(_fake_txt_bytes("Solo file")), "text/plain")},
    )
    assert resp_f1.status_code == 201, resp_f1.text
    f1 = resp_f1.json()

    # Verify no persistent path
    conn = get_db(coll)
    try:
        persistent = conn.execute(
            "SELECT path_id FROM file_paths WHERE file_id=? AND source_node_id IS NULL",
            (f1["file_id"],),
        ).fetchall()
        assert len(persistent) == 0, f"File should have no persistent path, got {persistent}"
    finally:
        conn.close()

    # There should be derived paths from this attachment
    detail_before = client.get(f"/api/file-mgmt/{coll}/files/{f1['file_id']}").json()
    derived_before = [p for p in detail_before["paths"] if p["source_node_id"] is not None]
    assert len(derived_before) > 0, "File should have derived paths before node deletion"

    # Delete node
    resp = client.delete(f"/api/file-mgmt/{coll}/nodes/{n1['node_id']}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "affected_files" in data
    assert len(data["affected_files"]) == 1
    assert data["affected_files"][0]["file_id"] == f1["file_id"]

    # File should still exist (was NOT auto-deleted)
    detail_after = client.get(f"/api/file-mgmt/{coll}/files/{f1['file_id']}")
    assert detail_after.status_code == 200

    # But derived paths should be cleaned up
    detail_after_data = detail_after.json()
    remaining_derived = [p for p in detail_after_data["paths"] if p["source_node_id"] is not None]
    assert len(remaining_derived) == 0, "Derived paths should be cleaned up after node deletion"


# ════════════════════════════════════════════════════════════════════
# 9. test_delete_node_multi_attach
# ════════════════════════════════════════════════════════════════════


def test_delete_node_multi_attach():
    """File attached to 2 nodes → delete 1 node → file survives via other node."""
    from src.main import app

    coll = "p5-del-multi"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    group = _create_group(client, coll, "Legal")
    group_id = group["group_id"]
    group_folder_id = group["folder_id"]

    # Two nodes on main chain
    n1 = _create_node(client, coll, main_id, "Node 1", order=1, group_id=group_id)
    n2 = _create_node(client, coll, main_id, "Node 2", order=2, group_id=group_id)

    # Upload one file
    f1 = _upload_file_to_folder(client, coll, group_folder_id, "shared.txt", _fake_txt_bytes("Shared"))

    # Attach to both nodes
    for nid in [n1["node_id"], n2["node_id"]]:
        resp = client.post(
            f"/api/file-mgmt/{coll}/nodes/{nid}/files",
            json={"file_id": f1["file_id"]},
        )
        assert resp.status_code == 201

    # Delete n1
    resp = client.delete(f"/api/file-mgmt/{coll}/nodes/{n1['node_id']}")
    assert resp.status_code == 204, resp.text  # 204 = clean delete, no affected_files

    # File still exists
    detail = client.get(f"/api/file-mgmt/{coll}/files/{f1['file_id']}")
    assert detail.status_code == 200
    assert len(detail.json()["nodes"]) >= 1, "File should still be attached to n2"

    # n2 attachment still exists
    conn = get_db(coll)
    try:
        fn = conn.execute(
            "SELECT 1 FROM file_nodes WHERE file_id=? AND node_id=?",
            (f1["file_id"], n2["node_id"]),
        ).fetchone()
        assert fn is not None
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# 9b. delete branch start / merge anchors (FK safety)
# ════════════════════════════════════════════════════════════════════


def test_delete_branch_start_node_cascades_branch():
    """Deleting main-chain parent_node_id must not hit FK; branch is removed."""
    from src.main import app

    coll = "p5-del-start-anchor"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    start = _create_node(client, coll, main_id, "Branch Start", order=1, node_type="start")
    branch = _create_chain(client, coll, main_id, start["node_id"], "Side Work")
    _create_node(client, coll, branch["chain_id"], "Branch Event", order=1)

    resp = client.delete(f"/api/file-mgmt/{coll}/nodes/{start['node_id']}")
    assert resp.status_code in (200, 204), resp.text

    # Start gone
    assert client.get(f"/api/file-mgmt/{coll}/nodes/{start['node_id']}").status_code == 404
    # Branch chain gone
    chains = client.get(f"/api/file-mgmt/{coll}/chains").json()
    assert all(c["chain_id"] != branch["chain_id"] for c in chains)


def test_delete_merge_node_clears_merge_fk():
    """Deleting merge_node_id must clear chains.merge_node_id (no IntegrityError)."""
    from src.main import app

    coll = "p5-del-merge-anchor"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    start = _create_node(client, coll, main_id, "Start", order=1, node_type="start")
    branch = _create_chain(client, coll, main_id, start["node_id"], "Side")
    _create_node(client, coll, branch["chain_id"], "Work", order=1)
    end_node = _create_node(client, coll, branch["chain_id"], "End", order=2, node_type="end")

    resp_end = client.post(
        f"/api/file-mgmt/{coll}/nodes/{end_node['node_id']}/end-chain",
        json={"inherit_node_ids": [], "message_body": "done", "title": "Merged"},
    )
    assert resp_end.status_code == 200, resp_end.text
    merge_id = resp_end.json().get("merged_node_id") or resp_end.json().get("merge_node_id")
    assert merge_id, resp_end.text

    # Confirm FK is set
    chains_before = client.get(f"/api/file-mgmt/{coll}/chains").json()
    br = next(c for c in chains_before if c["chain_id"] == branch["chain_id"])
    assert br["merge_node_id"] == merge_id

    resp = client.delete(f"/api/file-mgmt/{coll}/nodes/{merge_id}")
    assert resp.status_code in (200, 204), resp.text

    # Merge node gone; branch remains open (merge cleared)
    assert client.get(f"/api/file-mgmt/{coll}/nodes/{merge_id}").status_code == 404
    chains_after = client.get(f"/api/file-mgmt/{coll}/chains").json()
    br2 = next(c for c in chains_after if c["chain_id"] == branch["chain_id"])
    assert br2["merge_node_id"] is None


# ════════════════════════════════════════════════════════════════════
# 10. test_end_to_end
# ════════════════════════════════════════════════════════════════════


def test_end_to_end():
    """Full workflow: branch → nodes → files → end_chain → archive → restore → reopen."""
    from src.main import app

    coll = "p5-e2e"
    _setup_collection(coll)
    client = TestClient(app)

    main_id = _get_main_chain_id(client, coll)
    group = _create_group(client, coll, "Financial")
    group_id = group["group_id"]
    group_folder_id = group["folder_id"]

    # 1. Create branch chain
    start_node = _create_node(client, coll, main_id, "Start DD", order=1, group_id=group_id, node_type="start")
    branch = _create_chain(client, coll, main_id, start_node["node_id"], "Due Diligence")
    branch_id = branch["chain_id"]

    # 2. Create 3 nodes with files
    nodes = []
    files = []
    for i in range(3):
        n = _create_node(client, coll, branch_id, f"Step {i+1}", order=i+1, group_id=group_id)
        nodes.append(n)
        f = _upload_file_to_folder(client, coll, group_folder_id, f"doc_{i+1}.txt", _fake_txt_bytes(f"Doc {i+1}"))
        files.append(f)
        resp = client.post(
            f"/api/file-mgmt/{coll}/nodes/{n['node_id']}/files",
            json={"file_id": f["file_id"]},
        )
        assert resp.status_code == 201

    # 3. End chain, inherit only node 1
    end_node = _create_node(client, coll, branch_id, "Final End", order=4, group_id=group_id, node_type="end")
    resp = client.post(
        f"/api/file-mgmt/{coll}/nodes/{end_node['node_id']}/end-chain",
        json={"inherit_node_ids": [nodes[0]["node_id"]], "title": "Merged"},
    )
    assert resp.status_code == 200
    end_data = resp.json()

    # 4. Archive the archive_candidates (files 2 and 3)
    for fid in end_data["archive_candidates"]:
        detail = client.get(f"/api/file-mgmt/{coll}/files/{fid}").json()
        resp_arch = client.patch(
            f"/api/file-mgmt/{coll}/files/{fid}/archive",
            json={"archived": True, "version": detail["version"]},
        )
        assert resp_arch.status_code == 200

    # 5. Check /Archived
    resp_arch_list = client.get(f"/api/file-mgmt/{coll}/archived")
    archived_files = resp_arch_list.json()
    assert len(archived_files) == len(end_data["archive_candidates"])

    # 6. Restore one archived file
    if end_data["archive_candidates"]:
        fid = end_data["archive_candidates"][0]
        detail = client.get(f"/api/file-mgmt/{coll}/files/{fid}").json()
        resp_restore = client.patch(
            f"/api/file-mgmt/{coll}/files/{fid}/archive",
            json={"archived": False, "version": detail["version"]},
        )
        assert resp_restore.status_code == 200
        assert resp_restore.json()["archived"] is False

    # 7. Reopen chain — merge node moves onto branch as last event (not deleted)
    merge_id = end_data.get("merged_node_id")
    resp_reopen = client.post(f"/api/file-mgmt/{coll}/chains/{branch_id}/reopen")
    assert resp_reopen.status_code == 200
    assert resp_reopen.json().get("merge_node_id") is None
    assert resp_reopen.json().get("has_end_node") is False

    # 8. Add a new node after reopen
    n_new = _create_node(client, coll, branch_id, "New Step After Reopen", order=4, group_id=group_id)
    f_new = _upload_file_to_folder(client, coll, group_folder_id, "new_doc.txt", _fake_txt_bytes("New Doc"))
    resp_new = client.post(
        f"/api/file-mgmt/{coll}/nodes/{n_new['node_id']}/files",
        json={"file_id": f_new["file_id"]},
    )
    assert resp_new.status_code == 201

    # 9. Verify chain has nodes including the new one + former merge node
    resp_nodes = client.get(f"/api/file-mgmt/{coll}/chains/{branch_id}/nodes")
    assert resp_nodes.status_code == 200
    chain_nodes = resp_nodes.json()
    node_titles = {n["title"] for n in chain_nodes}
    node_ids = {n["node_id"] for n in chain_nodes}
    assert "New Step After Reopen" in node_titles
    # Branch-local end marker is removed; merge node is kept as event on branch
    if merge_id:
        assert merge_id in node_ids
        merged = next(n for n in chain_nodes if n["node_id"] == merge_id)
        assert merged["node_type"] == "event"
        assert merged["chain_id"] == branch_id
