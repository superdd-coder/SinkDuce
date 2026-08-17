"""Duplicate-name upload must 409 with name_conflict (not 500)."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db


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


def test_duplicate_upload_returns_409_name_conflict():
    from src.collections.store import create_collection_meta
    from src.main import app

    coll = "nc-upload-dup"
    create_collection_meta(coll, "Name conflict upload")
    init_collection_db(coll)
    client = TestClient(app)

    folder = client.post(
        f"/api/file-mgmt/{coll}/folders",
        json={"name": "Inbox", "kind": "plain"},
    )
    assert folder.status_code == 201, folder.text
    folder_id = folder.json()["folder_id"]

    first = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("same.zip", b"PK\x03\x04fake", "application/zip")},
        data={"folder_id": folder_id},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("same.zip", b"PK\x03\x04other", "application/zip")},
        data={"folder_id": folder_id},
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "name_conflict"
    assert detail["resource"] == "file"
    assert detail["name"] == "same.zip"
    assert isinstance(detail.get("suggested_name"), str)
    assert detail["suggested_name"] != "same.zip"


def test_rename_file_to_existing_name_returns_409():
    from src.collections.store import create_collection_meta
    from src.main import app

    coll = "nc-rename-file"
    create_collection_meta(coll, "Name conflict rename file")
    init_collection_db(coll)
    client = TestClient(app)

    folder = client.post(
        f"/api/file-mgmt/{coll}/folders",
        json={"name": "Inbox", "kind": "plain"},
    )
    assert folder.status_code == 201, folder.text
    folder_id = folder.json()["folder_id"]

    a = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("alpha.txt", b"aaa", "text/plain")},
        data={"folder_id": folder_id},
    )
    b = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("beta.txt", b"bbb", "text/plain")},
        data={"folder_id": folder_id},
    )
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text
    b_id = b.json()["file_id"]
    b_ver = b.json()["version"]

    renamed = client.patch(
        f"/api/file-mgmt/{coll}/files/{b_id}",
        json={"filename": "alpha.txt", "version": b_ver},
    )
    assert renamed.status_code == 409, renamed.text
    detail = renamed.json()["detail"]
    assert detail["code"] == "name_conflict"
    assert detail["name"] == "alpha.txt"
    assert detail["suggested_name"] != "alpha.txt"


def test_rename_folder_to_existing_name_returns_409():
    from src.collections.store import create_collection_meta
    from src.main import app

    coll = "nc-rename-folder"
    create_collection_meta(coll, "Name conflict rename folder")
    init_collection_db(coll)
    client = TestClient(app)

    a = client.post(
        f"/api/file-mgmt/{coll}/folders",
        json={"name": "Alpha", "kind": "plain"},
    )
    b = client.post(
        f"/api/file-mgmt/{coll}/folders",
        json={"name": "Beta", "kind": "plain"},
    )
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text
    b_id = b.json()["folder_id"]
    b_ver = b.json()["version"]

    renamed = client.patch(
        f"/api/file-mgmt/{coll}/folders/{b_id}",
        json={"name": "Alpha", "version": b_ver},
    )
    assert renamed.status_code == 409, renamed.text
    detail = renamed.json()["detail"]
    assert detail["code"] == "name_conflict"
    assert detail["name"] == "Alpha"
    assert detail["suggested_name"] != "Alpha"


def test_rename_root_orphan_to_existing_name_returns_409():
    """Collection-root files have no file_paths row; rename must still 409."""
    from src.collections.store import create_collection_meta
    from src.main import app

    coll = "nc-rename-orphan"
    create_collection_meta(coll, "Name conflict root orphan")
    init_collection_db(coll)
    client = TestClient(app)

    a = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("测试文件.docx", b"aaa", "application/octet-stream")},
    )
    b = client.post(
        f"/api/file-mgmt/{coll}/files/upload",
        files={"file": ("other.docx", b"bbb", "application/octet-stream")},
    )
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text
    b_id = b.json()["file_id"]
    b_ver = b.json()["version"]

    renamed = client.patch(
        f"/api/file-mgmt/{coll}/files/{b_id}",
        json={"filename": "测试文件.docx", "version": b_ver},
    )
    assert renamed.status_code == 409, renamed.text
    detail = renamed.json()["detail"]
    assert detail["code"] == "name_conflict"
    assert detail["name"] == "测试文件.docx"
