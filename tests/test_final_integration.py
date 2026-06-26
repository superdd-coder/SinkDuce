"""Step 9 v2: Final integration — end-to-end pipeline with all mocks."""

import pytest
from unittest.mock import MagicMock

from src.rag.agentic_query import AgenticQueryService
from src.rag.decomposer import AtomicQuery
from src.rag.rewrite_loop import RewriteLoopResult
from src.rag.retriever import RetrievedChunk


def _rl_result():
    c = [RetrievedChunk(text="text", score=0.9, metadata={"id": "c1"})]
    return RewriteLoopResult(chunks=c, retained_info="info", is_sufficient=True)


class TestIntegration:
    def test_end_to_end_simple(self):
        dec = MagicMock()
        dec.decompose.return_value = [
            AtomicQuery(query="Alpha project payment", target_collections=["col_a"]),
        ]
        rl = MagicMock()
        rl.run.return_value = _rl_result()
        agg = MagicMock()
        agg.aggregate.return_value = "# Result\n\n$1,000,000"
        svc = AgenticQueryService(
            direct_module=MagicMock(), rewrite_loop=rl,
            catalog=MagicMock(), decomposer=dec, aggregator=agg,
            llm=MagicMock(),
        )
        result = svc.run("Alpha project payment", generate_answer=True)
        assert result.answer == "# Result\n\n$1,000,000"

    def test_no_answer(self):
        dec = MagicMock()
        dec.decompose.return_value = [AtomicQuery(query="q")]
        rl = MagicMock()
        rl.run.return_value = _rl_result()
        svc = AgenticQueryService(
            direct_module=MagicMock(), rewrite_loop=rl,
            catalog=MagicMock(), decomposer=dec,
            aggregator=MagicMock(), llm=MagicMock(),
        )
        result = svc.run("test", generate_answer=False)
        assert result.answer is None

    def test_decompose_empty(self):
        dec = MagicMock()
        dec.decompose.return_value = []
        svc = AgenticQueryService(
            direct_module=MagicMock(), rewrite_loop=MagicMock(),
            catalog=MagicMock(), decomposer=dec,
            aggregator=MagicMock(), llm=MagicMock(),
        )
        result = svc.run("draft email", generate_answer=True)
        assert result.answer is None
