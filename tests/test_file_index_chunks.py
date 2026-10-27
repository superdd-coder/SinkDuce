"""Tests for ingest chunk-count write-back to the file index (0-chunks bug)."""

import json

import pytest

from src.collections import file_index


@pytest.fixture(autouse=True)
def _tmp_collections(tmp_path, monkeypatch):
    monkeypatch.setattr(file_index, "COLLECTIONS_DIR", tmp_path)
    yield


class TestRecordIngestChunks:
    def test_updates_existing_entry_in_place(self):
        file_index.add("col1", "fid1", "__file__:fid1", "old label.pdf", "file", 0)
        file_index.record_ingest_chunks("col1", "fid1", 42)

        entry = file_index.load("col1")["fid1"]
        assert entry["chunks"] == 42
        # label and ingested_at untouched
        assert entry["source_label"] == "old label.pdf"

    def test_creates_entry_when_missing(self):
        file_index.record_ingest_chunks("col1", "fid2", 7, source="__file__:fid2")
        entry = file_index.load("col1")["fid2"]
        assert entry["chunks"] == 7
        assert entry["source"] == "__file__:fid2"

    def test_noop_without_file_id(self):
        file_index.record_ingest_chunks("col1", "", 5)
        assert file_index.load("col1") == {}

    def test_visible_via_load_for_read(self):
        """The All Files list path (load_for_read overlay) must see the count."""
        file_index.record_ingest_chunks("col1", "fid3", 9)
        idx = file_index.load_for_read("col1")
        assert idx["fid3"]["chunks"] == 9
