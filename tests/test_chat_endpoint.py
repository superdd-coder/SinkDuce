"""Tests for POST /api/sessions/{id}/messages SSE endpoint — Phase 3 Step 2.

Run: pytest tests/test_chat_endpoint.py -v --tb=short
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.db.sessions import SessionStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_chat_endpoint.db"
    s = SessionStore(str(db_path))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


def _make_mock_agent(store):
    """Create a mock ChatboxAgent that yields some SSE events."""
    agent = MagicMock()

    async def _mock_stream(session_id, user_message):
        yield {"type": "token", "content": "Hello"}
        yield {"type": "token", "content": "!"}
        yield {"type": "done", "sources": []}

    agent.chat_stream = _mock_stream
    return agent


class TestChatEndpoint:
    def test_post_message_returns_sse(self, store):
        """POST /api/sessions/{id}/messages returns SSE stream."""
        from src.main import app

        s = store.create_session(title="test")
        mock_agent = _make_mock_agent(store)

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            mock_svc.chatbox_agent = mock_agent

            resp = client.post(
                f"/api/sessions/{s.id}/messages",
                json={"content": "Hello"},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_event_types_in_stream(self, store):
        """SSE stream contains token and done events."""
        from src.main import app

        s = store.create_session(title="test")
        mock_agent = _make_mock_agent(store)

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            mock_svc.chatbox_agent = mock_agent

            resp = client.post(
                f"/api/sessions/{s.id}/messages",
                json={"content": "Hello"},
            )
            body = resp.text
            assert "event: token" in body or '"type":"token"' in body
            assert "event: done" in body or '"type":"done"' in body

    def test_sources_in_done_event(self, store):
        """The done event includes sources from the agent."""
        from src.main import app

        s = store.create_session(title="test")
        mock_agent = MagicMock()

        async def _stream_with_sources(session_id, user_message):
            yield {"type": "token", "content": "Answer"}
            yield {
                "type": "done",
                "sources": [
                    {"text": "source text", "score": 0.9, "metadata": {"file": "doc.pdf"}}
                ],
            }

        mock_agent.chat_stream = _stream_with_sources

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            mock_svc.chatbox_agent = mock_agent

            resp = client.post(
                f"/api/sessions/{s.id}/messages",
                json={"content": "Question"},
            )
            body = resp.text
            # Sources should be in the done event
            assert "source text" in body or "sources" in body

    def test_nonexistent_session_404(self, store):
        """POST to nonexistent session returns 404."""
        from src.main import app

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.post(
                "/api/sessions/nonexistent-id/messages",
                json={"content": "hello"},
            )
            assert resp.status_code == 404

    def test_empty_content_400(self, store):
        """POST with empty content returns 400."""
        from src.main import app

        s = store.create_session()

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.post(
                f"/api/sessions/{s.id}/messages",
                json={"content": ""},
            )
            assert resp.status_code == 400

    def test_agent_unavailable_503(self, store):
        """POST when chatbox_agent is None returns 503."""
        from src.main import app

        s = store.create_session()

        client = TestClient(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            mock_svc.chatbox_agent = None
            resp = client.post(
                f"/api/sessions/{s.id}/messages",
                json={"content": "hello"},
            )
            assert resp.status_code == 503
