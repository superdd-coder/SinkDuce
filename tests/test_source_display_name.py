"""Display names for multi-version / renamed files (Recall + list_files)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def col_id(tmp_path, monkeypatch):
    """Isolated collection under tmp data dirs for file_index + SQLite."""
    cid = "disp-name-col"
    coll_dir = tmp_path / "collections"
    coll_dir.mkdir(parents=True)

    monkeypatch.setattr("src.collections.file_index.COLLECTIONS_DIR", coll_dir)
    monkeypatch.setattr("src.file_mgmt.store.COLLECTIONS_DIR", coll_dir)
    monkeypatch.setattr("src.file_mgmt.service.COLLECTIONS_DIR", coll_dir)

    from src.file_mgmt.store import get_db, init_collection_db

    init_collection_db(cid)
    conn = get_db(cid)
    try:
        # Circular FK: files.current_version_id ↔ file_versions
        conn.execute("PRAGMA foreign_keys=OFF")
        now = "2026-08-01T00:00:00Z"
        file_id = "fileaaa111"
        v1, v2 = "ver111", "ver222"
        conn.execute(
            """INSERT INTO files
               (file_id, current_version_id, is_definitive, archived, unsupported,
                created_by, version)
               VALUES (?, ?, 0, 0, 0, 'local', 1)""",
            (file_id, v2),
        )
        conn.execute(
            """INSERT INTO file_versions
               (version_id, file_id, version_no, storage_file_id, archived,
                commit_message, created_by, created_at)
               VALUES (?, ?, 1, ?, 1, 'first', 'local', ?)""",
            (v1, file_id, "old_name_v1.pdf", now),
        )
        conn.execute(
            """INSERT INTO file_versions
               (version_id, file_id, version_no, storage_file_id, archived,
                commit_message, created_by, created_at)
               VALUES (?, ?, 2, ?, 0, 'second', 'local', ?)""",
            (v2, file_id, "current_name_v2.pdf", now),
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
    finally:
        conn.close()

    # Stale files.json still has v1 label (the bug scenario)
    from src.collections.file_index import add as add_file_index

    add_file_index(
        cid,
        file_id,
        f"__file__:{file_id}",
        "old_name_v1.pdf",
        "file",
        3,
        "pdf",
    )

    return cid, file_id


def test_resolve_prefers_sqlite_current_over_stale_index(col_id):
    from src.collections.file_index import resolve_display_name

    cid, file_id = col_id
    name = resolve_display_name(
        cid,
        f"__file__:{file_id}",
        payload_label="ingest_snapshot_old.pdf",
    )
    assert name == "current_name_v2.pdf"


def test_resolve_payload_fallback_without_sqlite(tmp_path, monkeypatch):
    from src.collections import file_index as fi

    monkeypatch.setattr(fi, "COLLECTIONS_DIR", tmp_path / "collections")
    monkeypatch.setattr(fi, "current_storage_filename", lambda *a, **k: None)
    monkeypatch.setattr(fi, "load", lambda _c: {})

    name = fi.resolve_display_name(
        "missing",
        "__file__:orphan",
        payload_label="from_payload.pdf",
    )
    assert name == "from_payload.pdf"


def test_resolve_never_returns_opaque_source_key(tmp_path, monkeypatch):
    from src.collections import file_index as fi

    monkeypatch.setattr(fi, "COLLECTIONS_DIR", tmp_path / "collections")
    monkeypatch.setattr(fi, "current_storage_filename", lambda *a, **k: None)
    monkeypatch.setattr(fi, "load", lambda _c: {})
    monkeypatch.setattr(fi, "_meeting_display_from_store", lambda _s: None)
    monkeypatch.setattr(fi, "_note_display_from_store", lambda _s: None)

    # Bad payload that equals technical key must not leak to UI
    name = fi.resolve_display_name(
        "missing",
        "__file__:a1824f77c0874aeabb9f95acf7fa7765",
        payload_label="__file__:a1824f77c0874aeabb9f95acf7fa7765",
    )
    assert name == ""  # empty → UI uses filesMap, not "Document" / raw key

    mname = fi.resolve_display_name(
        "missing",
        "__meeting__:deadbeefdeadbeefdeadbeefdeadbeef:tab_99",
        payload_label="__meeting__:deadbeefdeadbeefdeadbeefdeadbeef:tab_99",
    )
    assert mname == ""


def test_update_source_label_preserves_chunks(col_id):
    from src.collections.file_index import load, update_source_label

    cid, file_id = col_id
    before = load(cid)[file_id]
    update_source_label(cid, file_id, "renamed_final.pdf", original_ext="pdf")
    after = load(cid)[file_id]
    assert after["source_label"] == "renamed_final.pdf"
    assert after["chunks"] == before["chunks"]
    assert after["source"] == before["source"]


def test_context_builder_uses_display_not_raw_file_id(col_id):
    from src.rag.context_builder import build_context
    from src.rag.retriever import RetrievedChunk

    cid, file_id = col_id
    chunks = [
        RetrievedChunk(
            text="hello world",
            score=0.9,
            metadata={
                "id": "p1",
                "collection": cid,
                "source": f"__file__:{file_id}",
                "source_label": "old_name_v1.pdf",
                "chunk_index": 1,
            },
        )
    ]
    out = build_context(chunks)
    assert "### Source: current_name_v2.pdf" in out
    heading = out.split("### Source:")[1].split("\n")[0]
    assert f"__file__:{file_id}" not in heading


def test_hydrate_recall_result_display_name(col_id):
    from src.api.routes.recall import _hydrate_recall_result
    from src.rag.retriever import RetrievedChunk

    cid, file_id = col_id
    chunk = RetrievedChunk(
        text="body",
        score=0.5,
        metadata={
            "id": "c1",
            "source": f"__file__:{file_id}",
            "source_label": "old_name_v1.pdf",
            "chunk_index": 0,
            "chunk_type": "normal",
        },
    )
    result = _hydrate_recall_result(chunk, collection=cid)
    assert result.display_name == "current_name_v2.pdf"
    assert result.source == f"__file__:{file_id}"
    assert result.source_label == "old_name_v1.pdf"
