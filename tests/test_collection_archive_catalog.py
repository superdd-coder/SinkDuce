"""Retrieval gates G1/G2 for collection archive.

G1: CollectionCatalog.get_catalog skips archived when enumerating all
    (collections=None) unless include_archived=True; explicit lists honored.
G2: AgenticQueryService.run passes include_archived through to get_catalog
    (its fallback collection set derives from catalog entries, so it follows).

TDD for the collection archive feature — spec:
development_doc/2026-09-13-collection-archive-design.md
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.collections import store as collections_store
from src.rag.catalog import CollectionCatalog
from src.rag.agentic_query import AgenticQueryService


@pytest.fixture(autouse=True)
def _archived(monkeypatch):
    monkeypatch.setattr(collections_store, "archived_collection_ids", lambda: {"col_arch"})


def _make_catalog(db=None):
    db = db or MagicMock()
    db.get_collection_config.return_value = {"project_description": "P"}
    return CollectionCatalog(db, MagicMock())


class TestGetCatalogArchiveGate:
    def test_enumerating_all_skips_archived(self):
        db = MagicMock()
        db.list_collections.return_value = ["col_active", "col_arch"]
        db.get_collection_config.return_value = {"project_description": "P"}
        cat = CollectionCatalog(db, MagicMock())

        entries = cat.get_catalog(None)
        assert [e.id for e in entries] == ["col_active"]

    def test_include_archived_true_enumerates_everything(self):
        db = MagicMock()
        db.list_collections.return_value = ["col_active", "col_arch"]
        db.get_collection_config.return_value = {"project_description": "P"}
        cat = CollectionCatalog(db, MagicMock())

        entries = cat.get_catalog(None, include_archived=True)
        assert {e.id for e in entries} == {"col_active", "col_arch"}

    def test_explicit_list_honors_archived(self):
        cat = _make_catalog()

        entries = cat.get_catalog(["col_arch"])
        assert [e.id for e in entries] == ["col_arch"]

    def test_explicit_list_honors_archived_even_with_default_switch(self):
        cat = _make_catalog()

        entries = cat.get_catalog(["col_active", "col_arch"], include_archived=False)
        assert {e.id for e in entries} == {"col_active", "col_arch"}


class TestAgenticRunPassThrough:
    def test_run_forwards_include_archived_to_catalog(self):
        catalog = MagicMock()
        catalog.get_catalog.return_value = []
        decomposer = MagicMock()
        decomposer.route_collections.return_value = []
        vf = MagicMock()
        vf.chunks = []
        vf.retained_info = ""
        vf.gap_analysis = ""
        agg = MagicMock()
        svc = AgenticQueryService(
            direct_module=MagicMock(), variant_fetcher=vf, catalog=catalog,
            decomposer=decomposer, aggregator=agg, llm=MagicMock(),
        )

        svc.run("q", decompose=False, include_archived=True)

        catalog.get_catalog.assert_called_once_with(None, include_archived=True)

    def test_run_defaults_include_archived_false(self):
        catalog = MagicMock()
        catalog.get_catalog.return_value = []
        decomposer = MagicMock()
        decomposer.route_collections.return_value = []
        vf = MagicMock()
        vf.chunks = []
        vf.retained_info = ""
        vf.gap_analysis = ""
        svc = AgenticQueryService(
            direct_module=MagicMock(), variant_fetcher=vf, catalog=catalog,
            decomposer=decomposer, aggregator=MagicMock(), llm=MagicMock(),
        )

        svc.run("q", decompose=False)

        catalog.get_catalog.assert_called_once_with(None, include_archived=False)
