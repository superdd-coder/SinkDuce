"""Tests for Session REST API.

Run: pytest tests/test_session_api.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.db.sessions import SessionStore, Session, Message


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_api_sessions.db"
    s = SessionStore(str(db_path))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


class TestSessionAPI:
    def test_create_session(self, store):
        """POST /api/sessions with defaults."""
        from src.main import app

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.post("/api/sessions", json={})
            assert resp.status_code == 201
            data = resp.json()
            assert data["title"] == "New Chat"
            assert data["collections"] == []
            assert "id" in data
            assert "created_at" in data
            assert data["message_count"] == 0

    def test_create_with_title_and_collections(self, store):
        """POST /api/sessions with title and collections."""
        from src.main import app

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.post(
                "/api/sessions",
                json={"title": "My Session", "collections": ["col-a", "col-b"]},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["title"] == "My Session"
            assert data["collections"] == ["col-a", "col-b"]

    def test_list_sessions(self, store):
        """GET /api/sessions returns sessions in updated_at desc order."""
        from src.main import app

        s1 = store.create_session(title="first")
        s2 = store.create_session(title="second")

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.get("/api/sessions")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) >= 2
            # most recent first
            ids = [s["id"] for s in data]
            assert ids[0] == s2.id

    def test_get_session_with_messages(self, store):
        """GET /api/sessions/{id} returns session detail with messages."""
        from src.main import app

        s = store.create_session(title="chat")
        store.add_message(s.id, "user", "hello")
        store.add_message(s.id, "assistant", "hi there")

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.get(f"/api/sessions/{s.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "chat"
            assert len(data["messages"]) == 2
            assert data["messages"][0]["role"] == "user"
            assert data["messages"][0]["content"] == "hello"
            assert data["messages"][1]["role"] == "assistant"

    def test_update_session(self, store):
        """PATCH /api/sessions/{id} updates title."""
        from src.main import app

        s = store.create_session(title="old title")

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.patch(f"/api/sessions/{s.id}", json={"title": "new title"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "new title"

    def test_delete_session(self, store):
        """DELETE /api/sessions/{id} removes session."""
        from src.main import app

        s = store.create_session()

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.delete(f"/api/sessions/{s.id}")
            assert resp.status_code == 204

            # Verify deleted
            resp2 = client.get(f"/api/sessions/{s.id}")
            assert resp2.status_code == 404

    def test_nonexistent_404(self, store):
        """GET on nonexistent session returns 404."""
        from src.main import app

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.get("/api/sessions/nonexistent-id")
            assert resp.status_code == 404

    def test_invalid_json_422(self, store):
        """POST with invalid JSON returns 422."""
        from src.main import app

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.post("/api/sessions", json={"invalid_field": "x"})
            # FastAPI ignores extra fields by default, so this should succeed
            # But let's test with wrong type for title
            resp2 = client.post("/api/sessions", json={"title": 123})
            # title as int should still work since FastAPI coerces
            # Actually test with garbage
            resp3 = client.post(
                "/api/sessions",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp3.status_code == 422
