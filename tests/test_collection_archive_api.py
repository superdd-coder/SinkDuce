"""Archive/unarchive REST endpoints (src/api/routes/collections.py).

TDD for the collection archive feature — spec:
development_doc/2026-09-13-collection-archive-design.md
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.collections import store as collections_store


@pytest.fixture(autouse=True)
def _tmp_collections(tmp_path, monkeypatch):
    monkeypatch.setattr(collections_store, "COLLECTIONS_DIR", tmp_path)
    yield


def _mock_services(qdrant_names: list[str]):
    svc = MagicMock()
    svc.db.list_collections.return_value = list(qdrant_names)
    svc.db.get_collection_info.return_value = {"points_count": 7}
    return svc


class TestArchiveEndpoints:
    def test_archive_then_list_marks_archived(self):
        from src.main import app

        collections_store.create_collection_meta("col_x", "X Project")
        client = TestClient(app)
        with patch("src.api.routes.collections.services", _mock_services(["col_x"])):
            resp = client.post("/api/collections/col_x/archive")
            assert resp.status_code == 200
            assert "error" not in resp.json()

            listed = client.get("/api/collections").json()
            assert len(listed) == 1
            assert listed[0]["archived"] is True

    def test_unarchive_restores_flag(self):
        from src.main import app

        collections_store.create_collection_meta("col_y", "Y Project")
        client = TestClient(app)
        with patch("src.api.routes.collections.services", _mock_services(["col_y"])):
            client.post("/api/collections/col_y/archive")
            resp = client.post("/api/collections/col_y/unarchive")
            assert resp.status_code == 200

            listed = client.get("/api/collections").json()
            assert listed[0]["archived"] is False

    def test_list_defaults_archived_false_for_legacy_meta(self):
        from src.main import app

        # Legacy collection without the archived field (created before feature)
        collections_store.create_collection_meta("col_z", "Z Legacy")
        client = TestClient(app)
        with patch("src.api.routes.collections.services", _mock_services(["col_z"])):
            listed = client.get("/api/collections").json()
            assert listed[0]["archived"] is False

    def test_archive_missing_collection_returns_error(self):
        from src.main import app

        client = TestClient(app)
        resp = client.post("/api/collections/col_nope/archive")
        assert resp.status_code == 200
        assert "error" in resp.json()
