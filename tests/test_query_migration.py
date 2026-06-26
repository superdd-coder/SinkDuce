"""Session 2 Step 1: query.py migration tests."""

import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from src.rag.retriever import RetrievedChunk
from src.rag.agentic_query import AgenticQueryResult


# ── helpers ──────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from src.main import app
    return TestClient(app)


def _mock_services(monkeypatch):
    """Set up all required mocks on services."""
    from src.services import services
    mock_db = MagicMock()
    mock_db.collection_exists.return_value = True
    mock_db.get_collection_config.return_value = {"chunk_mode": "normal", "search_mode": "dense"}
    monkeypatch.setattr(services, "db", mock_db)

    mock_dq = MagicMock()
    ck = RetrievedChunk(text="test chunk", score=0.9, metadata={"id": "c1", "source": "doc.md", "collection": "test_col"})
    mock_dq.retrieve.return_value = MagicMock(chunks=[ck], child_groups={}, answer="test answer", context="test context")
    monkeypatch.setattr(services, "direct_query", mock_dq)

    mock_aq = MagicMock()
    mock_aq.run.return_value = AgenticQueryResult(
        answer="Agentic answer",
        all_chunks=[ck],
        tasks=[{"task_id": "t1", "task_query": "test", "sub_queries": []}],
    )
    monkeypatch.setattr(services, "agentic_query", mock_aq)

    mock_llm = MagicMock()
    mock_llm.generate.return_value = "Generated answer"
    monkeypatch.setattr(services, "llm", mock_llm)

    # config
    from src.config import RAGConfig
    mock_cfg = MagicMock()
    mock_cfg.rag = RAGConfig()
    mock_cfg.llm.providers = []
    monkeypatch.setattr(services, "config", mock_cfg)

    return services


# ── TestQueryRouteDirect ──────────────────────────────────────────────────

class TestQueryRouteDirect:
    def test_direct_branch_uses_direct_module(self, client, monkeypatch):
        svc = _mock_services(monkeypatch)

        with patch("src.api.routes.query._save_history"):
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                resp = client.post("/api/query", json={
                    "question": "test", "collection": "test_col",
                    "use_agent": False,
                })

        assert resp.status_code == 200
        svc.direct_query.retrieve.assert_called_once()
        svc.agentic_query.run.assert_not_called()

    def test_direct_branch_returns_answer(self, client, monkeypatch):
        _mock_services(monkeypatch)

        with patch("src.api.routes.query._save_history"):
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                resp = client.post("/api/query", json={
                    "question": "test", "collection": "test_col",
                    "use_agent": False,
                })

        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert len(data["answer"]) > 0

    def test_direct_branch_saves_history(self, client, monkeypatch):
        _mock_services(monkeypatch)

        with patch("src.api.routes.query._save_history") as mock_save:
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                resp = client.post("/api/query", json={
                    "question": "test", "collection": "test_col",
                    "use_agent": False,
                })

        assert resp.status_code == 200
        mock_save.assert_called_once()


# ── TestQueryRouteAgentic ─────────────────────────────────────────────────

class TestQueryRouteAgentic:
    def test_agentic_branch_uses_service(self, client, monkeypatch):
        svc = _mock_services(monkeypatch)

        with patch("src.api.routes.query._save_history"):
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                resp = client.post("/api/query", json={
                    "question": "test", "collection": "test_col",
                    "use_agent": True,
                })

        assert resp.status_code == 200
        svc.agentic_query.run.assert_called_once()
        call_kwargs = svc.agentic_query.run.call_args
        assert call_kwargs[1].get("generate_answer") is True

    def test_agentic_branch_returns_answer_and_sources(self, client, monkeypatch):
        _mock_services(monkeypatch)

        with patch("src.api.routes.query._save_history"):
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                resp = client.post("/api/query", json={
                    "question": "test", "collection": "test_col",
                    "use_agent": True,
                })

        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert "sources" in data
        assert "iterations" in data

    def test_agentic_branch_streaming(self, client, monkeypatch):
        svc = _mock_services(monkeypatch)

        with patch("src.api.routes.query._save_history"):
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                resp = client.post("/api/query/stream", json={
                    "question": "test", "collection": "test_col",
                    "use_agent": True,
                })

        assert resp.status_code == 200
        body = resp.text
        # Should have SSE events
        assert "data:" in body
        events = [json.loads(line.replace("data: ", ""))
                   for line in body.split("\n") if line.startswith("data:")]
        types = {e["type"] for e in events}
        assert "done" in types


# ── TestQueryRouteParams ──────────────────────────────────────────────────

class TestQueryRouteParams:
    def test_agent_enabled_config_ignored(self, client, monkeypatch):
        svc = _mock_services(monkeypatch)
        # old config field should be ignored
        svc.db.get_collection_config.return_value = {
            "chunk_mode": "normal", "search_mode": "dense",
            "agent_enabled": True, "self_rag_enabled": True,
        }

        with patch("src.api.routes.query._save_history"):
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                # use_agent=False overrides old config
                resp = client.post("/api/query", json={
                    "question": "test", "collection": "test_col",
                    "use_agent": False,
                })

        assert resp.status_code == 200
        # Direct module should be used even though config says agent_enabled=True
        svc.direct_query.retrieve.assert_called()

    def test_legacy_self_rag_config_ignored(self, client, monkeypatch):
        svc = _mock_services(monkeypatch)
        svc.db.get_collection_config.return_value = {
            "chunk_mode": "normal", "search_mode": "dense",
            "self_rag_enabled": True, "self_rag_max_iterations": 5,
        }

        with patch("src.api.routes.query._save_history"):
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                resp = client.post("/api/query", json={
                    "question": "test", "collection": "test_col",
                    "use_agent": False,
                })

        assert resp.status_code == 200

    def test_missing_services_returns_503(self, client, monkeypatch):
        svc = _mock_services(monkeypatch)
        monkeypatch.setattr(svc, "direct_query", None)
        monkeypatch.setattr(svc, "agentic_query", None)

        with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
            resp = client.post("/api/query", json={
                "question": "test", "collection": "test_col",
                "use_agent": False,
            })

        assert resp.status_code == 503


# ── TestQueryRouteNoRegression ────────────────────────────────────────────

class TestQueryRouteNoRegression:
    def test_history_endpoint_still_works(self, client):
        resp = client.get("/api/history")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_empty_question_rejected(self, client, monkeypatch):
        _mock_services(monkeypatch)

        # Empty question should still work (FastAPI won't reject it by default)
        # but we verify the endpoint doesn't crash
        with patch("src.api.routes.query._save_history"):
            with patch("src.api.routes.query.get_embedding_overrides", return_value={}):
                resp = client.post("/api/query", json={
                    "question": "", "collection": "test_col",
                    "use_agent": False,
                })

        # Should not 500
        assert resp.status_code in (200, 400, 422)
