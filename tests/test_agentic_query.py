"""AgenticQueryService v2 — one-layer decompose + group-aware aggregate."""

import pytest
from unittest.mock import MagicMock

from src.rag.agentic_query import AgenticQueryService
from src.rag.decomposer import AtomicQuery


def _make_service(dec=None, rl=None, agg=None, cat=None, dm=None, llm=None):
    dec = dec or MagicMock()
    rl = rl or MagicMock()
    agg = agg or MagicMock()
    cat = cat or MagicMock()
    dm = dm or MagicMock()
    llm = llm or MagicMock()
    llm.generate.return_value = "answer"
    return AgenticQueryService(
        direct_module=dm, rewrite_loop=rl, catalog=cat,
        decomposer=dec, aggregator=agg, llm=llm,
    )


def _rl_result():
    from src.rag.rewrite_loop import RewriteLoopResult
    from src.rag.retriever import RetrievedChunk
    c = [RetrievedChunk(text="text", score=0.9, metadata={"id": "c1", "source": "doc.md"})]
    return RewriteLoopResult(chunks=c, retained_info="info", is_sufficient=True)


class TestAgenticQuery:
    def test_simple_query(self):
        """Full agentic path with single AQ → direct answer generation (not aggregator)."""
        dec = MagicMock()
        dec.decompose.return_value = [AtomicQuery(query="risks of X", target_collections=["col_a"])]
        rl = MagicMock()
        rl.run.return_value = _rl_result()
        svc = _make_service(dec=dec, rl=rl)
        svc.llm.generate.return_value = "direct answer"
        result = svc.run("what are the risks", generate_answer=True)
        assert result.answer == "direct answer"
        assert dec.decompose.called
        svc.llm.generate.assert_called_once()

    def test_non_retrieval_returns_empty(self):
        dec = MagicMock()
        dec.decompose.return_value = []
        svc = _make_service(dec=dec)
        result = svc.run("draft an email", generate_answer=True)
        assert result.answer is None
        assert result.all_chunks == []

    def test_multi_aq_parallel(self):
        dec = MagicMock()
        dec.decompose.return_value = [
            AtomicQuery(query="q1", task="finance"),
            AtomicQuery(query="q2", task="finance"),
            AtomicQuery(query="q3", task="sales"),
        ]
        rl = MagicMock()
        rl.run.return_value = _rl_result()
        agg = MagicMock()
        agg.aggregate.return_value = "merged"
        svc = _make_service(dec=dec, rl=rl, agg=agg)
        result = svc.run("complex query", generate_answer=True)
        assert rl.run.call_count == 3
        assert result.answer

    def test_decompose_failure_fallback(self):
        dec = MagicMock()
        dec.decompose.side_effect = RuntimeError("fail")
        rl = MagicMock()
        rl.run.return_value = _rl_result()
        agg = MagicMock()
        agg.aggregate.return_value = "fallback"
        svc = _make_service(dec=dec, rl=rl, agg=agg)
        result = svc.run("test", generate_answer=True)
        assert result is not None

    def test_no_answer_mode(self):
        dec = MagicMock()
        dec.decompose.return_value = [AtomicQuery(query="q")]
        rl = MagicMock()
        rl.run.return_value = _rl_result()
        svc = _make_service(dec=dec, rl=rl)
        result = svc.run("test", generate_answer=False)
        assert result.answer is None
        assert len(result.all_chunks) == 1

    def test_max_parallel_not_exceeded(self):
        dec = MagicMock()
        n = 20
        dec.decompose.return_value = [AtomicQuery(query=f"q{i}") for i in range(n)]
        rl = MagicMock()
        rl.run.return_value = _rl_result()
        agg = MagicMock()
        agg.aggregate.return_value = "ok"
        svc = _make_service(dec=dec, rl=rl, agg=agg)
        svc._max_parallel_queries = 8
        result = svc.run("many", generate_answer=True)
        assert rl.run.call_count == n

    def test_empty_query(self):
        svc = _make_service()
        result = svc.run("", generate_answer=True)
        assert result.answer is None
