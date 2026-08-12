"""Phase 2 tests: metadata CRUD API (folder / group / chain / node).

Run: pytest tests/test_file_mgmt_phase2.py -v --tb=short
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db


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


# ── 1. Folder CRUD ───────────────────────────────────────────────


def test_folder_crud():
    """Create plain folder -> query -> rename -> move -> delete."""
    from src.main import app

    _setup_collection("p2-1")
    client = TestClient(app)

    # create
    resp = client.post(
        "/api/file-mgmt/p2-1/folders",
        json={"name": "Projects", "parent_folder_id": None},
    )
    assert resp.status_code == 201, resp.text
    folder = resp.json()
    fid = folder["folder_id"]
    assert folder["name"] == "Projects"
    assert folder["kind"] == "plain"
    assert folder["is_system"] is False

    # query via tree
    resp = client.get("/api/file-mgmt/p2-1/folders")
    assert resp.status_code == 200
    tree = resp.json()
    names = {n["name"] for n in tree}
    assert "Projects" in names

    # query single
    resp = client.get(f"/api/file-mgmt/p2-1/folders/{fid}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Projects"

    # rename
    resp = client.patch(
        f"/api/file-mgmt/p2-1/folders/{fid}",
        json={"name": "Renamed", "version": 1},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed"
    assert resp.json()["version"] == 2

    # create a sub-folder, then move the first one under it
    resp = client.post(
        "/api/file-mgmt/p2-1/folders",
        json={"name": "Sub", "parent_folder_id": None},
    )
    sub_id = resp.json()["folder_id"]
    resp = client.patch(
        f"/api/file-mgmt/p2-1/folders/{fid}",
        json={"parent_folder_id": sub_id, "version": 2},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_folder_id"] == sub_id

    # delete
    resp = client.delete(f"/api/file-mgmt/p2-1/folders/{sub_id}")
    assert resp.status_code == 204
    resp = client.get(f"/api/file-mgmt/p2-1/folders/{fid}")
    assert resp.status_code == 404


# ── 2. System folder protection ─────────────────────────────────


def test_system_folder_protected():
    """Meeting/Notes/Archived cannot be renamed, moved, or deleted (403)."""
    from src.main import app

    _setup_collection("p2-2")
    client = TestClient(app)

    resp = client.get("/api/file-mgmt/p2-2/folders")
    tree = resp.json()
    sys_folders = [f for f in tree if f["is_system"]]
    assert len(sys_folders) == 3

    for f in sys_folders:
        fid = f["folder_id"]
        # rename -> 403
        resp = client.patch(
            f"/api/file-mgmt/p2-2/folders/{fid}",
            json={"name": "hacked", "version": 1},
        )
        assert resp.status_code == 403, f"rename {f['name']} should be 403"
        # move -> 403
        resp = client.patch(
            f"/api/file-mgmt/p2-2/folders/{fid}",
            json={"parent_folder_id": None, "version": 1},
        )
        assert resp.status_code == 403, f"move {f['name']} should be 403"
        # delete -> 403
        resp = client.delete(f"/api/file-mgmt/p2-2/folders/{fid}")
        assert resp.status_code == 403, f"delete {f['name']} should be 403"


# ── 3. Group folder flat constraint ──────────────────────────────


def test_group_folder_flat():
    """Creating a sub-folder under a user_group returns 400."""
    from src.main import app

    _setup_collection("p2-3")
    client = TestClient(app)

    # create a group (auto-creates a user_group folder)
    resp = client.post(
        "/api/file-mgmt/p2-3/groups",
        json={"name": "Financial"},
    )
    assert resp.status_code == 201, resp.text
    group = resp.json()
    folder_id = group["folder_id"]

    # try to create a sub-folder under the group folder
    resp = client.post(
        "/api/file-mgmt/p2-3/folders",
        json={"name": "Sub", "parent_folder_id": folder_id},
    )
    assert resp.status_code == 400, resp.text


# ── 4. Group create with bind_existing ───────────────────────────


def test_group_create_bind_existing():
    """Create plain folder -> create group binding it -> kind becomes user_group."""
    from src.main import app

    _setup_collection("p2-4")
    client = TestClient(app)

    # create plain folder
    resp = client.post(
        "/api/file-mgmt/p2-4/folders",
        json={"name": "Legal", "parent_folder_id": None},
    )
    assert resp.status_code == 201
    folder_id = resp.json()["folder_id"]

    # create group binding the folder
    resp = client.post(
        "/api/file-mgmt/p2-4/groups",
        json={"name": "Legal", "bind_existing_folder_id": folder_id},
    )
    assert resp.status_code == 201, resp.text
    group = resp.json()
    assert group["folder_id"] == folder_id

    # verify folder kind changed to user_group
    resp = client.get(f"/api/file-mgmt/p2-4/folders/{folder_id}")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "user_group"


# ── 5. Group create new (no bind) ────────────────────────────────


def test_group_create_new():
    """Create group without binding -> auto-creates folder."""
    from src.main import app

    _setup_collection("p2-5")
    client = TestClient(app)

    resp = client.post(
        "/api/file-mgmt/p2-5/groups",
        json={"name": "Financial", "description": "Financial docs"},
    )
    assert resp.status_code == 201, resp.text
    group = resp.json()
    assert group["name"] == "Financial"
    assert group["folder_id"] is not None

    # verify the auto-created folder
    resp = client.get(f"/api/file-mgmt/p2-5/folders/{group['folder_id']}")
    assert resp.status_code == 200
    fld = resp.json()
    assert fld["kind"] == "user_group"
    assert fld["name"] == "Financial"


# ── 6. Group delete ──────────────────────────────────────────────


def test_group_delete():
    """Delete group -> nodes unassigned, folder kept as plain."""
    from src.main import app

    _setup_collection("p2-6")
    client = TestClient(app)

    resp = client.post(
        "/api/file-mgmt/p2-6/groups",
        json={"name": "ToDelete", "icon_type": "lucide", "icon_value": "star", "icon_color": "#3DAF73"},
    )
    assert resp.status_code == 201
    group = resp.json()
    folder_id = group["folder_id"]
    assert group.get("icon_value") == "star"

    resp = client.delete(f"/api/file-mgmt/p2-6/groups/{group['group_id']}")
    assert resp.status_code == 204

    # folder kept, demoted to plain
    resp = client.get(f"/api/file-mgmt/p2-6/folders/{folder_id}")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "plain"


# ── 7. Chain create ──────────────────────────────────────────────


def test_chain_create():
    """Create a branch chain -> folder kind = branch."""
    from src.main import app

    _setup_collection("p2-7")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p2-7")

    # need a node on the main chain to branch from
    node = _create_node(client, "p2-7", main_id, "kickoff", order=1)

    # create branch chain
    resp = client.post(
        "/api/file-mgmt/p2-7/chains",
        json={
            "parent_chain_id": main_id,
            "parent_node_id": node["node_id"],
            "title": "DD Analysis",
        },
    )
    assert resp.status_code == 201, resp.text
    chain = resp.json()
    assert chain["title"] == "DD Analysis"
    assert chain["is_main"] is False
    assert chain["folder_id"] is not None

    # verify folder kind = branch
    resp = client.get(f"/api/file-mgmt/p2-7/folders/{chain['folder_id']}")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "branch"


# ── 8. Main chain protection ─────────────────────────────────────


def test_main_chain_protected():
    """Main chain cannot be deleted or renamed."""
    from src.main import app

    _setup_collection("p2-8")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p2-8")

    # delete -> 403
    resp = client.delete(f"/api/file-mgmt/p2-8/chains/{main_id}")
    assert resp.status_code == 403

    # rename -> 403
    resp = client.patch(
        f"/api/file-mgmt/p2-8/chains/{main_id}",
        json={"title": "hacked"},
    )
    assert resp.status_code == 403


def test_branch_start_anchor_cannot_move_off_main():
    """Moving a branch parent_node onto the branch detaches timeline layout.

    Guards the case where Section Summary ingest still lists the branch folder
    but the timeline drops it (parent not on main).
    """
    from src.main import app

    _setup_collection("p2-8b")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p2-8b")
    parent = _create_node(client, "p2-8b", main_id, "fork-here", order=1)

    # mark as start + create branch
    resp = client.patch(
        f"/api/file-mgmt/p2-8b/nodes/{parent['node_id']}",
        json={"node_type": "start", "version": parent["version"]},
    )
    assert resp.status_code == 200, resp.text
    parent = resp.json()

    resp = client.post(
        "/api/file-mgmt/p2-8b/chains",
        json={
            "parent_chain_id": main_id,
            "parent_node_id": parent["node_id"],
            "title": "Side Branch",
        },
    )
    assert resp.status_code == 201, resp.text
    branch_id = resp.json()["chain_id"]

    # create an event on the branch so it is a real chain
    _create_node(client, "p2-8b", branch_id, "work", order=1)

    # refuse to move the start anchor onto the branch
    resp = client.patch(
        f"/api/file-mgmt/p2-8b/nodes/{parent['node_id']}",
        json={"chain_id": branch_id, "version": parent["version"]},
    )
    assert resp.status_code == 400, resp.text
    assert "main chain" in resp.json()["detail"].lower()

    # parent still on main
    resp = client.get(f"/api/file-mgmt/p2-8b/nodes/{parent['node_id']}")
    assert resp.status_code == 200
    assert resp.json()["chain_id"] == main_id


# ── 9. Node CRUD ─────────────────────────────────────────────────


def test_node_crud():
    """Create node on main chain -> query -> rename -> delete."""
    from src.main import app

    _setup_collection("p2-9")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p2-9")

    # create
    node = _create_node(client, "p2-9", main_id, "Initial", order=1)
    nid = node["node_id"]
    assert node["title"] == "Initial"
    assert node["order"] == 1

    # list
    resp = client.get(f"/api/file-mgmt/p2-9/chains/{main_id}/nodes")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # get detail
    resp = client.get(f"/api/file-mgmt/p2-9/nodes/{nid}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Initial"

    # rename
    resp = client.patch(
        f"/api/file-mgmt/p2-9/nodes/{nid}",
        json={"title": "Updated", "version": 1},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Updated"

    # delete
    resp = client.delete(f"/api/file-mgmt/p2-9/nodes/{nid}")
    assert resp.status_code == 204

    resp = client.get(f"/api/file-mgmt/p2-9/nodes/{nid}")
    assert resp.status_code == 404


# ── 10. Node reorder ─────────────────────────────────────────────


def test_node_reorder():
    """Create 3 nodes -> move 3rd to position 1 -> verify order."""
    from src.main import app

    _setup_collection("p2-10")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p2-10")

    n1 = _create_node(client, "p2-10", main_id, "A", order=1)
    n2 = _create_node(client, "p2-10", main_id, "B", order=2)
    n3 = _create_node(client, "p2-10", main_id, "C", order=3)

    # move n3 to position 1
    resp = client.post(
        f"/api/file-mgmt/p2-10/nodes/{n3['node_id']}/reorder",
        json={"new_order": 1},
    )
    assert resp.status_code == 200, resp.text
    nodes = resp.json()
    assert len(nodes) == 3
    orders = [n["order"] for n in nodes]
    assert orders == [1, 2, 3]  # always contiguous 1..N
    titles = [n["title"] for n in nodes]
    assert titles == ["C", "A", "B"]  # C moved to front


# ── 11. Optimistic lock ──────────────────────────────────────────


def test_optimistic_lock():
    """Concurrent update same folder -> second returns 409."""
    from src.main import app

    _setup_collection("p2-11")
    client = TestClient(app)

    # create folder (version 1)
    resp = client.post(
        "/api/file-mgmt/p2-11/folders",
        json={"name": "Lock", "parent_folder_id": None},
    )
    fid = resp.json()["folder_id"]

    # first update (version 1 -> 2)
    resp = client.patch(
        f"/api/file-mgmt/p2-11/folders/{fid}",
        json={"name": "First", "version": 1},
    )
    assert resp.status_code == 200

    # second update with stale version 1 -> 409
    resp = client.patch(
        f"/api/file-mgmt/p2-11/folders/{fid}",
        json={"name": "Second", "version": 1},
    )
    assert resp.status_code == 409


# ── 12. End-to-end ───────────────────────────────────────────────


def test_end_to_end():
    """Create group -> create branch chain -> create node -> change group -> delete."""
    from src.main import app

    _setup_collection("p2-12")
    client = TestClient(app)

    main_id = _get_main_chain_id(client, "p2-12")

    # 1. create a group
    resp = client.post(
        "/api/file-mgmt/p2-12/groups",
        json={"name": "Commercial"},
    )
    assert resp.status_code == 201
    group = resp.json()
    gid = group["group_id"]

    # 2. create a node on main chain to branch from
    parent_node = _create_node(client, "p2-12", main_id, "trigger", order=1)

    # 3. create branch chain
    resp = client.post(
        "/api/file-mgmt/p2-12/chains",
        json={
            "parent_chain_id": main_id,
            "parent_node_id": parent_node["node_id"],
            "title": "Negotiation",
        },
    )
    assert resp.status_code == 201
    chain = resp.json()
    chain_id = chain["chain_id"]

    # 4. create node on the branch chain with the group
    node = _create_node(
        client, "p2-12", chain_id, "kickoff", order=1, group_id=gid
    )
    assert node["group_id"] == gid

    # 5. create a second group and change the node's group
    resp = client.post(
        "/api/file-mgmt/p2-12/groups",
        json={"name": "Legal"},
    )
    assert resp.status_code == 201
    gid2 = resp.json()["group_id"]

    resp = client.patch(
        f"/api/file-mgmt/p2-12/nodes/{node['node_id']}",
        json={"group_id": gid2, "version": 1},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["group_id"] == gid2

    # 6. delete the last node on the branch — empty branch is auto-removed
    resp = client.delete(f"/api/file-mgmt/p2-12/nodes/{node['node_id']}")
    assert resp.status_code == 204

    # 7. chain already gone (delete last node cascades empty branch)
    resp = client.get("/api/file-mgmt/p2-12/chains")
    assert resp.status_code == 200
    chains = resp.json()
    assert all(c["chain_id"] != chain_id for c in chains)
    # idempotent delete of missing chain → 404 is expected if called
    resp = client.delete(f"/api/file-mgmt/p2-12/chains/{chain_id}")
    assert resp.status_code == 404
