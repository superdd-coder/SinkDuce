"""Tests for Session REST API.

Run: pytest tests/test_session_api.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from tests.http_client import local_owner_client

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
    def test_saved_answer_recovers_only_its_exact_snapshot_citations(self, store):
        from src.chatbox.citations import EvidenceRegistry, registry_from_history
        from src.main import app

        sid = store.create_session(title="Index fixture").id
        source = {"text": "A" * 1600 + "The measured value.", "metadata": {
            "file_id": "sample-file", "version_id": "revision-a", "page_number": 4, "char_offset": 100,
        }}
        original = EvidenceRegistry(sid)
        original.capture("lookup_collection", "", sources=[source])
        first, second = original.records()
        other_session = EvidenceRegistry("other-session").register(source["text"][:1600], source["metadata"])
        content = (f"Result[cite:evidence_id_{second['id']}] again[cite:{second['id']}]. "
                   f"Other[cite:evidence_id_{other_session['id']}].")
        metadata = {"citations": [first], "tool_calls": 1}
        store.add_message(sid, "assistant", content, sources=[source], metadata=metadata)
        store.add_message(sid, "assistant", content, sources=[{
            **source, "metadata": {**source["metadata"], "version_id": "revision-b"},
        }], metadata={"citations": []})
        store.add_message(sid, "assistant", content, sources=[source], metadata={"qa_projects": ["restricted"]})
        with patch("src.api.routes.sessions.services") as svc, patch(
            "src.chatbox.qa_evidence.search_index.can_read", return_value=False,
        ):
            svc.session_store = store
            client = local_owner_client(app)
            result = client.get(f"/api/sessions/{sid}")
            history = registry_from_history(store, sid)
        assert result.status_code == 200
        repaired, wrong_version, denied = result.json()["messages"]
        assert repaired["metadata"] == {**metadata, "citations": [first, second]}
        assert second["quote"] == "The measured value." and second["location"] == {"char_offset": 1700}
        assert wrong_version["metadata"]["citations"] == []
        assert denied["metadata"] == {"qa_source_unavailable": True}
        assert denied["sources"] is None
        assert history.used(content) == [second]
        assert store.get_messages(sid)[0].metadata == metadata

    def test_retrieval_details_respect_current_collection_access(self, store):
        from src.main import app

        session = store.create_session(title="Fixture search")
        rows = [{"collection": cid, "model": cid, "dimensions": 4} for cid in ("available", "removed")]
        store.add_message(session.id, "assistant", "Answer", metadata={
            "skipped_collections": ["offline", "removed"], "embedding_searches": rows})
        with patch("src.api.routes.sessions.services") as svc, patch(
            "src.identity.visibility.visible_collection_ids", return_value={"available", "offline"},
        ):
            svc.session_store = store
            client = local_owner_client(app)
            response = client.get(f"/api/sessions/{session.id}")
        assert response.status_code == 200
        meta = response.json()["messages"][0]["metadata"]
        assert meta["skipped_collections"] == ["offline"] and meta["embedding_searches"] == rows[:1]

    def test_create_session(self, store):
        """POST /api/sessions with defaults."""
        from src.main import app

        client = local_owner_client(app)
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

        client = local_owner_client(app)
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

        client = local_owner_client(app)
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

        client = local_owner_client(app)
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

        client = local_owner_client(app)
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

        client = local_owner_client(app)
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

        client = local_owner_client(app)
        with patch("src.api.routes.sessions.services") as mock_svc:
            mock_svc.session_store = store
            resp = client.get("/api/sessions/nonexistent-id")
            assert resp.status_code == 404

    def test_invalid_json_422(self, store):
        """POST with invalid JSON returns 422."""
        from src.main import app

        client = local_owner_client(app)
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


class TestGenerateSessionTitle:
    def test_uses_llm_generate_not_raw_client_history(self, store):
        """Title must not replay tool-call history into a hardcoded extra_body."""
        from src.main import app
        from src.api.routes import sessions as sess_mod

        s = store.create_session(title="New Chat")
        store.add_message(s.id, "user", "What is in the library?")
        store.add_message(s.id, "assistant", "There are three folders.")

        class FakeLLM:
            def __init__(self):
                self.seen = None

            def generate(self, prompt, **kwargs):
                self.seen = {"prompt": prompt, **kwargs}
                return "Library folders"

        llm = FakeLLM()
        agent = type("A", (), {"_llm": llm})()
        client = local_owner_client(app)
        with patch.object(sess_mod, "services") as mock_svc:
            mock_svc.session_store = store
            mock_svc.chatbox_agent = agent
            resp = client.post(f"/api/sessions/{s.id}/generate-title")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Library folders"
        assert llm.seen is not None
        assert llm.seen.get("thinking") is False
        assert "What is in the library?" in llm.seen["prompt"]
        assert "tool_calls" not in llm.seen["prompt"]

    def test_falls_back_to_question_when_llm_fails(self, store):
        from src.main import app
        from src.api.routes import sessions as sess_mod

        s = store.create_session(title="New Chat")
        store.add_message(s.id, "user", "Summarize the diligence memo")
        store.add_message(s.id, "assistant", "It covers three sites.")

        class BoomLLM:
            def generate(self, *a, **k):
                raise RuntimeError("provider rejected thinking extra_body")

        agent = type("A", (), {"_llm": BoomLLM()})()
        client = local_owner_client(app)
        with patch.object(sess_mod, "services") as mock_svc:
            mock_svc.session_store = store
            mock_svc.chatbox_agent = agent
            resp = client.post(f"/api/sessions/{s.id}/generate-title")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Summarize the diligence memo"

    def test_fallback_helper_trims_question(self):
        from src.api.routes.sessions import _fallback_session_title

        assert _fallback_session_title("Hello there") == "Hello there"
        long_q = "x" * 90
        assert len(_fallback_session_title(long_q)) <= 60
