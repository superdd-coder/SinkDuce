"""Meeting ↔ file-mgmt ↔ timeline bridge (design 2026-08-04).

Covers:
- nodes.external_ref get-or-create anchor
- register under Meeting folder
- empty-anchor delete
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.file_mgmt.store import COLLECTIONS_DIR, get_db, init_collection_db
from src.file_mgmt.service import (
    delete_meeting_anchor_if_empty,
    ensure_meeting_anchor_node,
    get_node_by_external_ref,
    meeting_external_ref,
    register_ingested_source_file,
    attach_file_to_node,
    detach_file_from_node,
)


@pytest.fixture(autouse=True)
def _cleanup():
    existing: set[str] = set()
    if COLLECTIONS_DIR.exists():
        existing = {d.name for d in COLLECTIONS_DIR.iterdir() if d.is_dir()}
    yield
    if COLLECTIONS_DIR.exists():
        for d in COLLECTIONS_DIR.iterdir():
            if d.is_dir() and d.name not in existing:
                shutil.rmtree(d, ignore_errors=True)


def _setup(coll: str) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll, f"Test {coll}")
    init_collection_db(coll)


def test_meeting_external_ref_format():
    assert meeting_external_ref("abc") == "meeting:abc"


def test_ensure_meeting_anchor_idempotent():
    coll = "bridge-anchor-1"
    _setup(coll)
    mid = "meet_aaa"
    n1 = ensure_meeting_anchor_node(coll, mid, title="Kickoff", event_time="2026-08-01")
    n2 = ensure_meeting_anchor_node(coll, mid, title="Kickoff Renamed")
    assert n1 == n2

    looked = get_node_by_external_ref(coll, meeting_external_ref(mid))
    assert looked is not None
    assert looked.node_id == n1
    assert looked.title == "Kickoff Renamed"

    conn = get_db(coll)
    try:
        row = conn.execute(
            "SELECT external_ref, title, group_id FROM nodes WHERE node_id=?",
            (n1,),
        ).fetchone()
        assert row["external_ref"] == "meeting:meet_aaa"
        assert row["title"] == "Kickoff Renamed"
        # Bound to Meeting system group when present
        grp = conn.execute(
            "SELECT name FROM node_groups WHERE group_id=?", (row["group_id"],)
        ).fetchone()
        assert grp is None or grp["name"] == "Meeting"
    finally:
        conn.close()


def test_meeting_anchor_one_node_per_chain():
    """Same meeting may have separate anchors on main vs branch chain."""
    from src.file_mgmt.service import create_chain, list_chains
    from src.file_mgmt.models import ChainCreate

    coll = "bridge-anchor-multi"
    _setup(coll)
    mid = "meet_multi"
    chains = list_chains(coll)
    main = next(c for c in chains if c.is_main)
    # create a branch chain off first main node or via API-like helper
    # need a parent node on main for branch — use ensure meeting on main first
    n_main = ensure_meeting_anchor_node(
        coll, mid, title="Multi", chain_id=main.chain_id
    )
    branch = create_chain(
        coll,
        ChainCreate(
            parent_chain_id=main.chain_id,
            parent_node_id=n_main,
            title="Branch A",
        ),
    )
    n_branch = ensure_meeting_anchor_node(
        coll, mid, title="Multi", chain_id=branch.chain_id
    )
    assert n_main != n_branch
    assert get_node_by_external_ref(
        coll, meeting_external_ref(mid), chain_id=main.chain_id
    ).node_id == n_main
    assert get_node_by_external_ref(
        coll, meeting_external_ref(mid), chain_id=branch.chain_id
    ).node_id == n_branch
    # same chain still idempotent
    assert (
        ensure_meeting_anchor_node(coll, mid, title="Multi", chain_id=branch.chain_id)
        == n_branch
    )


def test_register_meeting_folder_and_empty_anchor_delete():
    coll = "bridge-reg-1"
    _setup(coll)
    mid = "meet_bbb"
    fid = "file_bbb_001"
    source = f"__meeting__:{mid}:tab_01"

    # Seed files.json so unregister can find file_id
    from src.collections.file_index import add as index_add, load as index_load

    (COLLECTIONS_DIR / coll / "files" / fid).mkdir(parents=True, exist_ok=True)
    (COLLECTIONS_DIR / coll / "files" / fid / "tab_01.md").write_text(
        "# sec\n\nhello", encoding="utf-8"
    )
    index_add(coll, fid, source, "Meet / sec", "meeting", 0)

    register_ingested_source_file(
        coll,
        file_id=fid,
        source=source,
        storage_name="tab_01.md",
        system_folder_name="Meeting",
    )

    conn = get_db(coll)
    try:
        frow = conn.execute(
            "SELECT file_id FROM files WHERE file_id=?", (fid,)
        ).fetchone()
        assert frow is not None
        meeting_folder = conn.execute(
            "SELECT folder_id FROM folders WHERE name='Meeting' AND is_system=1"
        ).fetchone()
        assert meeting_folder
        path = conn.execute(
            "SELECT 1 FROM file_paths WHERE file_id=? AND folder_id=?",
            (fid, meeting_folder["folder_id"]),
        ).fetchone()
        assert path is not None
    finally:
        conn.close()

    node_id = ensure_meeting_anchor_node(coll, mid, title="Board")
    attach_file_to_node(coll, node_id, file_id=fid)

    # Still has attachment → do not delete
    assert delete_meeting_anchor_if_empty(coll, mid) is False

    detach_file_from_node(coll, node_id, fid)
    assert delete_meeting_anchor_if_empty(coll, mid) is True

    conn = get_db(coll)
    try:
        gone = conn.execute(
            "SELECT 1 FROM nodes WHERE external_ref=?",
            (meeting_external_ref(mid),),
        ).fetchone()
        assert gone is None
    finally:
        conn.close()

    # index still has entry until unregister
    assert fid in (index_load(coll) or {})
