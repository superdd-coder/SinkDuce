"""Decomposer v2 — group-first decomposition."""

import pytest
from unittest.mock import MagicMock

from src.rag.decomposer import Decomposer, AtomicQuery


def _make_llm(json_output):
    import json
    llm = MagicMock()
    llm.generate.return_value = json.dumps(json_output, ensure_ascii=False)
    return llm


def _catalog(name, eid=None, definition="", coverage="", tags=None):
    from src.rag.catalog import CatalogEntry
    return CatalogEntry(
        name=name, id=eid or name,
        definition=definition, coverage=coverage, tags=tags or [],
    )


class TestDecompose:
    """Group-first decompose: raw_query + catalog → list[AtomicQuery]"""

    def test_single_topic_one_aq(self):
        llm = _make_llm([
            {"task": "", "queries": [
                {"query": "Alpha project 2025 payment records", "target_collections": ["col_a"]},
            ]},
        ])
        d = Decomposer(llm)
        aqs = d.decompose("What is the Alpha project payment amount?", [
            _catalog("col_a", definition="Alpha project financial data"),
        ])
        assert len(aqs) == 1
        assert aqs[0].task == ""

    def test_single_topic_multi_aq(self):
        """Single topic with multiple aspects → multiple AQs, one group"""
        llm = _make_llm([
            {"task": "", "queries": [
                {"query": "Alpha plant key risks", "target_collections": ["col_plant"]},
                {"query": "Alpha plant mitigation strategies", "target_collections": ["col_plant"]},
            ]},
        ])
        d = Decomposer(llm)
        aqs = d.decompose("What are the key risks and mitigation strategies for the Alpha plant?", [
            _catalog("col_plant", definition="Plant project documentation"),
        ])
        assert len(aqs) >= 1

    def test_multi_topic_grouped(self):
        """Unrelated topics → separate groups"""
        llm = _make_llm([
            {"task": "finance comparison", "queries": [
                {"query": "Project A payment records", "target_collections": ["col_a"]},
                {"query": "Project B payment records", "target_collections": ["col_b"]},
            ]},
            {"task": "sales trends", "queries": [
                {"query": "Company X 2025 Q4 sales figures", "target_collections": ["col_auto"]},
            ]},
        ])
        d = Decomposer(llm)
        aqs = d.decompose("Compare Project A and B payments, plus Company X Q4 sales", [
            _catalog("col_a", definition="Project A data"),
            _catalog("col_b", definition="Project B data"),
            _catalog("col_auto", definition="Automotive industry data"),
        ])
        assert len(aqs) == 3
        tasks = {aq.task for aq in aqs}
        assert "finance comparison" in tasks
        assert "sales trends" in tasks

    def test_non_retrieval_returns_empty(self):
        llm = _make_llm([])
        d = Decomposer(llm)
        aqs = d.decompose("draft an email to David", [])
        assert aqs == []

    def test_collections_filtered(self):
        llm = _make_llm([
            {"task": "", "queries": [
                {"query": "test query", "target_collections": ["valid_col", "fake_col"]},
            ]},
        ])
        d = Decomposer(llm)
        aqs = d.decompose("test", [_catalog("valid_col", definition="data")])
        assert "valid_col" in aqs[0].target_collections
        assert "fake_col" not in aqs[0].target_collections

    def test_empty_catalog(self):
        llm = _make_llm([
            {"task": "", "queries": [
                {"query": "test", "target_collections": []},
            ]},
        ])
        d = Decomposer(llm)
        aqs = d.decompose("test", [])
        assert len(aqs) >= 1
        assert aqs[0].target_collections == []

    def test_llm_malformed_fallback(self):
        llm = MagicMock()
        llm.generate.return_value = "not json @@@"
        d = Decomposer(llm)
        aqs = d.decompose("test query", [])
        assert len(aqs) == 1

    def test_empty_raw_query(self):
        d = Decomposer(MagicMock())
        aqs = d.decompose("", [])
        assert aqs == []

    def test_target_collections_can_be_multiple(self):
        llm = _make_llm([
            {"task": "", "queries": [
                {"query": "revenue data", "target_collections": ["col_a", "col_b"]},
            ]},
        ])
        d = Decomposer(llm)
        aqs = d.decompose("show me all revenue data", [
            _catalog("col_a", definition="Project A"),
            _catalog("col_b", definition="Project B"),
        ])
        assert len(aqs[0].target_collections) == 2

    def test_flat_format_backward_compat(self):
        """Old flat format still works"""
        llm = _make_llm([
            {"query": "test query", "target_collections": ["col_a"], "task": "t", "task_query": "Test query"},
        ])
        d = Decomposer(llm)
        aqs = d.decompose("test", [_catalog("col_a", definition="data")])
        assert len(aqs) == 1
