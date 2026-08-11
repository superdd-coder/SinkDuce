"""Tests for Recall testing module backend.

Run: pytest tests/test_recall.py -x -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock
import time

import pytest
from fastapi.testclient import TestClient

from src.config import EmbeddingConfig, EmbeddingProviderConfig


# ── Schemas ────────────────────────────────────────────────


def test_recall_search_request_defaults():
    """RecallSearchRequest has correct defaults."""
    from src.api.schemas import RecallSearchRequest

    req = RecallSearchRequest(query="test query")
    assert req.query == "test query"
    assert req.collections == ["default"]
    assert req.search_mode is None  # falls back to collection config → global
    assert req.top_k is None  # falls back to collection config → global
    assert req.rerank_top_k is None  # falls back to collection config → global
    assert req.use_reranker is None  # falls back to True (like chat)
    assert req.use_agent is False


def test_recall_search_response_fields():
    """RecallSearchResponse has all expected fields."""
    from src.api.schemas import RecallSearchResponse, RecallResult

    resp = RecallSearchResponse(
        results=[
            RecallResult(
                id="1",
                text="hello",
                score=0.9,
                source="test.txt",
                collection="default",
                chunk_index=0,
                chunk_type="normal",
            )
        ],
        time_ms=100,
        total=1,
        query_used="test",
    )
    assert resp.agent_iterations == 0
    assert len(resp.results) == 1
    assert resp.results[0].context is None


def test_recall_result_with_context():
    """RecallResult supports optional context field."""
    from src.api.schemas import RecallResult

    r = RecallResult(
        id="1",
        text="chunk",
        score=0.8,
        source="a.txt",
        collection="col",
        chunk_index=0,
        chunk_type="normal",
        context="some context",
    )
    assert r.context == "some context"


def test_recall_benchmark_request_defaults():
    """RecallBenchmarkRequest has correct defaults."""
    from src.api.schemas import RecallBenchmarkRequest

    req = RecallBenchmarkRequest(queries=["q1", "q2"])
    assert req.collections == ["default"]
    assert req.top_k is None  # falls back to collection config → global
    assert req.use_agent is False
    assert len(req.queries) == 2


def test_benchmark_result_fields():
    """BenchmarkResult has expected fields."""
    from src.api.schemas import BenchmarkResult

    result = BenchmarkResult(
        total_queries=2,
        avg_time_ms=50.0,
        results=[{"query": "q1", "time_ms": 40, "results_count": 3}],
        metrics={"recall@5": 0.8, "mrr": 0.6, "ndcg": 0.7},
    )
    assert result.total_queries == 2
    assert result.metrics["mrr"] == 0.6


# ── Recall params endpoint ─────────────────────────────────


def test_recall_params_normal_collection():
    """GET /recall/params returns expected structure for normal collection."""
    from src.main import app

    client = TestClient(app)
    # Mock the services.db.get_collection_config to return normal mode
    with patch("src.api.routes.recall.services") as mock_svc:
        mock_svc.db.get_collection_config.return_value = {
            "chunk_mode": "normal",
            "search_mode": "dense",
            "top_k": 10,
        }
        mock_svc.reranker = MagicMock()
        resp = client.get("/api/recall/params/default")
        assert resp.status_code == 200
        data = resp.json()
        assert "search_modes" in data
        assert "dense" in data["search_modes"]
        assert "hybrid" in data["search_modes"]
        assert data["max_top_k"] == 50
        assert data["has_reranker"] is True
        assert data["has_sparse_encoder"] is True
        assert data["chunk_mode"] == "normal"


def test_recall_params_no_reranker():
    """GET /recall/params reports has_reranker=False when reranker is None."""
    from src.main import app

    client = TestClient(app)
    with patch("src.api.routes.recall.services") as mock_svc:
        mock_svc.db.get_collection_config.return_value = {
            "chunk_mode": "parent_child",
        }
        mock_svc.reranker = None
        resp = client.get("/api/recall/params/default")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_reranker"] is False
        assert data["chunk_mode"] == "parent_child"


# ── Recall search: normal mode ─────────────────────────────


def test_recall_search_normal_mode():
    """POST /recall/search in dense mode returns results with timing."""
    from src.main import app
    from src.rag.retriever import RetrievedChunk

    client = TestClient(app)
    mock_chunks = [
        RetrievedChunk(text="chunk 1", score=0.9, metadata={"source": "a.txt", "chunk_index": 0}),
        RetrievedChunk(text="chunk 2", score=0.7, metadata={"source": "b.txt", "chunk_index": 1}),
    ]

    mock_embedding = MagicMock()
    mock_embedding.embed_query.return_value = [0.1] * 512

    with patch("src.api.routes.recall.services") as mock_svc, \
         patch("src.services.services", mock_svc), \
         patch("src.rag.collection_utils.create_embedding_provider", return_value=mock_embedding):
        mock_svc.db.collection_exists.return_value = True
        mock_svc.db.get_collection_config.return_value = {
            "chunk_mode": "normal",
            "search_mode": "dense",
            "top_k": 10,
        }
        mock_svc.db.get_vector_size.return_value = 512
        mock_svc.config.embedding = EmbeddingConfig(providers=[
            EmbeddingProviderConfig(id="emb-1", provider="local", model="test", dimensions=512, is_default=True)
        ])
        mock_svc.retriever.retrieve.return_value = mock_chunks
        mock_svc.reranker = None
        mock_svc.embedding = mock_embedding

        resp = client.post(
            "/api/recall/search",
            json={"query": "test query", "collections": ["default"], "use_agent": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "time_ms" in data
        assert "total" in data
        assert data["total"] == 2
        assert data["query_used"] == "test query"
        assert data["agent_iterations"] == 0
        assert len(data["results"]) == 2
        assert data["results"][0]["text"] == "chunk 1"
        assert data["results"][0]["score"] == 0.9
        assert data["results"][0]["collection"] == "default"


def test_recall_search_normal_mode_with_reranker():
    """POST /recall/search applies reranker when available."""
    from src.main import app
    from src.rag.retriever import RetrievedChunk

    client = TestClient(app)
    mock_chunks = [
        RetrievedChunk(text="chunk 1", score=0.5, metadata={"source": "a.txt", "chunk_index": 0}),
        RetrievedChunk(text="chunk 2", score=0.3, metadata={"source": "b.txt", "chunk_index": 1}),
    ]
    reranked = [
        RetrievedChunk(text="chunk 2", score=0.95, metadata={"source": "b.txt", "chunk_index": 1}),
        RetrievedChunk(text="chunk 1", score=0.8, metadata={"source": "a.txt", "chunk_index": 0}),
    ]

    mock_embedding = MagicMock()
    mock_embedding.embed_query.return_value = [0.1] * 512

    with patch("src.api.routes.recall.services") as mock_svc, \
         patch("src.services.services", mock_svc), \
         patch("src.rag.collection_utils.create_embedding_provider", return_value=mock_embedding):
        mock_svc.db.collection_exists.return_value = True
        mock_svc.db.get_collection_config.return_value = {
            "chunk_mode": "normal",
            "rerank_top_k": 2,
        }
        mock_svc.db.get_vector_size.return_value = 512
        mock_svc.config.embedding = EmbeddingConfig(providers=[
            EmbeddingProviderConfig(id="emb-1", provider="local", model="test", dimensions=512, is_default=True)
        ])
        mock_svc.retriever.retrieve.return_value = mock_chunks
        mock_svc.reranker = MagicMock()
        mock_svc.reranker.rerank.return_value = reranked
        mock_svc.embedding = mock_embedding

        resp = client.post(
            "/api/recall/search",
            json={"query": "test", "collections": ["default"], "top_k": 2, "use_reranker": True, "rerank_top_k": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        # After reranking, chunk 2 should be first
        assert data["results"][0]["text"] == "chunk 2"
        mock_svc.reranker.rerank.assert_called_once()


# ── Recall search: agentic mode ────────────────────────────


def test_recall_search_agentic_mode():
    """POST /recall/search with use_agent=True uses AgenticRAG."""
    from src.main import app
    from src.rag.agent import AgentResult

    client = TestClient(app)
    mock_result = AgentResult(
        answer="test answer",
        sources=[{"text": "src1", "score": 0.9, "metadata": {"source": "a.txt", "chunk_index": 0}}],
        iterations=2,
        query_used="rewritten query",
    )

    mock_embedding = MagicMock()
    mock_embedding.embed_query.return_value = [0.1] * 512
    mock_agent = MagicMock()
    mock_agent.run.return_value = mock_result

    with patch("src.api.routes.recall.services") as mock_svc, \
         patch("src.services.services", mock_svc), \
         patch("src.rag.collection_utils.create_embedding_provider", return_value=mock_embedding), \
         patch("src.rag.agent.AgenticRAG", return_value=mock_agent):
        mock_svc.db.collection_exists.return_value = True
        mock_svc.db.get_collection_config.return_value = {
            "chunk_mode": "normal",
        }
        mock_svc.db.get_vector_size.return_value = 512
        mock_svc.config.embedding = EmbeddingConfig(provider="local", model="test", dimensions=512)
        mock_svc.reranker = None
        mock_svc.embedding = mock_embedding

        resp = client.post(
            "/api/recall/search",
            json={"query": "test", "collections": ["default"], "use_agent": True, "top_k": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_iterations"] == 2
        assert len(data["results"]) == 1
        assert data["results"][0]["text"] == "src1"
        mock_agent.run.assert_called_once()
        # Verify rerank_top_k and max_iterations were passed to AgenticRAG
        call_args, call_kwargs = mock_agent.run.call_args
        assert call_kwargs.get("top_k") == 5


# ── Recall search: multi-collection ────────────────────────


def test_recall_search_multi_collection():
    """POST /recall/search with multiple collections aggregates results."""
    from src.main import app
    from src.rag.retriever import RetrievedChunk

    client = TestClient(app)
    chunks_col1 = [RetrievedChunk(text="col1 chunk", score=0.9, metadata={"source": "a.txt", "chunk_index": 0})]
    chunks_col2 = [RetrievedChunk(text="col2 chunk", score=0.8, metadata={"source": "b.txt", "chunk_index": 0})]

    mock_embedding = MagicMock()
    mock_embedding.embed_query.return_value = [0.1] * 512

    with patch("src.api.routes.recall.services") as mock_svc, \
         patch("src.services.services", mock_svc), \
         patch("src.rag.collection_utils.create_embedding_provider", return_value=mock_embedding):
        mock_svc.db.collection_exists.return_value = True
        mock_svc.db.get_collection_config.return_value = {
            "chunk_mode": "normal",
            "top_k": 10,
        }
        mock_svc.db.get_vector_size.return_value = 512
        mock_svc.config.embedding = EmbeddingConfig(provider="local", model="test", dimensions=512)
        # multi_collection_retrieve calls retriever.retrieve for each collection
        mock_svc.retriever.retrieve.side_effect = [chunks_col1, chunks_col2]
        mock_svc.reranker = None
        mock_svc.embedding = mock_embedding

        resp = client.post(
            "/api/recall/search",
            json={"query": "test", "collections": ["col1", "col2"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        collections_found = {r["collection"] for r in data["results"]}
        assert "col1" in collections_found
        assert "col2" in collections_found


# ── Recall search: timing ──────────────────────────────────


def test_recall_search_includes_timing():
    """POST /recall/search response time_ms is a positive integer."""
    from src.main import app
    from src.rag.retriever import RetrievedChunk

    client = TestClient(app)

    mock_embedding = MagicMock()
    mock_embedding.embed_query.return_value = [0.1] * 512

    with patch("src.api.routes.recall.services") as mock_svc, \
         patch("src.services.services", mock_svc), \
         patch("src.rag.collection_utils.create_embedding_provider", return_value=mock_embedding):
        mock_svc.db.collection_exists.return_value = True
        mock_svc.db.get_collection_config.return_value = {"chunk_mode": "normal", "top_k": 10}
        mock_svc.db.get_vector_size.return_value = 512
        mock_svc.config.embedding = EmbeddingConfig(provider="local", model="test", dimensions=512)
        mock_svc.retriever.retrieve.return_value = []
        mock_svc.reranker = None
        mock_svc.embedding = mock_embedding

        resp = client.post(
            "/api/recall/search",
            json={"query": "empty", "collections": ["default"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["time_ms"], int)
        assert data["time_ms"] >= 0


# ── Recall search: collection not found ────────────────────


def test_recall_search_collection_not_found():
    """POST /recall/search returns empty results for non-existent collection."""
    from src.main import app

    client = TestClient(app)

    with patch("src.api.routes.recall.services") as mock_svc:
        mock_svc.db.collection_exists.return_value = False
        mock_svc.retriever.retrieve.return_value = []
        mock_svc.reranker = None

        resp = client.post(
            "/api/recall/search",
            json={"query": "test", "collections": ["nonexistent"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []


# ── Recall benchmark ───────────────────────────────────────


def test_recall_benchmark_basic():
    """POST /recall/benchmark runs multiple queries and computes metrics."""
    from src.main import app
    from src.rag.retriever import RetrievedChunk

    client = TestClient(app)
    mock_chunks = [
        RetrievedChunk(text="result", score=0.9, metadata={"source": "a.txt", "chunk_index": 0}),
    ]

    mock_embedding = MagicMock()
    mock_embedding.embed_query.return_value = [0.1] * 512

    with patch("src.api.routes.recall.services") as mock_svc, \
         patch("src.services.services", mock_svc), \
         patch("src.rag.collection_utils.create_embedding_provider", return_value=mock_embedding):
        mock_svc.db.collection_exists.return_value = True
        mock_svc.db.get_collection_config.return_value = {"chunk_mode": "normal", "top_k": 10}
        mock_svc.db.get_vector_size.return_value = 512
        mock_svc.config.embedding = EmbeddingConfig(provider="local", model="test", dimensions=512)
        mock_svc.retriever.retrieve.return_value = mock_chunks
        mock_svc.reranker = None
        mock_svc.embedding = mock_embedding

        resp = client.post(
            "/api/recall/benchmark",
            json={
                "collections": ["default"],
                "queries": ["query 1", "query 2", "query 3"],
                "top_k": 5,
                "use_agent": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_queries"] == 3
        assert isinstance(data["avg_time_ms"], float)
        assert len(data["results"]) == 3
        assert "metrics" in data
        assert "recall@k" in data["metrics"]
        assert "mrr" in data["metrics"]
        # Each per-query result should have basic fields
        for qr in data["results"]:
            assert "query" in qr
            assert "time_ms" in qr
            assert "results_count" in qr


def test_recall_benchmark_empty_queries():
    """POST /recall/benchmark with no queries returns zero totals."""
    from src.main import app

    client = TestClient(app)

    with patch("src.api.routes.recall.services") as mock_svc:
        mock_svc.db.collection_exists.return_value = True
        mock_svc.db.get_collection_config.return_value = {"chunk_mode": "normal"}

        resp = client.post(
            "/api/recall/benchmark",
            json={"queries": [], "collections": ["default"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_queries"] == 0
        assert data["results"] == []


# ── Metrics computation ────────────────────────────────────


def test_compute_metrics_basic():
    """Recall metrics (MRR, NDCG) compute correctly for known inputs."""
    from src.api.routes.recall import _compute_metrics

    # Perfect ranking: relevant doc at position 0
    results_list = [
        {"query": "q1", "results": [{"score": 0.9, "relevant": True}]},
        {"query": "q2", "results": [{"score": 0.8, "relevant": True}]},
    ]
    metrics = _compute_metrics(results_list, k=5)
    assert metrics["mrr"] == 1.0  # Both have relevant at rank 1
    assert metrics["recall@k"] == 1.0


def test_compute_metrics_no_relevant():
    """Metrics are 0 when no relevant results."""
    from src.api.routes.recall import _compute_metrics

    results_list = [
        {"query": "q1", "results": [{"score": 0.9, "relevant": False}]},
    ]
    metrics = _compute_metrics(results_list, k=5)
    assert metrics["mrr"] == 0.0
    assert metrics["recall@k"] == 0.0


def test_compute_metrics_empty():
    """Metrics handle empty results gracefully."""
    from src.api.routes.recall import _compute_metrics

    metrics = _compute_metrics([], k=5)
    assert metrics["mrr"] == 0.0
    assert metrics["recall@k"] == 0.0
    assert metrics["ndcg"] == 0.0


# ── Hydration of RecallResult ──────────────────────────────


def test_hydrate_recall_result_from_chunk():
    """_hydrate_recall_result correctly maps RetrievedChunk to RecallResult."""
    from src.api.routes.recall import _hydrate_recall_result
    from src.rag.retriever import RetrievedChunk

    chunk = RetrievedChunk(
        text="hello world",
        score=0.85,
        metadata={"source": "doc.pdf", "chunk_index": 3, "chunk_type": "parent", "context": "bg info"},
    )
    result = _hydrate_recall_result(chunk, collection="mycol")
    assert result.text == "hello world"
    assert result.score == 0.85
    assert result.source == "doc.pdf"
    assert result.collection == "mycol"
    assert result.chunk_index == 3
    assert result.chunk_type == "parent"
    assert result.context == "bg info"


def test_hydrate_recall_result_defaults():
    """_hydrate_recall_result uses defaults for missing metadata."""
    from src.api.routes.recall import _hydrate_recall_result
    from src.rag.retriever import RetrievedChunk

    chunk = RetrievedChunk(text="text", score=0.5, metadata={})
    result = _hydrate_recall_result(chunk, collection="default")
    assert result.source == ""
    assert result.chunk_index == 0
    assert result.chunk_type == "normal"
    assert result.context is None


# ── Coverage-dominant quality formula ─────────────────────


def test_quality_formula_coverage_dominant():
    """Coverage (any +1) dominates; noise is a secondary adjustment.

    Formula: coverage*(1 - 0.3*noise) - (1-coverage)*(0.5 + 0.5*noise)
    where noise = count(-1) / judged_count, coverage = 1 if any +1 else 0.
    """
    def q(positive: int, neutral: int, negative: int) -> float:
        total = positive + neutral + negative
        coverage = 1 if positive > 0 else 0
        noise = negative / total if total else 0.0
        return coverage * (1 - 0.3 * noise) - (1 - coverage) * (0.5 + 0.5 * noise)

    # Covered cases: positive dominates
    assert q(5, 0, 0) == 1.0
    assert q(1, 4, 0) == 1.0          # 1 answer, 4 neutral → perfect
    assert q(1, 0, 4) == pytest.approx(0.76)  # 1 answer, 4 noise → still high
    assert q(1, 2, 2) == pytest.approx(0.88)

    # Uncovered cases: negative
    assert q(0, 5, 0) == -0.5         # nothing useful, not misleading
    assert q(0, 3, 2) == pytest.approx(-0.7)
    assert q(0, 0, 5) == -1.0         # nothing useful, all noise

    # Edge: only noise, no coverage
    assert q(0, 0, 1) == -1.0


def test_quality_range_is_bounded():
    """Quality score stays in [-1, 1] by construction."""
    def q(positive: int, neutral: int, negative: int) -> float:
        total = positive + neutral + negative
        coverage = 1 if positive > 0 else 0
        noise = negative / total if total else 0.0
        score = coverage * (1 - 0.3 * noise) - (1 - coverage) * (0.5 + 0.5 * noise)
        return max(-1.0, min(1.0, score))

    cases = [
        (5, 0, 0), (0, 0, 5), (0, 5, 0), (1, 0, 4),
        (3, 1, 1), (0, 1, 4), (1, 1, 3), (10, 0, 0),
    ]
    for pos, neu, neg in cases:
        s = q(pos, neu, neg)
        assert -1.0 <= s <= 1.0, f"out of range for ({pos},{neu},{neg}): {s}"


# ── Query specificity validation ───────────────────────────


def test_is_specific_query_rejects_generic_refs():
    """Generic demonstratives like 'this proposal' are rejected when no specific identifiers exist."""
    from src.api.routes.recall import _is_specific_query
    # These have no specific identifiers (short, no proper nouns, no acronyms, no numbers)
    assert _is_specific_query("what is it") is False
    assert _is_specific_query("how does it work") is False
    assert _is_specific_query("tell me about this") is False


def test_is_specific_query_accepts_specific_queries():
    """Queries with proper nouns, acronyms, or specific numbers should pass."""
    from src.api.routes.recall import _is_specific_query
    # These should pass
    assert _is_specific_query("What is the capacity of AQ PURE-2500 MAX?") is True
    assert _is_specific_query("What is the Chotiwat SMC project timeline?") is True
    assert _is_specific_query("What was the 2024 revenue for ABC Corp?") is True
    assert _is_specific_query("What is the AQIONIX proposal for project X?") is True


def test_is_specific_query_rejects_too_short():
    """Too-short or too-long queries are rejected."""
    from src.api.routes.recall import _is_specific_query
    assert _is_specific_query("Capacity of AQ-2500") is False  # 3 words
    # 36+ words rejected
    long_q = (
        "What is the capacity and detailed technical specifications of the AQ "
        "PURE-2500 MAX system installed at the Chotiwat SMC project in 2024 "
        "according to the official proposal document, including the physical "
        "dimensions and operating parameters of the unit?"
    )
    assert len(long_q.split()) >= 35
    assert _is_specific_query(long_q) is False


# ── Recalled metric (hard OR holistic) ─────────────────────


def test_recalled_definition():
    """recalled = hard_recall OR holistic_can_answer."""
    def is_recalled(hard: int, holistic: int) -> int:
        return 1 if (hard or holistic) else 0

    # Both signals: recalled
    assert is_recalled(1, 1) == 1
    # Only hard: recalled (target was retrieved)
    assert is_recalled(1, 0) == 1
    # Only holistic: recalled (combination answers query, even without target)
    assert is_recalled(0, 1) == 1
    # Neither: not recalled
    assert is_recalled(0, 0) == 0


# ── Eval case generation: searchable (non-archived) only ───


def test_is_searchable_eval_payload_filters_archived_and_non_current():
    """Eval sampling must match Retriever: no archived / non-current targets."""
    from src.api.routes.recall import _is_searchable_eval_payload

    assert _is_searchable_eval_payload({"text": "ok", "source": "a"}) is True
    assert _is_searchable_eval_payload({
        "text": "ok", "archived": False, "is_current": True,
    }) is True
    assert _is_searchable_eval_payload({"text": "ok", "archived": True}) is False
    assert _is_searchable_eval_payload({"text": "ok", "is_current": False}) is False
    assert _is_searchable_eval_payload({"text": "   ", "archived": False}) is False
    assert _is_searchable_eval_payload({
        "text": "cfg", "chunk_type": "__config__",
    }) is False


def test_generate_eval_cases_skips_archived_points():
    """POST generate only feeds non-archived current chunks to the LLM sampler."""
    from src.main import app

    client = TestClient(app)

    points = [
        {
            "id": "arch-1",
            "payload": {
                "source": "__file__:old",
                "text": "Power electricity 246498 TOTAL OPEX",
                "archived": True,
                "is_current": False,
                "chunk_type": "normal",
            },
        },
        {
            "id": "cur-1",
            "payload": {
                "source": "__file__:new",
                "text": "Screening table water tariff current version content",
                "archived": False,
                "is_current": True,
                "chunk_type": "normal",
                "summary": "quick filter",
            },
        },
    ]

    # Filter at scroll (as production does) + leftover post-filter safety
    def fake_scroll(**kwargs):
        fl = kwargs.get("scroll_filter")
        if fl is not None:
            kept = []
            for p in points:
                pl = p["payload"]
                if pl.get("archived") is True:
                    continue
                if pl.get("is_current") is False:
                    continue
                kept.append(p)
            return kept, None
        return points, None

    llm_out = (
        '[{"query": "What is the water tariff in the screening table?", '
        '"target_chunk_index": 1}]'
    )

    with patch("src.api.routes.recall.services") as mock_svc, \
         patch("src.api.routes.recall._save_cases") as mock_save, \
         patch("src.api.routes.recall._load_cases", return_value=[]), \
         patch("src.api.routes.recall._is_specific_query", return_value=True):
        mock_svc.db.collection_exists.return_value = True
        mock_svc.db.scroll_points.side_effect = fake_scroll
        mock_svc.llm.generate.return_value = llm_out

        resp = client.post("/api/recall/eval/col_test/cases/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][1]
        assert len(saved) == 1
        assert saved[0]["target_chunk_id"] == "cur-1"
        assert saved[0]["target_source"] == "__file__:new"
        # LLM prompt must not include archived OPEX text
        prompt = mock_svc.llm.generate.call_args[0][0]
        assert "246498" not in prompt
        assert "Screening table" in prompt or "water tariff" in prompt.lower() or "current version" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

