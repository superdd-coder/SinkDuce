"""Phase 3 tests: file paths, node attachments, messages.

Run: pytest tests/test_file_mgmt_phase3.py -v --tb=short
"""

from __future__ import annotations

import shutil
import uuid

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
    """Create a collection with meta.json + meta.db for testing."""
    from src.collections.store import create_collection_meta

    create_collection_meta(coll_id, f"Test {coll_id}")
    init_collection_db(coll_id)


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


def _create_file_record(coll_id: str, file_id: str, archived: int = 0) -> None:
    """Manually insert a file record + file_version into meta.db.

    Uses deferred FK checks to handle the circular FK between files <-> file_versions.
    """
    now = "2026-07-28T00:00:00+00:00"
    version_id = uuid.uuid4().hex
    conn = get_db(coll_id)
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO file_versions
               (version_id, file_id, version_no, storage_file_id,
                archived, commit_message, created_by, created_at)
               VALUES (?, ?, 1, ?, 0, 'initial', 'local', ?)""",
            (version_id, file_id, f"doc_{file_id[:6]}.pdf", now),
        )
        conn.execute(
            """INSERT INTO files
               (file_id, current_version_id, is_definitive, archived,
                unsupported, created_by, version)
               VALUES (?, ?, 0, ?, 0, 'local', 1)""",
            (file_id, version_id, archived),
        )
        conn.commit()
    finally:
        conn.close()


# ── 1. File path add/remove ─────────────────────────────────────


def test_file_path_add_remove():
    """Create file record -> add_path -> query -> remove_path."""
    from src.main import app

    _setup_collection("p3-1")
    client = TestClient(app)

    # Get a folder to add the file to
    resp = client.get("/api/file-mgmt/p3-1/folders")
    tree = resp.json()
    # Find a non-system folder, or create one
    plain_folders = [f for f in tree if f["kind"] == "plain"]
    if not plain_folders:
        resp = client.post(
            "/api/file-mgmt/p3-1/folders",
            json={"name": "MyFolder"},
        )
        assert resp.status_code == 201
        folder_id = resp.json()["folder_id"]
    else:
        folder_id = plain_folders[0]["folder_id"]

    fid = uuid.uuid4().hex
    _create_file_record("p3-1", fid)

    # add path
    resp = client.post(
        f"/api/file-mgmt/p3-1/files/{fid}/paths",
        json={"folder_id": folder_id},
    )
    assert resp.status_code == 201, resp.text
    path = resp.json()
    assert path["file_id"] == fid
    assert path["folder_id"] == folder_id
    assert path["source_node_id"] is None  # persistent path
    path_id = path["path_id"]

    # query file detail
    resp = client.get(f"/api/file-mgmt/p3-1/files/{fid}")
    assert resp.status_code == 200
    detail = resp.json()
    assert len(detail["paths"]) >= 1
    assert any(p["path_id"] == path_id for p in detail["paths"])

    # remove path
    resp = client.delete(f"/api/file-mgmt/p3-1/files/{fid}/paths/{path_id}")
    assert resp.status_code == 204

    # verify path removed
    resp = client.get(f"/api/file-mgmt/p3-1/files/{fid}")
    assert resp.status_code == 200
    assert all(p["path_id"] != path_id for p in resp.json()["paths"])


# ── 2. Attach generates derived paths ───────────────────────────


def test_attach_generates_derived_paths():
    """Create node (group=Financial, chain=branch) -> attach file -> verify 2 derived paths."""
    from src.main import app

    _setup_collection("p3-2")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-2")

    # create group
    resp = client.post(
        "/api/file-mgmt/p3-2/groups",
        json={"name": "Financial"},
    )
    assert resp.status_code == 201
    group = resp.json()
    gid = group["group_id"]

    # create branch chain
    pnode = _create_node(client, "p3-2", main_id, "trigger", order=1)
    resp = client.post(
        "/api/file-mgmt/p3-2/chains",
        json={
            "parent_chain_id": main_id,
            "parent_node_id": pnode["node_id"],
            "title": "DD Analysis",
        },
    )
    assert resp.status_code == 201
    chain = resp.json()
    chain_id = chain["chain_id"]

    # create node on branch chain with group
    node = _create_node(
        client, "p3-2", chain_id, "kickoff", order=1, group_id=gid
    )
    nid = node["node_id"]

    # create file record
    fid = uuid.uuid4().hex
    _create_file_record("p3-2", fid)

    # attach file to node
    resp = client.post(
        f"/api/file-mgmt/p3-2/nodes/{nid}/files",
        json={"file_id": fid},
    )
    assert resp.status_code == 201, resp.text

    # verify file has 2 derived paths (group folder + chain folder)
    resp = client.get(f"/api/file-mgmt/p3-2/files/{fid}")
    assert resp.status_code == 200
    detail = resp.json()
    paths = detail["paths"]
    assert len(paths) == 2, f"Expected 2 derived paths, got {len(paths)}: {paths}"

    # verify both are derived paths (source_node_id = node_id)
    for p in paths:
        assert p["source_node_id"] == nid, f"Expected derived path, got {p}"

    # one path should be in the group folder
    group_folder_id = group["folder_id"]
    chain_folder_id = chain["folder_id"]
    path_folder_ids = {p["folder_id"] for p in paths}
    assert group_folder_id in path_folder_ids
    assert chain_folder_id in path_folder_ids


# ── 3. Attach to main chain node ────────────────────────────────


def test_attach_main_chain_node():
    """Main chain node attach file -> verify only 1 derived path (group, no chain folder)."""
    from src.main import app

    _setup_collection("p3-3")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-3")

    # create group
    resp = client.post(
        "/api/file-mgmt/p3-3/groups",
        json={"name": "Legal"},
    )
    assert resp.status_code == 201
    group = resp.json()
    gid = group["group_id"]

    # create node on main chain with group
    node = _create_node(
        client, "p3-3", main_id, "main-event", order=1, group_id=gid
    )
    nid = node["node_id"]

    # create file record
    fid = uuid.uuid4().hex
    _create_file_record("p3-3", fid)

    # attach file to node
    resp = client.post(
        f"/api/file-mgmt/p3-3/nodes/{nid}/files",
        json={"file_id": fid},
    )
    assert resp.status_code == 201, resp.text

    # verify only 1 derived path (group folder only, no chain folder for main chain)
    resp = client.get(f"/api/file-mgmt/p3-3/files/{fid}")
    assert resp.status_code == 200
    detail = resp.json()
    paths = detail["paths"]
    assert len(paths) == 1, f"Expected 1 derived path, got {len(paths)}: {paths}"
    assert paths[0]["source_node_id"] == nid
    assert paths[0]["folder_id"] == group["folder_id"]


# ── 4. Detach removes derived paths ─────────────────────────────


def test_detach_removes_derived_paths():
    """Attach then detach -> derived paths gone, persistent path stays."""
    from src.main import app

    _setup_collection("p3-4")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-4")

    # create group
    resp = client.post(
        "/api/file-mgmt/p3-4/groups",
        json={"name": "TestGroup"},
    )
    assert resp.status_code == 201
    group = resp.json()
    gid = group["group_id"]

    # create a plain folder for persistent path
    resp = client.post(
        "/api/file-mgmt/p3-4/folders",
        json={"name": "PersistFolder"},
    )
    assert resp.status_code == 201
    persist_folder_id = resp.json()["folder_id"]

    # create branch chain
    pnode = _create_node(client, "p3-4", main_id, "trigger", order=1)
    resp = client.post(
        "/api/file-mgmt/p3-4/chains",
        json={
            "parent_chain_id": main_id,
            "parent_node_id": pnode["node_id"],
            "title": "TestBranch",
        },
    )
    assert resp.status_code == 201
    chain_id = resp.json()["chain_id"]

    # create node
    node = _create_node(
        client, "p3-4", chain_id, "test-node", order=1, group_id=gid
    )
    nid = node["node_id"]

    # create file record
    fid = uuid.uuid4().hex
    _create_file_record("p3-4", fid)

    # add persistent path first
    resp = client.post(
        f"/api/file-mgmt/p3-4/files/{fid}/paths",
        json={"folder_id": persist_folder_id},
    )
    assert resp.status_code == 201

    # attach file to node (generates derived paths)
    resp = client.post(
        f"/api/file-mgmt/p3-4/nodes/{nid}/files",
        json={"file_id": fid},
    )
    assert resp.status_code == 201

    # verify: persistent path + 2 derived paths = 3 total
    resp = client.get(f"/api/file-mgmt/p3-4/files/{fid}")
    detail = resp.json()
    assert len(detail["paths"]) == 3, f"Expected 3 paths, got {len(detail['paths'])}"

    # detach
    resp = client.delete(f"/api/file-mgmt/p3-4/nodes/{nid}/files/{fid}")
    assert resp.status_code == 204

    # verify: only persistent path remains
    resp = client.get(f"/api/file-mgmt/p3-4/files/{fid}")
    detail = resp.json()
    paths = detail["paths"]
    assert len(paths) == 1, f"Expected 1 path, got {len(paths)}"
    assert paths[0]["folder_id"] == persist_folder_id
    assert paths[0]["source_node_id"] is None  # persistent path


# ── 5. Promote path ─────────────────────────────────────────────


def test_promote_path():
    """Attach generates derived path -> promote -> source_node_id becomes NULL."""
    from src.main import app

    _setup_collection("p3-5")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-5")

    # create group
    resp = client.post(
        "/api/file-mgmt/p3-5/groups",
        json={"name": "PromoGroup"},
    )
    assert resp.status_code == 201
    group = resp.json()
    gid = group["group_id"]

    # create node on main chain (main chain -> only group derived path)
    node = _create_node(
        client, "p3-5", main_id, "promo-node", order=1, group_id=gid
    )
    nid = node["node_id"]

    # create file record
    fid = uuid.uuid4().hex
    _create_file_record("p3-5", fid)

    # attach file to node
    resp = client.post(
        f"/api/file-mgmt/p3-5/nodes/{nid}/files",
        json={"file_id": fid},
    )
    assert resp.status_code == 201

    # get the derived path
    resp = client.get(f"/api/file-mgmt/p3-5/files/{fid}")
    detail = resp.json()
    derived = [p for p in detail["paths"] if p["source_node_id"] is not None]
    assert len(derived) == 1
    path_id = derived[0]["path_id"]
    assert derived[0]["source_node_id"] == nid

    # promote
    resp = client.post(
        f"/api/file-mgmt/p3-5/files/{fid}/promote-path",
        json={"path_id": path_id},
    )
    assert resp.status_code == 200, resp.text
    promoted = resp.json()
    assert promoted["source_node_id"] is None  # now persistent

    # verify persisted
    resp = client.get(f"/api/file-mgmt/p3-5/files/{fid}")
    detail = resp.json()
    for p in detail["paths"]:
        if p["path_id"] == path_id:
            assert p["source_node_id"] is None

    # demote (unpin) → back to derived; path stays in list
    resp = client.post(
        f"/api/file-mgmt/p3-5/files/{fid}/demote-path",
        json={"path_id": path_id},
    )
    assert resp.status_code == 200, resp.text
    demoted = resp.json()
    assert demoted["source_node_id"] == nid
    assert demoted["path_id"] == path_id

    resp = client.get(f"/api/file-mgmt/p3-5/files/{fid}")
    detail = resp.json()
    path_ids = [p["path_id"] for p in detail["paths"]]
    assert path_id in path_ids
    for p in detail["paths"]:
        if p["path_id"] == path_id:
            assert p["source_node_id"] == nid


def test_demote_plain_folder_path_keeps_path():
    """Unpin (demote) on a plain folder mount must not delete the path card.

    Files uploaded/added to a folder have source_node_id=NULL (shown as pinned
    in UI). Demote with no reclaimable timeline node must refuse and leave the
    path in place — removal is ``remove-path``, not unpin.
    """
    from src.main import app

    _setup_collection("p3-5b")
    client = TestClient(app)

    # plain user folder (not a group/branch derived target)
    resp = client.post(
        "/api/file-mgmt/p3-5b/folders",
        json={"name": "Docs"},
    )
    assert resp.status_code == 201, resp.text
    folder_id = resp.json()["folder_id"]

    fid = uuid.uuid4().hex
    _create_file_record("p3-5b", fid)

    resp = client.post(
        f"/api/file-mgmt/p3-5b/files/{fid}/paths",
        json={"folder_id": folder_id},
    )
    assert resp.status_code == 201, resp.text
    path_id = resp.json()["path_id"]
    assert resp.json()["source_node_id"] is None

    # demote must not wipe the only path
    resp = client.post(
        f"/api/file-mgmt/p3-5b/files/{fid}/demote-path",
        json={"path_id": path_id},
    )
    assert resp.status_code == 400, resp.text

    resp = client.get(f"/api/file-mgmt/p3-5b/files/{fid}")
    detail = resp.json()
    path_ids = [p["path_id"] for p in detail["paths"]]
    assert path_id in path_ids
    assert len(detail["paths"]) == 1
    assert detail["paths"][0]["source_node_id"] is None


def test_demote_drops_pin_when_derived_sibling_exists():
    """When pin + derived share a folder, unpin deletes only the pin row."""
    from src.main import app

    _setup_collection("p3-5c")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-5c")
    resp = client.post(
        "/api/file-mgmt/p3-5c/groups",
        json={"name": "BothPaths"},
    )
    assert resp.status_code == 201
    group = resp.json()
    folder_id = group["folder_id"]
    node = _create_node(
        client, "p3-5c", main_id, "both-node", order=1, group_id=group["group_id"]
    )
    nid = node["node_id"]

    fid = uuid.uuid4().hex
    _create_file_record("p3-5c", fid)

    # persistent pin
    resp = client.post(
        f"/api/file-mgmt/p3-5c/files/{fid}/paths",
        json={"folder_id": folder_id},
    )
    assert resp.status_code == 201
    pin_id = resp.json()["path_id"]

    # derived via attach
    resp = client.post(
        f"/api/file-mgmt/p3-5c/nodes/{nid}/files",
        json={"file_id": fid},
    )
    assert resp.status_code == 201

    resp = client.get(f"/api/file-mgmt/p3-5c/files/{fid}")
    paths = [p for p in resp.json()["paths"] if p["folder_id"] == folder_id]
    assert len(paths) == 2

    resp = client.post(
        f"/api/file-mgmt/p3-5c/files/{fid}/demote-path",
        json={"path_id": pin_id},
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/api/file-mgmt/p3-5c/files/{fid}")
    remaining = [p for p in resp.json()["paths"] if p["folder_id"] == folder_id]
    assert len(remaining) == 1
    assert remaining[0]["source_node_id"] == nid
    assert remaining[0]["path_id"] != pin_id


# ── 6. UNIQUE constraint: persistent + derived coexist ──────────


def test_unique_constraint():
    """File has persistent path (F, folder, NULL) + attach node -> (F, folder, N) coexists."""
    from src.main import app

    _setup_collection("p3-6")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-6")

    # create group -> get folder_id
    resp = client.post(
        "/api/file-mgmt/p3-6/groups",
        json={"name": "UniqueGroup"},
    )
    assert resp.status_code == 201
    group = resp.json()
    group_folder_id = group["folder_id"]

    # create node on main chain
    node = _create_node(
        client, "p3-6", main_id, "uniq-node", order=1, group_id=group["group_id"]
    )
    nid = node["node_id"]

    # create file record
    fid = uuid.uuid4().hex
    _create_file_record("p3-6", fid)

    # add persistent path to group folder
    resp = client.post(
        f"/api/file-mgmt/p3-6/files/{fid}/paths",
        json={"folder_id": group_folder_id},
    )
    assert resp.status_code == 201  # persistent path (source_node_id=NULL)

    # attach file to node -> should create derived path (source_node_id=nid)
    resp = client.post(
        f"/api/file-mgmt/p3-6/nodes/{nid}/files",
        json={"file_id": fid},
    )
    assert resp.status_code == 201

    # verify: both paths coexist
    resp = client.get(f"/api/file-mgmt/p3-6/files/{fid}")
    detail = resp.json()
    paths_in_group = [p for p in detail["paths"] if p["folder_id"] == group_folder_id]
    # 1 persistent + 1 derived = 2 paths in the same folder
    assert len(paths_in_group) == 2, (
        f"Expected 2 paths in same folder (persistent + derived), "
        f"got {len(paths_in_group)}"
    )
    sources = {p["source_node_id"] for p in paths_in_group}
    assert None in sources
    assert nid in sources


# ── 7. Message CRUD ─────────────────────────────────────────────


def test_message_crud():
    """Create folder message -> edit -> delete."""
    from src.main import app

    _setup_collection("p3-7")
    client = TestClient(app)

    # get a folder
    resp = client.get("/api/file-mgmt/p3-7/folders")
    tree = resp.json()
    # use a system folder for message test
    meeting = [f for f in tree if f["name"] == "Meeting"]
    assert meeting
    folder_id = meeting[0]["folder_id"]

    # create message
    resp = client.post(
        f"/api/file-mgmt/p3-7/folders/{folder_id}/messages",
        json={"body": "## Test message", "owner_type": "folder", "owner_id": folder_id},
    )
    assert resp.status_code == 201, resp.text
    msg = resp.json()
    assert msg["body"] == "## Test message"
    assert msg["owner_type"] == "folder"
    assert msg["author_type"] == "user"
    mid = msg["message_id"]

    # edit message
    resp = client.patch(
        f"/api/file-mgmt/p3-7/messages/{mid}",
        json={"body": "## Edited message", "version": 1},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["body"] == "## Edited message"
    assert resp.json()["edited_at"] is not None

    # delete message
    resp = client.delete(f"/api/file-mgmt/p3-7/messages/{mid}")
    assert resp.status_code == 204

    # verify deleted
    resp = client.get(f"/api/file-mgmt/p3-7/folders/{folder_id}/messages")
    messages = resp.json()
    assert all(m["message_id"] != mid for m in messages)


# ── 8. System message protected ─────────────────────────────────


def test_system_message_protected():
    """Insert author_type=system message -> edit/delete returns 403."""
    from src.main import app

    _setup_collection("p3-8")
    client = TestClient(app)

    # Insert a system message directly into DB
    conn = get_db("p3-8")
    mid = uuid.uuid4().hex
    now = "2026-07-28T00:00:00+00:00"
    conn.execute(
        """INSERT INTO messages
           (message_id, owner_type, owner_id, source_node_id, body,
            author_type, author_id, created_at, edited_at, edited_by, version)
           VALUES (?, 'folder', 'test', NULL, 'system msg',
                   'system', 'system', ?, NULL, NULL, 1)""",
        (mid, now),
    )
    conn.commit()
    conn.close()

    # edit -> 403
    resp = client.patch(
        f"/api/file-mgmt/p3-8/messages/{mid}",
        json={"body": "hacked", "version": 1},
    )
    assert resp.status_code == 403, resp.text

    # delete -> 403
    resp = client.delete(f"/api/file-mgmt/p3-8/messages/{mid}")
    assert resp.status_code == 403, resp.text


# ── 9. Folder messages aggregation ──────────────────────────────


def test_folder_messages_aggregation():
    """Folder message + file message + node message -> list_folder_messages returns all sorted."""
    from src.main import app

    _setup_collection("p3-9")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-9")

    # create group (for node messages to aggregate)
    resp = client.post(
        "/api/file-mgmt/p3-9/groups",
        json={"name": "AggGroup"},
    )
    assert resp.status_code == 201
    group = resp.json()
    group_folder_id = group["folder_id"]

    # create node in this group on main chain
    node = _create_node(
        client, "p3-9", main_id, "agg-node", order=1, group_id=group["group_id"]
    )
    nid = node["node_id"]

    # create file record + add to group folder
    fid = uuid.uuid4().hex
    _create_file_record("p3-9", fid)
    resp = client.post(
        f"/api/file-mgmt/p3-9/files/{fid}/paths",
        json={"folder_id": group_folder_id},
    )
    assert resp.status_code == 201

    # 1. Add folder message
    resp = client.post(
        f"/api/file-mgmt/p3-9/folders/{group_folder_id}/messages",
        json={"body": "folder-msg", "owner_type": "folder", "owner_id": group_folder_id},
    )
    assert resp.status_code == 201

    # 2. Add file message
    resp = client.post(
        f"/api/file-mgmt/p3-9/files/{fid}/messages",
        json={"body": "file-msg", "owner_type": "file", "owner_id": fid},
    )
    assert resp.status_code == 201

    # 3. Add node message
    resp = client.post(
        f"/api/file-mgmt/p3-9/nodes/{nid}/messages",
        json={"body": "node-msg", "owner_type": "node", "owner_id": nid},
    )
    assert resp.status_code == 201

    # Query aggregated messages
    resp = client.get(f"/api/file-mgmt/p3-9/folders/{group_folder_id}/messages")
    assert resp.status_code == 200, resp.text
    messages = resp.json()
    assert len(messages) == 3, f"Expected 3 messages, got {len(messages)}"

    bodies = {m["body"] for m in messages}
    assert bodies == {"folder-msg", "file-msg", "node-msg"}

    # Verify chronological order
    timestamps = [m["created_at"] for m in messages]
    assert timestamps == sorted(timestamps, reverse=True), "Messages should be sorted by created_at DESC (newest first)"


# ── 10. is_greyed calculation ───────────────────────────────────


def test_is_greyed_calculation():
    """file-level archive greys all paths; path-level archive greys only that path."""
    from src.main import app

    _setup_collection("p3-10")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-10")

    # create group
    resp = client.post(
        "/api/file-mgmt/p3-10/groups",
        json={"name": "GreyGroup"},
    )
    assert resp.status_code == 201
    group = resp.json()

    # create plain folder
    resp = client.post(
        "/api/file-mgmt/p3-10/folders",
        json={"name": "PlainFolder"},
    )
    assert resp.status_code == 201
    plain_folder_id = resp.json()["folder_id"]

    # create node
    node = _create_node(
        client, "p3-10", main_id, "grey-node", order=1, group_id=group["group_id"]
    )
    nid = node["node_id"]

    # TEST A: files.archived=1 -> all paths is_greyed
    fid_archived = uuid.uuid4().hex
    _create_file_record("p3-10", fid_archived, archived=1)

    resp = client.post(
        f"/api/file-mgmt/p3-10/files/{fid_archived}/paths",
        json={"folder_id": plain_folder_id},
    )
    assert resp.status_code == 201

    resp = client.get(f"/api/file-mgmt/p3-10/files/{fid_archived}")
    detail = resp.json()
    for p in detail["paths"]:
        assert p["is_greyed"] is True, f"File-archived path should be greyed: {p}"

    # TEST B: path-level archive greys only that path (not attachment greyed)
    fid_normal = uuid.uuid4().hex
    _create_file_record("p3-10", fid_normal, archived=0)

    # Add persistent path
    resp = client.post(
        f"/api/file-mgmt/p3-10/files/{fid_normal}/paths",
        json={"folder_id": plain_folder_id},
    )
    assert resp.status_code == 201

    # Attach to node (derived paths)
    resp = client.post(
        f"/api/file-mgmt/p3-10/nodes/{nid}/files",
        json={"file_id": fid_normal},
    )
    assert resp.status_code == 201

    # Path-archive the plain folder mount only
    resp = client.patch(
        f"/api/file-mgmt/p3-10/files/{fid_normal}/archive",
        json={
            "archived": True,
            "version": 1,
            "scope": "path",
            "folder_id": plain_folder_id,
        },
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/api/file-mgmt/p3-10/files/{fid_normal}")
    detail = resp.json()
    for p in detail["paths"]:
        if p["folder_id"] == plain_folder_id and p.get("source_node_id") is None:
            assert p["is_greyed"] is True, "Path-archived mount should be greyed"
        elif p["source_node_id"] is not None:
            # derived paths not path-archived remain active unless file-level
            assert p["is_greyed"] is False or p.get("archived") is True


# ── 11. End-to-end ──────────────────────────────────────────────


def test_end_to_end():
    """Create group -> branch chain -> node -> attach file -> check folder files & messages -> detach -> verify."""
    from src.main import app

    _setup_collection("p3-11")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p3-11")

    # 1. create group
    resp = client.post(
        "/api/file-mgmt/p3-11/groups",
        json={"name": "E2EGroup"},
    )
    assert resp.status_code == 201
    group = resp.json()
    group_folder_id = group["folder_id"]

    # 2. create branch chain
    pnode = _create_node(client, "p3-11", main_id, "trigger", order=1)
    resp = client.post(
        "/api/file-mgmt/p3-11/chains",
        json={
            "parent_chain_id": main_id,
            "parent_node_id": pnode["node_id"],
            "title": "E2EBranch",
        },
    )
    assert resp.status_code == 201
    chain = resp.json()
    chain_folder_id = chain["folder_id"]

    # 3. create node on branch chain with group
    node = _create_node(
        client, "p3-11", chain["chain_id"], "e2e-node", order=1,
        group_id=group["group_id"]
    )
    nid = node["node_id"]

    # 4. create file + attach
    fid = uuid.uuid4().hex
    _create_file_record("p3-11", fid)

    resp = client.post(
        f"/api/file-mgmt/p3-11/nodes/{nid}/files",
        json={"file_id": fid},
    )
    assert resp.status_code == 201

    # 5. file should appear in group folder file list
    resp = client.get(f"/api/file-mgmt/p3-11/folders/{group_folder_id}/files")
    assert resp.status_code == 200
    files_in_group = resp.json()
    assert any(f["file_id"] == fid for f in files_in_group), (
        f"File {fid} should appear in group folder files"
    )

    # 6. file should appear in chain folder file list
    resp = client.get(f"/api/file-mgmt/p3-11/folders/{chain_folder_id}/files")
    assert resp.status_code == 200
    files_in_chain = resp.json()
    assert any(f["file_id"] == fid for f in files_in_chain), (
        f"File {fid} should appear in chain folder files"
    )

    # 7. add messages
    resp = client.post(
        f"/api/file-mgmt/p3-11/nodes/{nid}/messages",
        json={"body": "node-msg", "owner_type": "node", "owner_id": nid},
    )
    assert resp.status_code == 201

    resp = client.post(
        f"/api/file-mgmt/p3-11/files/{fid}/messages",
        json={"body": "file-msg", "owner_type": "file", "owner_id": fid},
    )
    assert resp.status_code == 201

    # 8. check folder messages aggregation
    resp = client.get(f"/api/file-mgmt/p3-11/folders/{group_folder_id}/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) >= 2

    # 9. node detail should show attachments
    resp = client.get(f"/api/file-mgmt/p3-11/nodes/{nid}")
    assert resp.status_code == 200
    node_detail = resp.json()
    assert len(node_detail["attachments"]) >= 1
    assert any(a["file_id"] == fid for a in node_detail["attachments"])

    # 10. detach
    resp = client.delete(f"/api/file-mgmt/p3-11/nodes/{nid}/files/{fid}")
    assert resp.status_code == 204

    # 11. file should disappear from group folder files (only had derived path)
    resp = client.get(f"/api/file-mgmt/p3-11/folders/{group_folder_id}/files")
    files_after = resp.json()
    assert not any(f["file_id"] == fid for f in files_after), (
        f"File {fid} should be gone from group folder after detach"
    )

    # 12. node detail should show no attachments
    resp = client.get(f"/api/file-mgmt/p3-11/nodes/{nid}")
    node_detail = resp.json()
    assert len(node_detail["attachments"]) == 0


# ── 12. list_files endpoint ─────────────────────────────────────


def test_list_files_endpoint():
    """GET /files returns file list; can filter by folder_id and archived."""
    from src.main import app

    _setup_collection("p3-12")
    client = TestClient(app)

    # create folder
    resp = client.post(
        "/api/file-mgmt/p3-12/folders",
        json={"name": "ListFolder"},
    )
    assert resp.status_code == 201
    folder_id = resp.json()["folder_id"]

    # create 2 files, 1 archived
    fid1 = uuid.uuid4().hex
    fid2 = uuid.uuid4().hex
    _create_file_record("p3-12", fid1, archived=0)
    _create_file_record("p3-12", fid2, archived=1)

    # add paths
    resp = client.post(
        f"/api/file-mgmt/p3-12/files/{fid1}/paths",
        json={"folder_id": folder_id},
    )
    assert resp.status_code == 201
    resp = client.post(
        f"/api/file-mgmt/p3-12/files/{fid2}/paths",
        json={"folder_id": folder_id},
    )
    assert resp.status_code == 201

    # list all files
    resp = client.get(f"/api/file-mgmt/p3-12/files?folder_id={folder_id}")
    assert resp.status_code == 200
    all_files = resp.json()
    assert len(all_files) >= 2

    # filter by folder
    resp = client.get(f"/api/file-mgmt/p3-12/files?folder_id={folder_id}")
    assert resp.status_code == 200
    folder_files = resp.json()
    assert len(folder_files) >= 2

    # filter archived=1
    resp = client.get("/api/file-mgmt/p3-12/files?archived=true")
    assert resp.status_code == 200
    archived_files = resp.json()
    assert all(f["archived"] is True for f in archived_files)


# ── 13. list_archived_files endpoint ────────────────────────────


def test_list_archived_files():
    """GET /archived returns all archived files."""
    from src.main import app

    _setup_collection("p3-13")
    client = TestClient(app)

    fid = uuid.uuid4().hex
    _create_file_record("p3-13", fid, archived=1)

    resp = client.get("/api/file-mgmt/p3-13/archived")
    assert resp.status_code == 200
    files = resp.json()
    assert any(f["file_id"] == fid for f in files)
