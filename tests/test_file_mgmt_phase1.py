"""Phase 1 tests: SQLite infrastructure + collection lifecycle integration.

Run: pytest tests/test_file_mgmt_phase1.py -v
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.file_mgmt.store import (
    COLLECTIONS_DIR,
    EXPECTED_INDEXES,
    EXPECTED_TABLES,
    delete_collection_db,
    get_db,
    init_collection_db,
)


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


def _db_path(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "meta.db"


# ── 1. Tables ────────────────────────────────────────────────────

def test_init_collection_db_creates_tables():
    init_collection_db("test-fm-1")
    assert _db_path("test-fm-1").exists()

    conn = sqlite3.connect(str(_db_path("test-fm-1")))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    tables = {r[0] for r in rows}
    conn.close()

    assert EXPECTED_TABLES <= tables, f"Missing tables: {EXPECTED_TABLES - tables}"

    # indexes
    conn = sqlite3.connect(str(_db_path("test-fm-1")))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    indexes = {r[0] for r in rows}
    conn.close()

    assert EXPECTED_INDEXES <= indexes, f"Missing indexes: {EXPECTED_INDEXES - indexes}"


# ── 2. System data ───────────────────────────────────────────────

def test_init_collection_db_seeds_system_data():
    init_collection_db("test-fm-2")

    conn = get_db("test-fm-2")
    # 3 system folders
    folders = conn.execute(
        "SELECT name FROM folders WHERE kind='system_group' AND is_system=1"
    ).fetchall()
    folder_names = {r["name"] for r in folders}
    assert folder_names == {"Meeting", "Notes", "Archived"}

    # 2 system groups (Meeting, Notes — not Archived)
    groups = conn.execute(
        "SELECT name, folder_id FROM node_groups"
    ).fetchall()
    group_names = {r["name"] for r in groups}
    assert group_names == {"Meeting", "Notes"}
    # each group's folder_id points to an existing system folder
    for g in groups:
        row = conn.execute(
            "SELECT name FROM folders WHERE folder_id=?", (g["folder_id"],)
        ).fetchone()
        assert row is not None
        assert row["name"] == g["name"]

    # 1 main chain (parent_chain_id IS NULL)
    chains = conn.execute(
        "SELECT chain_id, parent_chain_id, parent_node_id, folder_id, title FROM chains"
    ).fetchall()
    assert len(chains) == 1
    assert chains[0]["parent_chain_id"] is None
    assert chains[0]["parent_node_id"] is None
    assert chains[0]["folder_id"] is None
    assert chains[0]["title"] is None
    conn.close()


# ── 3. get_db returns WAL connection ─────────────────────────────

def test_get_db_returns_wal_connection():
    init_collection_db("test-fm-3")

    conn = get_db("test-fm-3")
    assert conn.row_factory is sqlite3.Row

    mode = conn.execute("PRAGMA journal_mode").fetchone()
    assert mode[0] == "wal"

    fk = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk[0] == 1
    conn.close()


# ── 4. get_db raises if not initialized ──────────────────────────

def test_get_db_raises_if_not_initialized():
    with pytest.raises(FileNotFoundError):
        get_db("test-fm-nonexistent")


# ── 5. delete_collection_db removes file ─────────────────────────

def test_delete_collection_db_removes_file():
    init_collection_db("test-fm-5")
    assert _db_path("test-fm-5").exists()

    delete_collection_db("test-fm-5")

    assert not _db_path("test-fm-5").exists()
    assert not _db_path("test-fm-5").with_suffix(".db-wal").exists()
    assert not _db_path("test-fm-5").with_suffix(".db-shm").exists()


# ── 6. Collection create initializes DB (integration) ────────────

def test_collection_create_initializes_db():
    from src.api.routes.collections import create_collection
    from src.api.schemas import CollectionCreateRequest
    from src.services import services

    mock_embedding = MagicMock()
    mock_embedding.dimensions = 512
    mock_db = MagicMock()

    with patch.object(services, "embedding", mock_embedding), \
         patch.object(services, "db", mock_db):
        req = CollectionCreateRequest(name="test-fm-create")
        result = create_collection(req)

    assert "error" not in result, f"Create failed: {result}"
    coll_id = result["id"]
    assert _db_path(coll_id).exists()

    # verify tables + system data
    conn = get_db(coll_id)
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert EXPECTED_TABLES <= tables
    folders = conn.execute(
        "SELECT name FROM folders WHERE is_system=1"
    ).fetchall()
    assert len(folders) == 3
    conn.close()


# ── 7. Collection delete removes DB (integration) ────────────────

def test_collection_delete_removes_db():
    from src.api.routes.collections import create_collection, delete_collection
    from src.api.schemas import CollectionCreateRequest
    from src.services import services

    mock_embedding = MagicMock()
    mock_embedding.dimensions = 512
    mock_db = MagicMock()
    mock_db.collection_exists.return_value = True
    mock_db.list_collections.return_value = ["col_a", "col_b"]

    with patch.object(services, "embedding", mock_embedding), \
         patch.object(services, "db", mock_db):
        req = CollectionCreateRequest(name="test-fm-delete")
        result = create_collection(req)
        coll_id = result["id"]
        assert _db_path(coll_id).exists()

        delete_collection(coll_id)

    assert not _db_path(coll_id).exists()
    assert not _db_path(coll_id).with_suffix(".db-wal").exists()
    assert not _db_path(coll_id).with_suffix(".db-shm").exists()
    # The entire directory is removed by delete_collection_meta (rmtree)
    assert not (COLLECTIONS_DIR / coll_id).exists()
