"""Grade failures remain distinct from valid empty evidence and completion."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.rag.agent_nodes import node_combined_grade
from src.rag.agent_state import AgentState
from src.rag.aggregator import Aggregator
from src.rag.retriever import RetrievedChunk
from src.rag.variant_fetcher import VariantFetcher

BODY = "Entity A produced 120 units in 2024."
VALID = {"relevant_indices": [0], "retained_info": BODY, "gap_analysis": ""}


class Replies:
    supports_vision = False

    def __init__(self, *responses):
        self.responses = responses
        self.calls = []

    def generate(self, prompt, **kwargs):
        response = self.responses[min(len(self.calls), len(self.responses) - 1)]
        self.calls.append((deepcopy(prompt), kwargs))
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response)


def candidates():
    return [RetrievedChunk(BODY, .9, {"id": "fact", "source": "report", "collection": "col"})]


@pytest.mark.parametrize("root", ["", "Compare Entity A's output across two years."])
@pytest.mark.parametrize("invalid", [
    {}, [], None,
    {"relevant_indices": [0], "gap_analysis": ""},
    {"relevant_indices": [0], "retained_info": BODY},
    {"relevant_indices": None, "retained_info": BODY, "gap_analysis": ""},
    {"relevant_indices": "0", "retained_info": BODY, "gap_analysis": ""},
    {"relevant_indices": [0], "retained_info": None, "gap_analysis": ""},
    {"relevant_indices": [0], "retained_info": BODY, "gap_analysis": None},
])
def test_invalid_grade_structure_retries_instead_of_claiming_completion(root, invalid):
    model = Replies(invalid, VALID)
    state = AgentState(original_query="Entity A's 2024 output", original_user_question=root)
    batch = candidates()
    node_combined_grade(state, batch, llm=model)
    assert len(model.calls) == 2
    assert state.retained_chunks == batch
    assert not state.current_gap_analysis


def test_exhausted_structure_retries_retain_unverified_sources_with_failure():
    model = Replies({})
    state = AgentState(original_query="output")
    batch = candidates()
    node_combined_grade(state, batch, llm=model)
    assert len(model.calls) == 3
    assert state.retained_chunks == batch
    assert state.current_gap_analysis
    assert state.grade_error


def test_empty_selection_cannot_claim_completion_without_existing_evidence():
    state = AgentState(original_query="output")
    node_combined_grade(state, candidates(), llm=Replies({
        "relevant_indices": [], "retained_info": "", "gap_analysis": "",
    }))
    assert not state.retained_chunks
    assert not state.retained_info
    assert state.current_gap_analysis


def test_invalid_source_indices_never_promote_unselected_sources_or_claim_completion():
    state = AgentState(original_query="output")
    node_combined_grade(state, candidates(), llm=Replies({
        "relevant_indices": [True, 0.8, "0", -1, 1],
        "retained_info": BODY, "gap_analysis": "",
    }))
    assert not state.retained_chunks
    assert not state.retained_info
    assert state.current_gap_analysis
    assert state.grade_error


def test_valid_empty_result_and_legacy_three_fields_keep_single_call_compatibility():
    for reply in [VALID, {"relevant_indices": [], "retained_info": "", "gap_analysis": "Missing output"}]:
        state = AgentState(original_query="output")
        model = Replies(reply)
        node_combined_grade(state, candidates(), llm=model)
        assert len(model.calls) == 1
        assert not getattr(state, "grade_error", "")
        assert bool(state.retained_chunks) == bool(reply["relevant_indices"])


def test_mixed_indices_keep_valid_sources_without_promoting_invalid_ones():
    batch = candidates() + [RetrievedChunk("An unrelated fact.", .8, {"id": "noise"})]
    state = AgentState(original_query="output")
    model = Replies({"relevant_indices": [0, True, .8, "1", -1, 99, 0],
                     "retained_info": BODY, "gap_analysis": ""})
    node_combined_grade(state, batch, llm=model)
    assert state.retained_chunks == batch[:1]
    assert state.grade_error
    assert state.current_gap_analysis
    assert len(model.calls) == 1


def test_prior_evidence_can_complete_without_a_new_selection_and_failure_is_reset():
    state = AgentState(original_query="output", retained_chunks=candidates(),
                       retained_info=BODY, grade_error="OldFailure")
    node_combined_grade(state, candidates(), llm=Replies({
        "relevant_indices": [], "retained_info": "", "gap_analysis": "",
    }))
    assert state.retained_chunks == candidates()
    assert not state.current_gap_analysis
    assert not state.grade_error


@pytest.mark.parametrize("evidence", [None, [], "bad", [{"index": 0, "quote": "fabricated 900"}]])
def test_optional_quotes_do_not_retry_or_certify_fabricated_findings(evidence):
    state = AgentState(original_query="output")
    model = Replies({"relevant_indices": [0], "retained_info": "Output was 900.",
                     "gap_analysis": "", "evidence": evidence})
    node_combined_grade(state, candidates(), llm=model)
    assert len(model.calls) == 1
    assert state.retained_chunks == candidates()
    assert "900" not in state.retained_info
    assert BODY in state.retained_info
    assert not state.grade_error


@pytest.mark.parametrize("response", ["not JSON", {}, RuntimeError("provider failed")])
def test_grade_failure_reaches_pipeline_errors_and_gap_context(response):
    from src.rag.agentic_query import AgenticQueryService

    batch = candidates()
    direct = SimpleNamespace(retrieve=lambda *args, **kwargs: SimpleNamespace(chunks=batch, trace=[], errors=[]))
    model = Replies(response)
    class SingleQueryFetcher(VariantFetcher):
        def run(self, *args, **kwargs):
            return super().run(*args, variant_count=0, **kwargs)

    fetcher = SingleQueryFetcher(direct, model)
    catalog = SimpleNamespace(get_catalog=lambda *args, **kwargs: [])
    router = SimpleNamespace(route_collections=lambda *args, **kwargs: ["col"])
    service = AgenticQueryService(direct, fetcher, catalog, router, Aggregator(model), model)
    result = service.run("output", collections=["col"], decompose=False,
                         preliminary_retrieval=False, generate_answer=False)
    assert result.retained_chunks == batch
    assert any(event["stage"] == "grade" for event in result.errors)
    assert any(event["kind"] == "grading_fallback" for event in result.trace)
    assert "<gaps>" in result.context


@pytest.mark.parametrize("response,limit,attempts,error", [
    ({}, 3, 3, "InvalidGradeResponse"),
    ("not JSON", 3, 3, "InvalidGradeResponse"),
    (RuntimeError("private provider message"), 10, 1, "RuntimeError"),
    (ValueError("private provider message"), 10, 1, "ValueError"),
])
def test_grade_failure_categories_keep_existing_limits_and_safe_diagnostics(response, limit, attempts, error):
    batch = [RetrievedChunk(f"Primary passage {i}", 1 - i / 20, {"id": str(i)}) for i in range(11)]
    direct = SimpleNamespace(retrieve=lambda *args, **kwargs: SimpleNamespace(chunks=batch, trace=[], errors=[]))
    model = Replies(response)
    result = VariantFetcher(direct, model).run("output", ["col"], variant_count=0)
    assert result.chunks == batch[:limit]
    assert result.retrieved_chunks == batch
    assert len(model.calls) == attempts
    assert result.gap_analysis
    assert result.errors == [{"stage": "grade", "query": "output", "error": error}]
    assert len(result.trace) == 1
    assert result.trace[0]["kind"] == "grading_fallback"
    assert result.trace[0]["error"] == error
    assert "private provider message" not in json.dumps(result.errors + result.trace)


def test_image_only_empty_quotes_preserve_original_chunk_without_fabricated_findings():
    body = ":::image\nimage_id: " + "a" * 32 + "\nocr_text: 120\ndescription: 900\n:::\n"
    chunk = RetrievedChunk(body, .9, {"id": "figure", "source": "chart"})
    state = AgentState(original_query="Read the original chart")
    node_combined_grade(state, [chunk], llm=Replies({
        "relevant_indices": [0], "retained_info": "The output was 900.",
        "gap_analysis": "Original chart inspection needed", "evidence": [],
    }))
    assert state.retained_chunks == [chunk]
    assert "900" not in state.retained_info
    assert state.current_gap_analysis
