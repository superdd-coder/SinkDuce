"""Tests for deprecated /query endpoint — Phase 3 Step 3.

Run: pytest tests/test_deprecated.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestDeprecatedEndpoints:
    def test_query_returns_410(self):
        """POST /api/query returns 410 Gone."""
        from src.main import app

        client = TestClient(app)
        with patch("src.api.routes.query.services") as mock_svc:
            resp = client.post("/api/query", json={"question": "test"})
            assert resp.status_code == 410
            data = resp.json()
            assert data["error"] == "Gone"

    def test_query_stream_returns_410(self):
        """POST /api/query/stream returns 410 Gone."""
        from src.main import app

        client = TestClient(app)
        with patch("src.api.routes.query.services") as mock_svc:
            resp = client.post("/api/query/stream", json={"question": "test"})
            assert resp.status_code == 410
            data = resp.json()
            assert data["error"] == "Gone"

    def test_deprecation_headers(self):
        """410 responses include Deprecation and Sunset headers."""
        from src.main import app

        client = TestClient(app)
        with patch("src.api.routes.query.services") as mock_svc:
            resp = client.post("/api/query", json={"question": "test"})
            assert resp.headers.get("Deprecation") == "true"
            assert "Sunset" in resp.headers
