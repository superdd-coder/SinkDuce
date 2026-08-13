"""Legacy query routes: 410 stubs stay; dead helpers and RewriteLoop must be gone."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

GONE = {
    "error": "Gone",
    "message": "This endpoint is deprecated. Please migrate to POST /api/sessions/{id}/messages",
}


@pytest.fixture
def client():
    from src.main import app
    return TestClient(app)


class TestDeprecatedQueryRoutes:
    def test_post_query_returns_410_gone(self, client):
        resp = client.post("/api/query", json={"question": "test", "collection": "test_col"})
        assert resp.status_code == 410
        body = resp.json()
        assert body["error"] == GONE["error"]
        assert body["message"] == GONE["message"]
        assert resp.headers.get("deprecation") == "true"

    def test_post_query_stream_returns_410_gone(self, client):
        resp = client.post(
            "/api/query/stream",
            json={"question": "test", "collection": "test_col", "use_agent": True},
        )
        assert resp.status_code == 410
        assert resp.json()["error"] == "Gone"

    def test_empty_question_still_410(self, client):
        resp = client.post("/api/query", json={"question": "", "collection": "test_col"})
        assert resp.status_code == 410


class TestQueryHistory:
    def test_history_returns_list(self, client):
        resp = client.get("/api/history")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_history_reads_jsonl(self, client, tmp_path, monkeypatch):
        (tmp_path / "history.jsonl").write_text(
            json.dumps({"question": "q1", "answer": "a1", "timestamp": "t0"}) + "\n"
            + json.dumps({"question": "q2", "answer": "a2", "timestamp": "t1"}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.api.routes.query.HISTORY_DIR", tmp_path)
        resp = client.get("/api/history", params={"limit": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["question"] == "q2"

    def test_history_missing_file_is_empty(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.routes.query.HISTORY_DIR", tmp_path)
        resp = client.get("/api/history")
        assert resp.status_code == 200
        assert resp.json() == []


class TestDeadQueryHelpersRemoved:
    def test_abandoned_helpers_are_gone(self):
        from src.api.routes import query as query_mod

        for name in (
            "_col_display_name",
            "_multi_collection_note",
            "_save_history",
            "_resolve_params",
            "_resolve_sparse_llm_tokenize",
            "_resolve_llm",
            "_run_direct",
            "_run_agentic",
        ):
            assert not hasattr(query_mod, name), name


class TestRewriteLoopRemoved:
    def test_rewrite_loop_module_is_gone(self):
        assert importlib.util.find_spec("src.rag.rewrite_loop") is None
        assert not Path("src/rag/rewrite_loop.py").exists()

    def test_sse_event_name_rewrite_loop_done_kept(self):
        from src.rag import variant_fetcher as vf

        source = Path(vf.__file__).read_text(encoding="utf-8")
        assert "rewrite_loop_done" in source
