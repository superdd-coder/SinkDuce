"""Original-user constraints reach planning and grading without changing search inputs."""

import json
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch, AsyncMock

from tests.test_chatbox_agent import store, mock_llm, mock_agentic, _fake_stream, _tc_delta

from src.rag.agent_state import AgentState
from src.rag.agent_nodes import node_combined_grade
from src.rag.agent_prompts import (
    DECOMPOSE_SYSTEM,
    DECOMPOSE_USER,
    GRADE_COMBINED_SYSTEM,
    GRADE_COMBINED_USER,
    ROUTE_SYSTEM,
)
from src.rag.decomposer import Decomposer
from src.rag.retriever import RetrievedChunk
from src.rag.variant_fetcher import VariantFetcher


ROOT = "Compare Entity A's 2022 and 2023 reports using their 2024 payment forecasts."
NEED = "Entity A payment forecasts for 2024"
AQ = "What is Entity A's 2024 forecast in its 2022 report?"
BODY = "Entity A's 2022 report forecasts a 2024 payment of 120 units."


def llm(value):
    return SimpleNamespace(generate=Mock(return_value=json.dumps(value)))


def passage():
    return RetrievedChunk(BODY, 0.9, {"id": "point-a", "collection": "col-a"})


def test_ordinary_decomposition_receives_literal_user_and_outer_need_separately():
    model = llm([{"task": "Entity A", "task_query": NEED,
                  "queries": [{"query": AQ, "target_collections": [0]}]}])
    result = Decomposer(model).decompose(
        NEED, [{"id": "col-a", "name": "Public records"}], original_user_question=ROOT,
    )
    prompt = model.generate.call_args.args[0]
    assert ROOT in prompt and NEED in prompt
    assert "Original user question" in prompt and "Current information needs" in prompt
    assert result[0].query == AQ


def test_grade_keeps_atomic_query_and_root_as_distinct_inputs():
    model = llm({"relevant_indices": [0], "retained_info": BODY, "gap_analysis": ""})
    state = AgentState(original_query=AQ, original_user_question=ROOT)
    node_combined_grade(state, [passage()], llm=model)
    prompt = model.generate.call_args.args[0]
    assert ROOT in prompt and AQ in prompt and BODY in prompt
    assert "Current sub-question" in prompt
    assert state.original_query == AQ
    assert state.current_gap_analysis == ""


def test_legacy_grade_without_root_uses_identical_prompt_and_system():
    model = llm({"relevant_indices": [0], "retained_info": BODY, "gap_analysis": ""})
    state = AgentState(original_query=AQ)
    node_combined_grade(state, [passage()], llm=model)
    assert model.generate.call_args.kwargs["system"] == GRADE_COMBINED_SYSTEM
    expected = GRADE_COMBINED_USER.format(
        original_query=AQ, previous_knowledge="(none — this is the first search round)",
        previous_gaps="(none)",
        chunks_text=f"[0] (database: col-a, source: unknown) {BODY}",
    )
    assert model.generate.call_args.args[0] == expected


def test_variant_fetcher_passes_root_to_grade_without_changing_retrieval_query():
    model = llm({"relevant_indices": [0], "retained_info": BODY, "gap_analysis": ""})
    direct = SimpleNamespace(retrieve=Mock(return_value=SimpleNamespace(
        chunks=[passage()], trace=[], errors=[],
    )))
    VariantFetcher(direct, model).run(
        AQ, ["col-a"], original_user_question=ROOT, variant_count=0,
        top_k=10, search_mode="hybrid",
    )
    direct.retrieve.assert_called_once_with(AQ, ["col-a"], top_k=10, search_mode="hybrid")
    assert ROOT in model.generate.call_args.args[0]


def test_agentic_ordinary_plan_and_grade_receive_root():
    from tests.test_evidence_planning import service, plan

    svc, model, direct, fetcher = service(plan(queries=[{"query": AQ, "target_collections": [0]}]))
    svc.run(NEED, collections=["col-a"], original_user_question=ROOT,
            preliminary_retrieval=False, generate_answer=False)
    assert ROOT in model.calls[0][0]
    assert fetcher.calls[0][2]["original_user_question"] == ROOT


