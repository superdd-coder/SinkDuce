"""Context-sensitive relevance decisions must preserve original passages."""

import json
from copy import deepcopy

from src.rag.agent_nodes import node_combined_grade
from src.rag.agent_state import AgentState
from src.rag.reranker import Reranker
from src.rag.retriever import RetrievedChunk


def test_reranker_disambiguates_identical_passages_using_ingest_context():
    class ContextSensitiveRanking:
        def rerank(self, query, documents, top_k=5):
            scores = [
                (i, 0.95 if "annual output for entity A" in text else 0.1)
                for i, text in enumerate(documents)
            ]
            return sorted(scores, key=lambda item: item[1], reverse=True)[:top_k]

    unrelated = RetrievedChunk(
        "Annual output: 120", 0.9,
        {"id": "other", "context": "annual output for entity B"},
    )
    correct = RetrievedChunk(
        "Annual output: 120", 0.8,
        {"id": "correct", "context": "annual output for entity A"},
    )
    original_metadata = deepcopy(correct.metadata)

    result = Reranker(ContextSensitiveRanking()).rerank(
        "entity A annual output", [unrelated, correct], top_k=1,
    )

    assert result == [correct]
    assert result[0] is correct
    assert correct.text == "Annual output: 120"
    assert correct.metadata == original_metadata


def test_agent_grade_keeps_passage_whose_relevance_depends_on_ingest_context():
    class ContextSensitiveGrader:
        def generate(self, prompt, **kwargs):
            usable = "Context: annual output for entity A\nAnnual output: 120" in prompt
            return json.dumps({
                "relevant_indices": [0] if usable else [],
                "retained_info": "output 120" if usable else "",
                "gap_analysis": "" if usable else "entity identity missing",
            })

    chunk = RetrievedChunk(
        "Annual output: 120", 0.8,
        {
            "id": "evidence", "source": "opaque-source", "collection": "test",
            "context": "annual output for entity A",
        },
    )
    original_metadata = deepcopy(chunk.metadata)
    state = AgentState(original_query="entity A annual output", collections=["test"])

    node_combined_grade(state, [chunk], llm=ContextSensitiveGrader())

    assert state.retained_chunks == [chunk]
    assert state.retained_chunks[0] is chunk
    assert not state.current_gap_analysis
    assert chunk.text == "Annual output: 120"
    assert chunk.metadata == original_metadata


def test_plain_passages_keep_original_reranker_input():
    class CaptureRanking:
        def rerank(self, query, documents, top_k=5):
            assert documents == ["plain evidence", "another passage"]
            return [(0, 0.9), (1, 0.8)]

    chunks = [
        RetrievedChunk("plain evidence", 0.8, {"id": "plain"}),
        RetrievedChunk("another passage", 0.7, {"id": "blank-context", "context": " \n "}),
    ]
    assert Reranker(CaptureRanking()).rerank("fact", chunks) == chunks


def test_empty_body_filter_preserves_reranker_result_mapping_with_context():
    class CaptureRanking:
        def rerank(self, query, documents, top_k=5):
            assert documents == ["first passage", "last passage"]
            return [(1, 0.99)]

    empty = RetrievedChunk(" \n ", 0.9, {"id": "empty", "context": "context only"})
    first = RetrievedChunk("first passage", 0.8, {"id": "first"})
    last = RetrievedChunk("last passage", 0.7, {"id": "last"})

    result = Reranker(CaptureRanking()).rerank("fact", [empty, first, last], top_k=1)

    assert result == [last]
    assert result[0] is last
    assert last.score == 0.99
    assert empty.text == " \n "
    assert empty.metadata == {"id": "empty", "context": "context only"}


def test_context_only_empty_passages_do_not_call_reranker():
    class NeverCalledRanking:
        def rerank(self, *args, **kwargs):
            raise AssertionError("Empty passages must be filtered before the provider call")

    chunks = [
        RetrievedChunk("", 0.9, {"context": "context only"}),
        RetrievedChunk(" \n ", 0.8, {"context": "context only"}),
    ]
    assert Reranker(NeverCalledRanking()).rerank("fact", chunks) == []


def test_agent_grade_plain_passage_input_remains_compatible():
    class CaptureGrader:
        def generate(self, prompt, **kwargs):
            assert "[0] (database: test, source: source) plain evidence" in prompt
            assert "Context:" not in prompt
            return json.dumps({
                "relevant_indices": [0], "retained_info": "fact", "gap_analysis": "",
            })

    chunk = RetrievedChunk(
        "plain evidence", 0.8, {"id": "plain", "source": "source", "collection": "test"},
    )
    state = AgentState(original_query="fact", collections=["test"])

    node_combined_grade(state, [chunk], llm=CaptureGrader())

    assert state.retained_chunks == [chunk]
