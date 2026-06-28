"""Aggregator v3 — task-query aware aggregation from raw AQ materials."""

import pytest
from unittest.mock import MagicMock

from src.rag.aggregator import Aggregator, SubQueryResult


def _make_sqr(query="q", retained_info="info", task="", task_query="", sufficient=True):
    ck = MagicMock()
    ck.text = "chunk text"
    ck.score = 0.9
    ck.metadata = {"id": "c1", "source": "doc.md", "chunk_index": 1}
    return SubQueryResult(
        query=query, retained_chunks=[ck], retained_info=retained_info,
        gap_analysis="" if sufficient else "missing data",
        task=task, task_query=task_query,
    )


def _make_llm(output="synthesized answer"):
    llm = MagicMock()
    llm.generate.return_value = output
    return llm


class TestAggregate:
    def test_single_aq_llm_aggregate(self):
        """Single AQ → LLM aggregate called"""
        llm = _make_llm("single")
        agg = Aggregator(llm)
        result = agg.aggregate([_make_sqr()])
        assert llm.generate.called
        assert result == "single"

    def test_ungrouped_multiple(self):
        llm = _make_llm("combined")
        agg = Aggregator(llm)
        results = [_make_sqr(query="q1"), _make_sqr(query="q2")]
        result = agg.aggregate(results)
        assert llm.generate.called
        assert result == "combined"

    def test_grouped_aggregation(self):
        llm = _make_llm()
        agg = Aggregator(llm)
        results = [
            _make_sqr(query="q1", task="finance", task_query="Compare A and B"),
            _make_sqr(query="q2", task="finance", task_query="Compare A and B"),
            _make_sqr(query="q3", task="sales", task_query="Check car sales"),
        ]
        result = agg.aggregate(results)
        assert llm.generate.call_count >= 2  # one per task
        assert result

    def test_task_query_in_prompt(self):
        """Prompts include task_query"""
        llm = _make_llm()
        agg = Aggregator(llm)
        results = [
            _make_sqr(query="q1", task="f", task_query="Find the revenue of Project A"),
            _make_sqr(query="q2", task="f", task_query="Find the revenue of Project A"),
        ]
        agg.aggregate(results)
        prompt = llm.generate.call_args[0][0]
        assert "Find the revenue of Project A" in prompt

    def test_empty_list(self):
        agg = Aggregator(MagicMock())
        assert agg.aggregate([]) == ""

    def test_llm_failure_fallback(self):
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("fail")
        agg = Aggregator(llm)
        results = [_make_sqr(query="q1"), _make_sqr(query="q2")]
        result = agg.aggregate(results)
        assert "q1" in result or "q2" in result

    def test_build_context_called(self):
        """build_context is called for each AQ's chunks"""
        llm = _make_llm()
        agg = Aggregator(llm)
        results = [_make_sqr()]
        agg.aggregate(results)
        prompt = llm.generate.call_args[0][0]
        assert "chunk text" in prompt
        assert "doc.md" in prompt
