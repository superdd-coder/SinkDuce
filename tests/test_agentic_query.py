"""AgenticQueryService v2 — one-layer decompose + group-aware aggregate."""

import pytest
from unittest.mock import MagicMock

from src.rag.agentic_query import AgenticQueryService
from src.rag.decomposer import AtomicQuery


def _make_service(dec=None, vf=None, agg=None, cat=None, dm=None, llm=None):
    dec = dec or MagicMock()
    vf = vf or MagicMock()
    agg = agg or MagicMock()
    cat = cat or MagicMock()
    dm = dm or MagicMock()
    llm = llm or MagicMock()
    llm.generate.return_value = "answer"
    return AgenticQueryService(
        direct_module=dm, variant_fetcher=vf, catalog=cat,
        decomposer=dec, aggregator=agg, llm=llm,
    )


def _vf_result():
    from src.rag.variant_fetcher import VariantFetcherResult
    from src.rag.retriever import RetrievedChunk
    c = [RetrievedChunk(text="text", score=0.9, metadata={"id": "c1", "source": "doc.md"})]
    return VariantFetcherResult(chunks=c, retained_info="info", gap_analysis="")


class TestAgenticQuery:
    def test_simple_query(self):
        """Full agentic path with single AQ → builds context for Chat LLM."""
        dec = MagicMock()
        dec.decompose.return_value = [AtomicQuery(query="risks of X", target_collections=["col_a"])]
        vf = MagicMock()
        vf.run.return_value = _vf_result()
        svc = _make_service(dec=dec, vf=vf)
        result = svc.run("what are the risks", generate_answer=True)
        assert result.answer and "<search_results>" in result.answer
        assert dec.decompose.called

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
        vf = MagicMock()
        vf.run.return_value = _vf_result()
        agg = MagicMock()
        agg.build_prompt.return_value = "<task>test</task>"
        svc = _make_service(dec=dec, vf=vf, agg=agg)
        result = svc.run("complex query", generate_answer=True)
        assert vf.run.call_count == 3
        assert result.answer and "<search_results>" in result.answer

    def test_decompose_failure_fallback(self):
        dec = MagicMock()
        dec.decompose.side_effect = RuntimeError("fail")
        vf = MagicMock()
        vf.run.return_value = _vf_result()
        svc = _make_service(dec=dec, vf=vf)
        result = svc.run("test", generate_answer=True)
        assert result is not None

    def test_no_answer_mode(self):
        dec = MagicMock()
        dec.decompose.return_value = [AtomicQuery(query="q")]
        vf = MagicMock()
        vf.run.return_value = _vf_result()
        svc = _make_service(dec=dec, vf=vf)
        result = svc.run("test", generate_answer=False)
        assert result.answer is None
        assert len(result.all_chunks) == 1

    def test_max_parallel_not_exceeded(self):
        dec = MagicMock()
        n = 20
        dec.decompose.return_value = [AtomicQuery(query=f"q{i}") for i in range(n)]
        vf = MagicMock()
        vf.run.return_value = _vf_result()
        svc = _make_service(dec=dec, vf=vf)
        svc._max_parallel_queries = 8
        result = svc.run("many", generate_answer=True)
        assert vf.run.call_count == n

    def test_empty_query(self):
        svc = _make_service()
        result = svc.run("", generate_answer=True)
        assert result.answer is None
