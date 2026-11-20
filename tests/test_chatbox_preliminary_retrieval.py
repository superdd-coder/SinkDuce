"""A chat turn performs at most one preliminary search, including streaming."""
import asyncio
import json
from unittest.mock import Mock, patch

from src.chatbox.agent import ChatboxAgent
from src.config import AppConfig
from tests.test_chatbox_agent import (
    store, mock_llm, mock_agentic, _fake_llm_response, _fake_stream, _tc_delta,
)


def test_sync_turn_passes_original_question_and_seeds_only_first_decomposed_search(store, mock_llm, mock_agentic):
    tool = lambda cid, query: {"id": cid, "type": "function", "function": {
        "name": "search_knowledge_base", "arguments": json.dumps({"raw_query": query}),
    }}
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_llm_response(tool_calls=[tool("first", "annual output")]),
        _fake_llm_response(tool_calls=[tool("second", "permit status")]),
        _fake_llm_response(content="answer"),
    ]
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    session = store.create_session(title="preliminary search")
    with patch("src.config.get_config", return_value=AppConfig()):
        agent.chat(session.id, "Compare output and permits", mode="agentic")
    calls = mock_agentic.run.call_args_list
    assert len(calls) == 2
    assert [c.kwargs.get("preliminary_retrieval") for c in calls] == [True, False]
    assert all(c.kwargs.get("original_user_question") == "Compare output and permits" for c in calls)
    assert [c.args[0] for c in calls] == ["annual output", "permit status"]


def test_stream_turn_passes_original_question_and_seeds_only_first_decomposed_search(store, mock_llm, mock_agentic):
    def tool_stream(cid, query):
        return _fake_stream([
            ("tool_calls", {"tool_calls": [
                _tc_delta(0, cid, "search_knowledge_base", json.dumps({"raw_query": query})),
            ]}),
        ])
    mock_llm._client.chat.completions.create.side_effect = [
        tool_stream("first", "annual output"), tool_stream("second", "permit status"),
        _fake_stream([("stop", {"content": "answer"})]),
    ]
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    session = store.create_session(title="stream preliminary search")

    async def collect():
        with patch("src.config.get_config", return_value=AppConfig()):
            return [e async for e in agent.chat_stream(session.id, "Compare output and permits", mode="agentic")]

    asyncio.run(collect())
    calls = mock_agentic.run.call_args_list
    assert len(calls) == 2
    assert [c.kwargs.get("preliminary_retrieval") for c in calls] == [True, False]
    assert all(c.kwargs.get("original_user_question") == "Compare output and permits" for c in calls)


def test_search_model_details_reach_tool_event_completed_answer_and_history(store, mock_llm, mock_agentic, monkeypatch):
    from src.services import services

    model = {"collection": "alpha", "model": "vector-a", "provider_id": "model-a",
             "provider_name": "Alpha connection", "dimensions": 4, "mode": "dense"}
    event = {**model, "kind": "retrieval", "query": "internal search variant"}
    mock_agentic.run.return_value.trace = [event, event, dict(event, collection="not-selected")]
    db = Mock()
    db.list_collections.return_value = ["alpha", "offline"]
    config = AppConfig()
    monkeypatch.setattr(services, "db", db)
    monkeypatch.setattr(services, "config", config)
    monkeypatch.setattr("src.rag.collection_utils.get_collection_embedding_config", lambda _settings, cid: cid == "alpha" or None)
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_stream([("tool_calls", {"tool_calls": [
            _tc_delta(0, "lookup", "search_knowledge_base", json.dumps({"raw_query": "fixture search"})),
        ]})]), _fake_stream([("stop", {"content": "Answer"})]),
    ]
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    session = store.create_session(title="Fixture search")
    async def collect():
        with patch("src.config.get_config", return_value=config):
            return [e async for e in agent.chat_stream(session.id, "fixture search", mode="agentic")]
    events = asyncio.run(collect())
    tool = next(e for e in events if e["type"] == "tool_result")
    done = next(e for e in events if e["type"] == "done")
    history = store.get_messages(session.id)[-1].metadata
    for snapshot in (tool, done, history):
        assert snapshot["embedding_searches"] == [model]
        assert snapshot["skipped_collections"] == ["offline"]
