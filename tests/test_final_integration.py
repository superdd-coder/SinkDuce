"""Step 9 v2: Final integration — end-to-end pipeline with all mocks."""

import pytest
from unittest.mock import MagicMock

from src.rag.agentic_query import AgenticQueryService
from src.rag.decomposer import AtomicQuery
from src.rag.variant_fetcher import VariantFetcherResult
from src.rag.retriever import RetrievedChunk


def _vf_result():
    c = [RetrievedChunk(text="text", score=0.9, metadata={"id": "c1"})]
    return VariantFetcherResult(chunks=c, retained_info="info", gap_analysis="")


class TestIntegration:
    def test_end_to_end_simple(self):
        dec = MagicMock()
        dec.decompose.return_value = [
            AtomicQuery(query="Alpha project payment", target_collections=["col_a"]),
        ]
        vf = MagicMock()
        vf.run.return_value = _vf_result()
        svc = AgenticQueryService(
            direct_module=MagicMock(), variant_fetcher=vf,
            catalog=MagicMock(), decomposer=dec,
            aggregator=MagicMock(), llm=MagicMock(),
        )
        result = svc.run("Alpha project payment", generate_answer=True)
        assert result.answer and "<search_results>" in result.answer

    def test_no_answer(self):
        dec = MagicMock()
        dec.decompose.return_value = [AtomicQuery(query="q")]
        vf = MagicMock()
        vf.run.return_value = _vf_result()
        svc = AgenticQueryService(
            direct_module=MagicMock(), variant_fetcher=vf,
            catalog=MagicMock(), decomposer=dec,
            aggregator=MagicMock(), llm=MagicMock(),
        )
        result = svc.run("test", generate_answer=False)
        assert result.answer is None

    def test_decompose_empty(self):
        dec = MagicMock()
        dec.decompose.return_value = []
        svc = AgenticQueryService(
            direct_module=MagicMock(), variant_fetcher=MagicMock(),
            catalog=MagicMock(), decomposer=dec,
            aggregator=MagicMock(), llm=MagicMock(),
        )
        result = svc.run("draft email", generate_answer=True)
        assert result.answer is None
