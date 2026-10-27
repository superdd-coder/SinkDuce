"""Collection-level archive state (src/collections/store.py).

TDD for the collection archive feature — spec:
development_doc/2026-09-13-collection-archive-design.md
"""

from __future__ import annotations

import pytest

from src.collections import store as collections_store


@pytest.fixture(autouse=True)
def _tmp_collections(tmp_path, monkeypatch):
    monkeypatch.setattr(collections_store, "COLLECTIONS_DIR", tmp_path)
    yield


class TestArchivedDefault:
    def test_legacy_meta_without_field_is_not_archived(self):
        collections_store.create_collection_meta("col_a", "Alpha")
        assert collections_store.is_archived("col_a") is False

    def test_missing_meta_is_not_archived(self):
        assert collections_store.is_archived("col_nope") is False


class TestSetArchived:
    def test_archive_writes_flag_and_timestamp(self):
        collections_store.create_collection_meta("col_b", "Beta")
        meta = collections_store.set_archived("col_b", True)
        assert meta is not None
        assert meta["archived"] is True
        assert meta["archived_at"]
        assert collections_store.is_archived("col_b") is True

    def test_unarchive_clears_flag_and_timestamp(self):
        collections_store.create_collection_meta("col_c", "Gamma")
        collections_store.set_archived("col_c", True)
        meta = collections_store.set_archived("col_c", False)
        assert meta["archived"] is False
        assert meta["archived_at"] is None
        assert collections_store.is_archived("col_c") is False

    def test_missing_collection_returns_none(self):
        assert collections_store.set_archived("col_nope", True) is None


class TestArchivedCollectionIds:
    def test_lists_only_archived(self):
        collections_store.create_collection_meta("col_active", "Active")
        collections_store.create_collection_meta("col_gone", "Gone")
        collections_store.set_archived("col_gone", True)
        assert collections_store.archived_collection_ids() == {"col_gone"}

    def test_empty_dir_returns_empty_set(self):
        assert collections_store.archived_collection_ids() == set()
