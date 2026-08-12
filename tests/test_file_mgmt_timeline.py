"""Regression: build_timeline / get_timeline must not NameError on collection_id.

Root cause (fixed): ``_node_summary_row`` used ``collection_id`` without taking it
as a parameter when resolving attachment display names.
"""

from __future__ import annotations

import shutil
import uuid

import pytest

from src.file_mgmt.store import COLLECTIONS_DIR, get_db, init_collection_db


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


def _setup_collection(coll_id: str) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll_id, f"Test {coll_id}")
    init_collection_db(coll_id)


def _seed_node_with_attachment(coll_id: str) -> tuple[str, str]:
    """Insert main-chain node + file attachment. Returns (node_id, file_id)."""
    now = "2026-08-12T00:00:00+00:00"
    conn = get_db(coll_id)
    try:
        main = conn.execute(
            "SELECT chain_id FROM chains WHERE parent_chain_id IS NULL LIMIT 1"
        ).fetchone()
        assert main is not None, "seed should create main chain"
        chain_id = main["chain_id"]

        group = conn.execute(
            "SELECT group_id FROM node_groups LIMIT 1"
        ).fetchone()
        group_id = group["group_id"] if group else None

        node_id = uuid.uuid4().hex
        file_id = uuid.uuid4().hex
        version_id = uuid.uuid4().hex

        # Circular FK: file_versions.file_id → files; files.current_version_id → file_versions
        conn.execute("PRAGMA defer_foreign_keys=ON")
        conn.execute(
            """INSERT INTO nodes
               (node_id, chain_id, group_id, node_type, title, "order",
                external_ref, created_by, created_at, version)
               VALUES (?, ?, ?, 'event', 'Kickoff', 1, NULL, 'local', ?, 1)""",
            (node_id, chain_id, group_id, now),
        )
        conn.execute(
            """INSERT INTO file_versions
               (version_id, file_id, version_no, storage_file_id,
                archived, commit_message, created_by, created_at)
               VALUES (?, ?, 1, ?, 0, 'initial', 'local', ?)""",
            (version_id, file_id, "agenda.pdf", now),
        )
        conn.execute(
            """INSERT INTO files
               (file_id, current_version_id, is_definitive, archived,
                unsupported, created_by, version)
               VALUES (?, ?, 0, 0, 0, 'local', 1)""",
            (file_id, version_id),
        )
        conn.execute(
            """INSERT INTO file_nodes
               (file_id, node_id, version_id, greyed, added_by)
               VALUES (?, ?, ?, 0, 'local')""",
            (file_id, node_id, version_id),
        )
        conn.commit()
        return node_id, file_id
    finally:
        conn.close()


def test_build_timeline_with_attachments_no_nameerror():
    """summary depth loads attachments and must pass collection_id through."""
    from src.file_mgmt.service import build_timeline

    coll = "tl-attach-1"
    _setup_collection(coll)
    node_id, file_id = _seed_node_with_attachment(coll)

    data = build_timeline(coll, depth="summary")

    assert data["timeline"] is not None
    nodes = data["timeline"]["nodes"]
    assert len(nodes) >= 1
    kick = next(n for n in nodes if n["node_id"] == node_id)
    assert kick["attachment_count"] == 1
    assert isinstance(kick.get("attachments"), list)
    assert kick["attachments"][0]["file_id"] == file_id
    assert kick["attachments"][0]["filename"] == "agenda.pdf"


def test_build_timeline_minimal_depth_skips_attachments():
    from src.file_mgmt.service import build_timeline

    coll = "tl-attach-2"
    _setup_collection(coll)
    node_id, _ = _seed_node_with_attachment(coll)

    data = build_timeline(coll, depth="minimal")
    kick = next(n for n in data["timeline"]["nodes"] if n["node_id"] == node_id)
    assert "attachments" not in kick
    assert kick["attachment_count"] == 1


def test_mcp_get_timeline_real_service_no_error():
    """MCP tool path hits real build_timeline (only collection gate mocked)."""
    import asyncio
    from unittest.mock import patch

    from src.mcp.tools import file_mgmt as mod

    coll = "tl-attach-3"
    _setup_collection(coll)
    _seed_node_with_attachment(coll)

    with patch.object(mod, "_require_fm_collection", return_value=None):
        out = asyncio.get_event_loop().run_until_complete(mod.get_timeline(coll))
    sc = getattr(out, "structured_content", None) or out
    assert "error" not in sc, sc
    assert sc.get("timeline") is not None
    assert sc["summary"]["node_count"] >= 1