def test_full_question_fields_are_not_injected_into_embedding_or_search():
    model = llm({"relevant_indices": [], "retained_info": "", "gap_analysis": "No source"})
    direct = SimpleNamespace(retrieve=Mock(return_value=SimpleNamespace(
        chunks=[passage()], trace=[], errors=[],
    )))
    VariantFetcher(direct, model).run(AQ, ["col-a"], original_user_question=ROOT, variant_count=0)
    assert direct.retrieve.call_args.args == (AQ, ["col-a"])
    assert "original_user_question" not in direct.retrieve.call_args.kwargs


def test_legacy_decomposition_without_root_uses_identical_prompt_and_system():
    model = llm([{"task": "Entity A", "queries": [{"query": AQ, "target_collections": [0]}]}])
    decomposer = Decomposer(model)
    catalog = [{"id": "col-a", "name": "Public records"}]
    result = decomposer.decompose(NEED, catalog)
    catalog_text, _ = decomposer._build_catalog_text(catalog)
    assert model.generate.call_args.args[0] == DECOMPOSE_USER.format(
        raw_query=NEED, catalog_text=catalog_text,
    )
    assert model.generate.call_args.kwargs["system"] == DECOMPOSE_SYSTEM
    assert result[0].query == AQ


def test_single_atomic_path_passes_root_without_decomposition():
    from tests.test_evidence_planning import service, plan

    svc, model, direct, fetcher = service(plan())
    svc.run(AQ, collections=["col-a"], original_user_question=ROOT,
            decompose=False, generate_answer=False)
    assert len(model.calls) == 1
    assert model.calls[0][1]["system"] == ROUTE_SYSTEM
    assert fetcher.calls[0][0] == AQ
    assert fetcher.calls[0][2]["original_user_question"] == ROOT


def test_stream_collection_discovery_only_exposes_selected_project(store, mock_llm, mock_agentic):
    from src.chatbox.agent import ChatboxAgent

    mock_llm._client.chat.completions.create.side_effect = [
        _fake_stream([("tool_calls", {"tool_calls": [
            _tc_delta(0, "catalog", "list_collections", "{}"),
        ]})]),
        _fake_stream([("stop", {"content": "answer"})]),
    ]
    session = store.create_session(title="scoped discovery")
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    entries = [{"id": "col-other", "catalog_definition": "Unselected project details"},
               {"id": "col-a", "name": "Selected project"}]

    async def collect():
        with patch("src.chatbox.query_tools._check_tool_allowed", new=AsyncMock(return_value=None)), \
             patch("src.mcp.tools.collections.list_collections", new=AsyncMock(return_value=json.dumps(entries))):
            return [event async for event in agent.chat_stream(
                session.id, "List my selected project", collections=["col-a"], mode="agentic",
            )]

    asyncio.run(collect())
    messages = mock_llm._client.chat.completions.create.call_args.kwargs["messages"]
    results = [m["content"] for m in messages if m["role"] == "tool"]
    assert len(results) == 1
    assert json.loads(results[0]) == [entries[1]]
    assert "Unselected project details" not in json.dumps(messages)


def test_scoped_discovery_filters_before_truncating_long_catalog():
    from src.chatbox.query_tools import execute_structure_tool_async

    entries = [{"id": "col-other", "catalog_definition": "Unselected details " * 5000},
               {"id": "col-a", "name": "Selected project"}]
    with patch("src.chatbox.query_tools._dispatch_structure_async", new=AsyncMock(return_value=json.dumps(entries))):
        result = asyncio.run(execute_structure_tool_async(
            "list_collections", {}, mode="agentic", selected_collections=["col-a"],
        ))
    assert json.loads(result) == [entries[1]]


def test_unscoped_discovery_preserves_all_visible_projects():
    from src.chatbox.query_tools import execute_structure_tool_async

    entries = [{"id": "col-a"}, {"id": "col-b"}]
    with patch("src.chatbox.query_tools._dispatch_structure_async", new=AsyncMock(return_value=json.dumps(entries))):
        result = asyncio.run(execute_structure_tool_async("list_collections", {}, mode="agentic"))
    assert json.loads(result) == entries


def test_scoped_malformed_catalog_fails_closed():
    from src.chatbox.query_tools import execute_structure_tool_async

    with patch("src.chatbox.query_tools._dispatch_structure_async", new=AsyncMock(return_value='{"unexpected": "other project details"}')):
        result = asyncio.run(execute_structure_tool_async(
            "list_collections", {}, mode="agentic", selected_collections=["col-a"],
        ))
    assert "error" in json.loads(result)
    assert "other project details" not in result
