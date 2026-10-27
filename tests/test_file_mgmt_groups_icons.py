"""Group icons + delete keeps folder (service layer, no MCP)."""

from __future__ import annotations

import shutil

import pytest

from src.collections.store import create_collection_meta
from src.file_mgmt import service
from src.file_mgmt.models import GroupCreate, GroupUpdate
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


def test_create_group_with_icons():
    coll = _setup("grp-icon-create")
    g = service.create_group(
        coll,
        GroupCreate(
            name="Finance",
            icon_type="lucide",
            icon_value="briefcase",
            icon_color="#3DAF73",
        ),
    )
    assert g.name == "Finance"
    assert g.icon_type == "lucide"
    assert g.icon_value == "briefcase"
    assert g.icon_color == "#3DAF73"
    assert g.is_system is False
    assert g.folder_id


def test_update_group_icons():
    coll = _setup("grp-icon-update")
    g = service.create_group(coll, GroupCreate(name="Ops"))
    g2 = service.update_group(
        coll,
        g.group_id,
        GroupUpdate(icon_type="emoji", icon_value="🚀", icon_color=None),
    )
    assert g2.icon_type == "emoji"
    assert g2.icon_value == "🚀"


def test_delete_group_keeps_folder():
    coll = _setup("grp-icon-delete")
    g = service.create_group(coll, GroupCreate(name="Temp"))
    fid = g.folder_id
    assert fid
    service.delete_group(coll, g.group_id)
    fld = service.get_folder(coll, fid)
    assert fld.kind == "plain"
    ids = {x.group_id for x in service.list_groups(coll)}
    assert g.group_id not in ids


def test_list_groups_includes_system_flag():
    coll = _setup("grp-icon-list")
    groups = service.list_groups(coll)
    names = {g.name: g for g in groups}
    if "Meeting" in names:
        assert names["Meeting"].is_system is True
    if "Notes" in names:
        assert names["Notes"].is_system is True
