"""Versioned doc_summary: store/get by version_id, consolidate picks current."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_sm():
    from src.rag.summary_manager import SummaryManager

    db = MagicMock()
    db.collection_exists.return_value = True
    db.scroll_points.return_value = ([], None)
    # client.get_collection for vector size
    info = MagicMock()
    info.config.params.vectors.size = 128
    db.client.get_collection.return_value = info
    sm = SummaryManager(db=db, vector_size=128)
    return sm, db


def test_store_with_version_dual_writes():
    sm, db = _make_sm()
    sm.store_doc_summary(
        "col",
        "__file__:abc",
        ["d1"],
        ["f1"],
        ["i1"],
        include_in_summary=True,
        version_id="ver111",
        file_id="abc",
    )
    kwargs = db.upsert_points.call_args.kwargs
    assert len(kwargs["ids"]) == 2  # versioned + legacy
    payloads = kwargs["payloads"]
    assert all(p.get("version_id") == "ver111" for p in payloads)
    assert all(p.get("file_id") == "abc" for p in payloads)
    assert payloads[0]["data"] == ["d1"]


def test_get_doc_summary_matches_version():
    sm, db = _make_sm()
    db.scroll_points.return_value = (
        [
            {
                "id": "1",
                "payload": {
                    "type": "doc_summary",
                    "source": "__file__:abc",
                    "version_id": "v1",
                    "data": ["old"],
                    "facts": [],
                    "insights": [],
                },
            },
            {
                "id": "2",
                "payload": {
                    "type": "doc_summary",
                    "source": "__file__:abc",
                    "version_id": "v2",
                    "data": ["new"],
                    "facts": [],
                    "insights": [],
                },
            },
        ],
        None,
    )
    got = sm.get_doc_summary("col", "__file__:abc", version_id="v1")
    assert got is not None
    assert got["data"] == ["old"]
    got2 = sm.get_doc_summary("col", "__file__:abc", version_id="v2")
    assert got2["data"] == ["new"]


def test_pick_current_doc_summaries(monkeypatch):
    from src.api.routes import info as info_mod

    monkeypatch.setattr(
        info_mod,
        "current_version_id_for_source",
        lambda col, src: "v2" if src == "__file__:a" else None,
    )
    summaries = [
        {"source": "__file__:a", "version_id": "v1", "data": ["a1"]},
        {"source": "__file__:a", "version_id": "v2", "data": ["a2"]},
        {"source": "legacy.pdf", "data": ["L"]},
    ]
    picked = info_mod.pick_current_doc_summaries("col", summaries)
    by_src = {p["source"]: p for p in picked}
    assert by_src["__file__:a"]["data"] == ["a2"]
    assert by_src["legacy.pdf"]["data"] == ["L"]
