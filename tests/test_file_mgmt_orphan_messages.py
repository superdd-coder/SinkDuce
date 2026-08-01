"""Regression: folder messages must not outlive their owner folder."""

from __future__ import annotations

import shutil

import pytest
from fastapi import HTTPException

from src.collections.store import create_collection_meta
from src.file_mgmt import service
from src.file_mgmt.models import GroupCreate, MessageCreate
from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db


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


def _setup(coll_id: str) -> str:
    create_collection_meta(coll_id, f"Test {coll_id}")
    init_collection_db(coll_id)
    return coll_id


def test_delete_user_group_folder_removes_messages():
    """Deleting a user_group folder cleans its messages (no Nested orphans)."""
    coll = _setup("orphan-ug-delete")
    g = service.create_group(coll, GroupCreate(name="Doomed"))
    fid = g.folder_id
    msg = service.create_message(
        coll,
        MessageCreate(
            owner_type="folder",
            owner_id=fid,
            body="will-be-orphaned-if-bug",
            author_type="user",
        ),
    )
    assert msg.source_name == "Doomed"

    service.delete_folder(coll, fid)

    with pytest.raises(HTTPException) as ei:
        service.get_folder(coll, fid)
    assert ei.value.status_code == 404

    msgs = service.list_root_messages(
        coll, include_node_msgs=False, include_file_msgs=False, recursive=True
    )
    bodies = [m.body for m in msgs]
    assert "will-be-orphaned-if-bug" not in bodies


def test_purge_heals_preexisting_orphan_folder_messages():
    """list_root_messages purges leftover orphans from older delete bugs."""
    coll = _setup("orphan-ug-heal")
    g = service.create_group(coll, GroupCreate(name="Ghost"))
    fid = g.folder_id
    service.create_message(
        coll,
        MessageCreate(
            owner_type="folder",
            owner_id=fid,
            body="ghost-msg",
            author_type="user",
        ),
    )
    # Simulate the old bug: delete folder row without cleaning messages
    conn = service._open_db(coll)
    try:
        with conn:
            conn.execute(
                "UPDATE nodes SET group_id=NULL WHERE group_id IN "
                "(SELECT group_id FROM node_groups WHERE folder_id=?)",
                (fid,),
            )
            conn.execute("DELETE FROM node_groups WHERE folder_id=?", (fid,))
            # Leave messages behind intentionally
            conn.execute("DELETE FROM folders WHERE folder_id=?", (fid,))
    finally:
        conn.close()

    # Precondition: raw orphan exists
    conn = service._open_db(coll)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE body=?", ("ghost-msg",)
        ).fetchone()["c"]
        assert n == 1
    finally:
        conn.close()

    msgs = service.list_root_messages(coll, recursive=True)
    assert all(m.body != "ghost-msg" for m in msgs)

    conn = service._open_db(coll)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE body=?", ("ghost-msg",)
        ).fetchone()["c"]
        assert n == 0
    finally:
        conn.close()


def test_folder_message_source_name_resolved():
    coll = _setup("orphan-src-name")
    g = service.create_group(coll, GroupCreate(name="NamedFolder"))
    msg = service.create_message(
        coll,
        MessageCreate(
            owner_type="folder",
            owner_id=g.folder_id,
            body="hi",
            author_type="user",
        ),
    )
    assert msg.source_name == "NamedFolder"
    listed = service.list_folder_messages(coll, g.folder_id, include_node_msgs=False, include_file_msgs=False)
    assert listed and listed[0].source_name == "NamedFolder"
