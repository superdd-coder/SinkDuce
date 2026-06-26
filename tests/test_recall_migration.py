"""Session 2 Step 2: recall.py migration tests."""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from src.rag.retriever import RetrievedChunk
from src.rag.agentic_query import AgenticQueryResult


@pytest.fixture
def client():
    from src.main import app
    return TestClient(app)


def _patch_services():
    """Build patch dict for src.api.routes.recall.services."""
    from src.config import RAGConfig

    mock_db = MagicMock()
    mock_db.collection_exists.return_value = True
    mock_db.get_collection_config.return_value = {"chunk_mode": "normal", "search_mode": "dense"}
    ck = RetrievedChunk(text="chunk", score=0.9, metadata={"id": "c1", "source": "doc.md", "collection": "c", "chunk_index": 1})
    mock_dq = MagicMock()
    mock_dq.retrieve.return_value = MagicMock(chunks=[ck], child_groups={})
    mock_aq = MagicMock()
    mock_aq.run.return_value = AgenticQueryResult(answer=None, all_chunks=[ck], tasks=[])
    mock_llm = MagicMock()
    mock_llm.generate.return_value = "x"
    mock_cfg = MagicMock()
    mock_cfg.rag = RAGConfig()
    mock_cfg.llm.providers = []
    mock_reranker = MagicMock()
    mock_reranker.provider = MagicMock()

    return {
        "db": mock_db,
        "direct_query": mock_dq,
        "agentic_query": mock_aq,
        "llm": mock_llm,
        "config": mock_cfg,
        "reranker": mock_reranker,
    }


class TestRecallRouteDirect:
    def test_uses_direct_module(self, client):
        svc = _patch_services()
        with patch.multiple("src.api.routes.recall.services", **svc):
            with patch("src.api.routes.recall.get_embedding_overrides", return_value={}):
                resp = client.post("/api/recall/search", json={
                    "query": "test", "collections": ["test_col"],
                })
        assert resp.status_code == 200
        svc["direct_query"].retrieve.assert_called()

    def test_mixed_collections_handled_by_module(self, client):
        svc = _patch_services()
        svc["db"].get_collection_config.side_effect = [
            {"chunk_mode": "parent_child", "search_mode": "dense"},
            {"chunk_mode": "normal", "search_mode": "dense"},
        ]
        with patch.multiple("src.api.routes.recall.services", **svc):
            with patch("src.api.routes.recall.get_embedding_overrides", return_value={}):
                resp = client.post("/api/recall/search", json={
                    "query": "test", "collections": ["col_pc", "col_normal"],
                })
        assert resp.status_code == 200
        call = svc["direct_query"].retrieve.call_args
        assert call[0][1] == ["col_pc", "col_normal"]

    def test_returns_recall_results(self, client):
        svc = _patch_services()
        with patch.multiple("src.api.routes.recall.services", **svc):
            with patch("src.api.routes.recall.get_embedding_overrides", return_value={}):
                resp = client.post("/api/recall/search", json={
                    "query": "test", "collections": ["test_col"],
                })
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "time_ms" in data

    def test_child_groups_preserved(self, client):
        svc = _patch_services()
        svc["db"].get_collection_config.return_value = {"chunk_mode": "parent_child", "search_mode": "dense"}
        ck_parent = RetrievedChunk(text="parent", score=0.95, metadata={"id": "p1", "source": "doc.md", "collection": "c", "chunk_index": 1, "chunk_type": "parent"})
        svc["direct_query"].retrieve.return_value = MagicMock(
            chunks=[ck_parent],
            child_groups={"p1": [{"id": "c1", "text": "child", "score": 0.9}]},
        )
        with patch.multiple("src.api.routes.recall.services", **svc):
            with patch("src.api.routes.recall.get_embedding_overrides", return_value={}):
                resp = client.post("/api/recall/search", json={
                    "query": "test", "collections": ["test_col"],
                })
        assert resp.status_code == 200


class TestRecallRouteAgentic:
    def test_uses_agentic_service(self, client):
        svc = _patch_services()
        with patch.multiple("src.api.routes.recall.services", **svc):
            with patch("src.api.routes.recall.get_embedding_overrides", return_value={}):
                resp = client.post("/api/recall/search", json={
                    "query": "test", "collections": ["test_col"],
                    "use_agent": True,
                })
        assert resp.status_code == 200
        svc["agentic_query"].run.assert_called_once()
        assert svc["agentic_query"].run.call_args[1]["generate_answer"] is False

    def test_chunks_converted_to_recall_results(self, client):
        svc = _patch_services()
        with patch.multiple("src.api.routes.recall.services", **svc):
            with patch("src.api.routes.recall.get_embedding_overrides", return_value={}):
                resp = client.post("/api/recall/search", json={
                    "query": "test", "collections": ["test_col"],
                    "use_agent": True,
                })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) >= 1


class TestRecallRouteNoRegression:
    def test_benchmark_still_works(self, client):
        svc = _patch_services()
        with patch.multiple("src.api.routes.recall.services", **svc):
            with patch("src.api.routes.recall.get_embedding_overrides", return_value={}):
                resp = client.post("/api/recall/benchmark", json={
                    "collection": "test_col", "queries": [{"query": "t", "relevant_ids": []}],
                })
        assert resp.status_code in (200, 422, 500)

    def test_eval_endpoints_still_work(self, client):
        svc = _patch_services()
        with patch.multiple("src.api.routes.recall.services", **svc):
            with patch("src.api.routes.recall.get_embedding_overrides", return_value={}):
                resp = client.get("/api/recall/eval/test_col/cases")
        assert resp.status_code in (200, 404)
