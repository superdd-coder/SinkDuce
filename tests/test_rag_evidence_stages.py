"""Retrieval candidates and generation evidence must be independently observable."""

from unittest.mock import MagicMock

from src.rag.agentic_query import AgenticQueryService
from src.rag.aggregator import Aggregator
from src.rag.direct_query import DirectQueryModule
from src.rag.retriever import RetrievedChunk
from src.rag.variant_fetcher import VariantFetcherResult


def test_direct_preserves_candidates_before_final_cap():
    retriever = MagicMock()
    candidates = [RetrievedChunk("gold", .9, {"id": "a"}),
                  RetrievedChunk("other gold", .8, {"id": "b"})]
    retriever.retrieve.return_value = candidates
    db = MagicMock()
    db.get_collection_config.return_value = {"chunk_mode": "normal"}
    result = DirectQueryModule(retriever, db).retrieve(
        "query", ["col"], top_k=2, rerank_enabled=True, rerank_top_k=1)
    assert [c.metadata["id"] for c in result.candidate_chunks] == ["a", "b"]
    assert [c.metadata["id"] for c in result.chunks] == ["a"]
    assert "other gold" not in result.context


def test_agent_exposes_dropped_candidates_separately_from_context_evidence():
    gold = RetrievedChunk("gold evidence", .9, {"id": "gold", "collection": "col"})
    vf = MagicMock()
    vf.run.return_value = VariantFetcherResult(
        chunks=[], retrieved_chunks=[gold], retained_info="Nothing found", gap_analysis="Missing fact")
    service = AgenticQueryService(MagicMock(), vf, MagicMock(), MagicMock(),
                                  Aggregator(MagicMock()), MagicMock())
    result = service.run("fact?", collections=["col"], decompose=False, generate_answer=False)
    assert result.candidate_chunks == [gold]
    assert result.retained_chunks == []
    assert "gold evidence" not in result.context


def test_direct_exposes_collection_errors_without_losing_other_results():
    retriever = MagicMock()
    retriever.retrieve.side_effect = RuntimeError("embedding unavailable")
    db = MagicMock()
    db.get_collection_config.return_value = {}
    result = DirectQueryModule(retriever, db).retrieve("query", ["col"])
    assert result.chunks == []
    assert result.errors and result.errors[0]["stage"] == "retrieve"
    assert result.errors[0]["collection"] == "col"


def test_hybrid_fallback_trace_records_the_actual_dense_mode():
    from src.rag.retriever import Retriever

    db = MagicMock()
    db.get_collection_config.return_value = {}
    db.search.return_value = [{"id": "a", "score": .9, "payload": {"text": "gold", "source": "a.txt"}}]
    embedding = MagicMock()
    embedding.embed_query.return_value = [1.0, 1.0]
    result = DirectQueryModule(Retriever(db, embedding), db).retrieve(
        "gold", ["col"], search_mode="hybrid", embedding_overrides={})
    assert result.chunks[0].text == "gold"
    assert result.trace[0]["kind"] == "retrieval_fallback"
    assert result.trace[0]["mode"] == "dense"
    assert result.trace[0]["collection"] == "col"


def test_failed_atomic_query_is_reported_as_an_error():
    vf = MagicMock()
    vf.run.side_effect = RuntimeError("provider failed")
    service = AgenticQueryService(MagicMock(), vf, MagicMock(), MagicMock(),
                                  Aggregator(MagicMock()), MagicMock())
    result = service.run("fact", collections=["col"], decompose=False, generate_answer=False)
    assert result.errors[0]["stage"] == "atomic_query"
    assert result.errors[0]["error"] == "RuntimeError"


def test_variant_retrieval_failure_is_visible_to_evaluation():
    from src.rag.variant_fetcher import VariantFetcher

    direct = MagicMock()
    direct.retrieve.side_effect = RuntimeError("provider failed")
    result = VariantFetcher(direct, MagicMock()).run("fact", ["col"], variant_count=0)
    assert result.errors[0]["stage"] == "variant_retrieve"
